//! The weapon props on the GPU.
//!
//! Modelled on `characters_gpu.rs` and deliberately not merged with it: that one
//! owns a bone storage buffer and a per-actor staging pass for geometry that
//! deforms, and none of it applies to a rifle. What is shared is the part worth
//! sharing — the lighting, through `lighting.wgsl.inc`.
//!
//! **Every prop stays resident, and one of them is current.** It used to be one
//! at a time, on the argument that a re-upload is imperceptible — which was true
//! of the upload and false of everything in front of it: dropping the prop threw
//! away the *parse* too, so switching back to a gun re-decoded its textures
//! (57–110 ms, see `prop::preload`) on the frame thread. Three weapons of
//! vertices and textures is a few megabytes of VRAM to make a weapon switch
//! cost a hash lookup.

use std::collections::HashMap;

use glam::Vec3;
use wgpu::util::DeviceExt;

use crate::character::{MaterialDef, TextureImage};
use crate::prop::{Prop, PropVertex};

impl PropVertex {
    pub fn layout() -> wgpu::VertexBufferLayout<'static> {
        const ATTRS: [wgpu::VertexAttribute; 3] = wgpu::vertex_attr_array![
            0 => Float32x3, // position
            1 => Float32x3, // normal
            2 => Float32x2, // uv
        ];
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<PropVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &ATTRS,
        }
    }
}

#[repr(C)]
#[derive(Copy, Clone, bytemuck::Pod, bytemuck::Zeroable)]
struct MaterialUniform {
    base_color: [f32; 4],
    params: [f32; 4],
}

struct Draw {
    first_vertex: u32,
    vertex_count: u32,
    material: usize,
}

/// One uploaded weapon, ready to draw.
pub struct PropGpu {
    vertices: wgpu::Buffer,
    draws: Vec<Draw>,
    materials: Vec<wgpu::BindGroup>,
    /// The model's bounds, kept so a swap back can be re-fitted to the boxes
    /// without the parsed `Prop` — which is exactly what the cache exists to
    /// avoid rebuilding.
    bounds: (Vec3, Vec3),
}

/// The pipeline and the resident prop.
pub struct Props {
    pipeline: wgpu::RenderPipeline,
    shader: wgpu::ShaderModule,
    material_layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    /// Every prop that has been uploaded, by weapon id.
    uploaded: HashMap<String, PropGpu>,
    /// Which of them is in the hands, if any.
    current: Option<String>,
}

impl Props {
    pub fn new(
        device: &wgpu::Device,
        camera_layout: &wgpu::BindGroupLayout,
        format: wgpu::TextureFormat,
        samples: u32,
    ) -> Props {
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("prop-shader"),
            source: wgpu::ShaderSource::Wgsl(
                // The same lighting the world and the operator use. Concatenated
                // rather than imported: WGSL has no `include`.
                concat!(include_str!("lighting.wgsl.inc"), include_str!("prop.wgsl")).into(),
            ),
        });

        let material_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("prop-material-layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });

        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("prop-sampler"),
            address_mode_u: wgpu::AddressMode::Repeat,
            address_mode_v: wgpu::AddressMode::Repeat,
            address_mode_w: wgpu::AddressMode::Repeat,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::MipmapFilterMode::Linear,
            // Legal only because all three filters above are `Linear` — see
            // `mipmap::ANISOTROPY`.
            anisotropy_clamp: crate::mipmap::ANISOTROPY,
            ..Default::default()
        });

        let pipeline = build_pipeline(
            device,
            &shader,
            camera_layout,
            &material_layout,
            format,
            samples,
        );

        Props {
            pipeline,
            shader,
            material_layout,
            sampler,
            uploaded: HashMap::new(),
            current: None,
        }
    }

    /// Put an already-uploaded prop in the hands, and report its bounds so the
    /// view model can fit it. `None` leaves the current one alone — the caller
    /// has nothing to draw for this weapon and keeps its boxes.
    pub fn select(&mut self, weapon: &str) -> Option<(Vec3, Vec3)> {
        let bounds = self.uploaded.get(weapon)?.bounds;
        self.current = Some(weapon.to_string());
        Some(bounds)
    }

    /// Stop drawing a prop. The boxes take over; **nothing is unloaded**, so
    /// coming back to this weapon costs a lookup.
    pub fn clear(&mut self) {
        self.current = None;
    }

    /// Upload a parsed prop into the cache, without putting it in the hands.
    /// `select` does that, and the two are separate because the props arrive
    /// from the preloader in their own order rather than the order they are
    /// picked up in.
    pub fn set(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, weapon: &str, prop: &Prop) {
        let views: Vec<wgpu::TextureView> = prop
            .textures
            .iter()
            .map(|image| upload_texture(device, queue, image))
            .collect();
        // A material with no base-colour map still has to bind something, and a
        // 1x1 white pixel makes its factor the whole answer rather than needing
        // a second pipeline without a sampler.
        let fallback = upload_texture(
            device,
            queue,
            &TextureImage {
                width: 1,
                height: 1,
                rgba: vec![255, 255, 255, 255],
            },
        );

        let materials = prop
            .materials
            .iter()
            .map(|material| self.material_group(device, material, &views, &fallback))
            .collect();

        let vertices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("prop-vertices"),
            contents: bytemuck::cast_slice(&prop.vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });

        let draws = prop
            .primitives
            .iter()
            .map(|p| Draw {
                first_vertex: p.first_vertex,
                vertex_count: p.vertex_count,
                material: p.material,
            })
            .collect();

        self.uploaded.insert(
            weapon.to_string(),
            PropGpu {
                vertices,
                draws,
                materials,
                bounds: prop.bounds(),
            },
        );
    }

    fn material_group(
        &self,
        device: &wgpu::Device,
        material: &MaterialDef,
        views: &[wgpu::TextureView],
        fallback: &wgpu::TextureView,
    ) -> wgpu::BindGroup {
        let uniform = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("prop-material"),
            contents: bytemuck::cast_slice(&[MaterialUniform {
                base_color: material.base_color_factor.to_array(),
                params: [material.alpha_cutoff, 0.0, 0.0, 0.0],
            }]),
            usage: wgpu::BufferUsages::UNIFORM,
        });
        let view = material
            .base_color_texture
            .and_then(|i| views.get(i))
            .unwrap_or(fallback);
        device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("prop-material-group"),
            layout: &self.material_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: uniform.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::TextureView(view),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
            ],
        })
    }

    /// Rebuild the pipeline for a new sample count or surface format.
    pub fn rebuild(
        &mut self,
        device: &wgpu::Device,
        camera_layout: &wgpu::BindGroupLayout,
        format: wgpu::TextureFormat,
        samples: u32,
    ) {
        self.pipeline = build_pipeline(
            device,
            &self.shader,
            camera_layout,
            &self.material_layout,
            format,
            samples,
        );
    }

    /// Draw the resident prop, if there is one. Returns whether it drew.
    ///
    /// The **camera bind group is the caller's**, and it must be the view
    /// model's: that uniform carries both the weapon's own projection and the
    /// camera-to-world matrix the shader lights by. Handing it the world camera
    /// would draw the weapon somewhere out in the map.
    pub fn draw(&self, pass: &mut wgpu::RenderPass<'_>, camera: &wgpu::BindGroup) -> bool {
        let Some(prop) = self.current.as_ref().and_then(|id| self.uploaded.get(id)) else {
            return false;
        };
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, camera, &[]);
        pass.set_vertex_buffer(0, prop.vertices.slice(..));
        for draw in &prop.draws {
            let Some(material) = prop.materials.get(draw.material) else {
                continue;
            };
            pass.set_bind_group(1, material, &[]);
            pass.draw(
                draw.first_vertex..draw.first_vertex + draw.vertex_count,
                0..1,
            );
        }
        true
    }
}

fn upload_texture(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    image: &TextureImage,
) -> wgpu::TextureView {
    let size = wgpu::Extent3d {
        width: image.width.max(1),
        height: image.height.max(1),
        depth_or_array_layers: 1,
    };
    // `Space::Srgb` to match the format below — averaging these bytes raw comes
    // out darker than the surface they represent, worst in the mid-tones.
    let levels = crate::mipmap::chain(
        image.rgba.clone(),
        size.width,
        size.height,
        crate::mipmap::Space::Srgb,
    );
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("prop-texture"),
        size,
        mip_level_count: levels.len() as u32,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        // `Srgb`: the maps are authored in sRGB and the shader relies on the
        // hardware having decoded them. Sampling as linear washes a weapon out
        // to pale grey, which reads as broken lighting rather than a format.
        format: wgpu::TextureFormat::Rgba8UnormSrgb,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });
    for (level, (w, h, pixels)) in levels.iter().enumerate() {
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &texture,
                mip_level: level as u32,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            pixels,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(4 * w),
                rows_per_image: Some(*h),
            },
            wgpu::Extent3d {
                width: *w,
                height: *h,
                depth_or_array_layers: 1,
            },
        );
    }
    texture.create_view(&wgpu::TextureViewDescriptor::default())
}

fn build_pipeline(
    device: &wgpu::Device,
    shader: &wgpu::ShaderModule,
    camera_layout: &wgpu::BindGroupLayout,
    material_layout: &wgpu::BindGroupLayout,
    format: wgpu::TextureFormat,
    samples: u32,
) -> wgpu::RenderPipeline {
    let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("prop-pipeline-layout"),
        bind_group_layouts: &[Some(camera_layout), Some(material_layout)],
        immediate_size: 0,
    });
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("prop-pipeline"),
        layout: Some(&layout),
        vertex: wgpu::VertexState {
            module: shader,
            entry_point: Some("vs_prop"),
            buffers: &[Some(PropVertex::layout())],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: shader,
            entry_point: Some("fs_prop"),
            targets: &[Some(wgpu::ColorTargetState {
                format,
                blend: Some(wgpu::BlendState::REPLACE),
                write_mask: wgpu::ColorWrites::ALL,
            })],
            compilation_options: Default::default(),
        }),
        primitive: wgpu::PrimitiveState {
            topology: wgpu::PrimitiveTopology::TriangleList,
            strip_index_format: None,
            front_face: wgpu::FrontFace::Ccw,
            // **No culling**, matching the operator and for the same reason:
            // these are scanned and hand-modelled meshes, not a mesher's output,
            // and a single-sided sheet anywhere in one — a sling, a stamped
            // trigger guard — vanishes as the camera crosses its plane. A weapon
            // is a few thousand triangles at arm's length; both sides is nothing.
            cull_mode: None,
            polygon_mode: wgpu::PolygonMode::Fill,
            unclipped_depth: false,
            conservative: false,
        },
        depth_stencil: Some(wgpu::DepthStencilState {
            format: crate::renderer::DEPTH_FORMAT,
            depth_write_enabled: Some(true),
            depth_compare: Some(wgpu::CompareFunction::Less),
            stencil: wgpu::StencilState::default(),
            bias: wgpu::DepthBiasState::default(),
        }),
        multisample: wgpu::MultisampleState {
            count: samples,
            ..Default::default()
        },
        multiview_mask: None,
        cache: None,
    })
}
