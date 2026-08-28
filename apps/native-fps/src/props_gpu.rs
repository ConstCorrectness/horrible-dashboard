//! The weapon props on the GPU.
//!
//! Modelled on `characters_gpu.rs` and deliberately not merged with it: that one
//! owns a bone storage buffer and a per-actor staging pass for geometry that
//! deforms, and none of it applies to a rifle. What is shared is the part worth
//! sharing — the lighting, through `lighting.wgsl.inc`.
//!
//! One prop is resident at a time, because one weapon is in your hands at a
//! time. Swapping re-uploads, which is a few thousand vertices and a megabyte of
//! texture on a weapon switch; the alternative — every weapon resident for the
//! whole match — is four times the VRAM to save an upload nobody can perceive.

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
}

/// The pipeline and the resident prop.
pub struct Props {
    pipeline: wgpu::RenderPipeline,
    shader: wgpu::ShaderModule,
    material_layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    /// The prop currently in the hands, and which weapon it is.
    resident: Option<(String, PropGpu)>,
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
            // One mip level is uploaded, so this only has to be valid.
            mipmap_filter: wgpu::MipmapFilterMode::Nearest,
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
            resident: None,
        }
    }

    /// Whether a prop for this weapon is already uploaded.
    pub fn holds(&self, weapon: &str) -> bool {
        self.resident.as_ref().is_some_and(|(id, _)| id == weapon)
    }

    /// Drop whatever is resident. The boxes take over until something is set.
    pub fn clear(&mut self) {
        self.resident = None;
    }

    /// Upload a parsed prop, replacing whatever was in the hands.
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

        self.resident = Some((
            weapon.to_string(),
            PropGpu {
                vertices,
                draws,
                materials,
            },
        ));
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
        let Some((_, prop)) = &self.resident else {
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
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("prop-texture"),
        size,
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        // `Srgb`: the maps are authored in sRGB and the shader relies on the
        // hardware having decoded them. Sampling as linear washes a weapon out
        // to pale grey, which reads as broken lighting rather than a format.
        format: wgpu::TextureFormat::Rgba8UnormSrgb,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });
    queue.write_texture(
        wgpu::TexelCopyTextureInfo {
            texture: &texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        &image.rgba,
        wgpu::TexelCopyBufferLayout {
            offset: 0,
            bytes_per_row: Some(4 * size.width),
            rows_per_image: Some(size.height),
        },
        size,
    );
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
