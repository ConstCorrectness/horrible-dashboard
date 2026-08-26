//! The GPU half of the operator: one static vertex buffer, one bone matrix per
//! player per frame, and a textured pass.
//!
//! Kept out of `renderer.rs` because it is the only part of this renderer that
//! has textures, bind groups beyond the camera, or a per-draw dynamic offset.
//! Folding it in would spread three concepts through a file whose whole point is
//! that the world needs none of them.
//!
//! ## Why the skinning is on the GPU
//!
//! The obvious port of `bodies.rs` would keep building vertices on the CPU each
//! frame, the way every other mesh here does. It does not survive contact with
//! the asset: the operator is 90,093 vertices, so eight players is ~720k
//! vertices to transform *and re-upload* every frame — around 26 MB/frame across
//! the bus to save writing one shader. The geometry is uploaded once instead,
//! and what changes per frame is 34 matrices per player.

use glam::Mat4;
use wgpu::util::DeviceExt;

use crate::animator::ActorPose;
use crate::character::{Operator, SkinVertex};

/// How many players' bones the buffer is sized for.
///
/// Eight is the match limit, plus headroom for the range dummies. Overflowing it
/// draws the first `MAX_ACTORS` rather than growing mid-frame: a reallocation
/// inside a render pass is a stall at exactly the wrong moment, and a ninth body
/// missing for one frame is invisible next to it.
const MAX_ACTORS: usize = 16;

impl SkinVertex {
    const ATTRS: [wgpu::VertexAttribute; 5] = wgpu::vertex_attr_array![
        0 => Float32x3,
        1 => Float32x3,
        2 => Float32x2,
        3 => Uint32x4,
        4 => Float32x4,
    ];

    fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<SkinVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRS,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
struct MaterialUniform {
    base_color: [f32; 4],
    /// x: alpha cutoff. The rest is padding to the 16-byte minimum.
    params: [f32; 4],
}

struct PrimitiveDraw {
    first_vertex: u32,
    vertex_count: u32,
    material: usize,
}

pub struct Characters {
    pipeline: wgpu::RenderPipeline,
    vertices: wgpu::Buffer,
    primitives: Vec<PrimitiveDraw>,
    materials: Vec<wgpu::BindGroup>,
    /// Bones for every actor, one `stride` apart so a dynamic offset can select
    /// one player's slice.
    bones: wgpu::Buffer,
    bone_group: wgpu::BindGroup,
    stride: u32,
    bone_count: usize,
    /// Scratch, reused between frames so the per-frame path allocates nothing.
    staging: Vec<u8>,
    actors: u32,
    /// Kept so the pipeline can be rebuilt when the sample count changes.
    shader: wgpu::ShaderModule,
    bone_layout: wgpu::BindGroupLayout,
    material_layout: wgpu::BindGroupLayout,
}

impl Characters {
    pub fn new(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        operator: &Operator,
        camera_layout: &wgpu::BindGroupLayout,
        shadow_layout: &wgpu::BindGroupLayout,
        format: wgpu::TextureFormat,
        samples: u32,
    ) -> Characters {
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("skin-shader"),
            source: wgpu::ShaderSource::Wgsl(
                // Concatenated rather than imported: WGSL has no include, and the
                // lighting has to be one copy shared with the other shader.
                concat!(include_str!("lighting.wgsl.inc"), include_str!("skin.wgsl")).into(),
            ),
        });

        let bone_count = operator.bone_count();
        // A player's slice: the tint, then the matrices. Rounded up to the
        // dynamic-offset alignment, which is a device limit rather than a
        // constant — 256 on most desktop backends, but assuming that is how you
        // get a validation error on somebody else's GPU.
        let unaligned = 16 + bone_count * std::mem::size_of::<Mat4>();
        let alignment = device.limits().min_storage_buffer_offset_alignment as usize;
        let stride = unaligned.div_ceil(alignment) * alignment;

        let bones = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("operator-bones"),
            size: (stride * MAX_ACTORS) as u64,
            usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let bone_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("operator-bones-layout"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Storage { read_only: true },
                    has_dynamic_offset: true,
                    // Bounding the binding to one player's slice is what stops
                    // an out-of-range bone index reading another player's pose
                    // instead of failing.
                    min_binding_size: wgpu::BufferSize::new(unaligned as u64),
                },
                count: None,
            }],
        });
        let bone_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("operator-bones-group"),
            layout: &bone_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: wgpu::BindingResource::Buffer(wgpu::BufferBinding {
                    buffer: &bones,
                    offset: 0,
                    size: wgpu::BufferSize::new(unaligned as u64),
                }),
            }],
        });

        let material_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("operator-material-layout"),
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
            label: Some("operator-sampler"),
            address_mode_u: wgpu::AddressMode::Repeat,
            address_mode_v: wgpu::AddressMode::Repeat,
            address_mode_w: wgpu::AddressMode::Repeat,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            // One mip level is uploaded, so this only has to be valid, not good.
            mipmap_filter: wgpu::MipmapFilterMode::Nearest,
            ..Default::default()
        });

        let views: Vec<wgpu::TextureView> = operator
            .textures
            .iter()
            .map(|image| upload_texture(device, queue, image))
            .collect();
        // A material with no base-colour map still has to bind *something*, and
        // a 1x1 white pixel makes its factor the whole answer rather than
        // needing a second pipeline without a sampler.
        let fallback = upload_texture(
            device,
            queue,
            &crate::character::TextureImage {
                width: 1,
                height: 1,
                rgba: vec![255, 255, 255, 255],
            },
        );

        let materials = operator
            .materials
            .iter()
            .map(|material| {
                let uniform = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("operator-material"),
                    contents: bytemuck::cast_slice(&[MaterialUniform {
                        base_color: material.base_color_factor.to_array(),
                        params: [material.alpha_cutoff, 0.0, 0.0, 0.0],
                    }]),
                    usage: wgpu::BufferUsages::UNIFORM,
                });
                let view = material
                    .base_color_texture
                    .and_then(|i| views.get(i))
                    .unwrap_or(&fallback);
                device.create_bind_group(&wgpu::BindGroupDescriptor {
                    label: Some("operator-material-group"),
                    layout: &material_layout,
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
                            resource: wgpu::BindingResource::Sampler(&sampler),
                        },
                    ],
                })
            })
            .collect();

        let vertices = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("operator-vertices"),
            contents: bytemuck::cast_slice(&operator.vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });

        let primitives = operator
            .primitives
            .iter()
            .map(|p| PrimitiveDraw {
                first_vertex: p.first_vertex,
                vertex_count: p.vertex_count,
                material: p.material,
            })
            .collect();

        let pipeline = build_pipeline(
            device,
            &shader,
            camera_layout,
            &bone_layout,
            &material_layout,
            shadow_layout,
            format,
            samples,
        );

        Characters {
            pipeline,
            vertices,
            primitives,
            materials,
            bones,
            bone_group,
            stride: stride as u32,
            bone_count,
            staging: Vec::new(),
            actors: 0,
            shader,
            bone_layout,
            material_layout,
        }
    }

    /// Rebuild the pipeline for a new sample count or surface format.
    pub fn rebuild(
        &mut self,
        device: &wgpu::Device,
        camera_layout: &wgpu::BindGroupLayout,
        shadow_layout: &wgpu::BindGroupLayout,
        format: wgpu::TextureFormat,
        samples: u32,
    ) {
        self.pipeline = build_pipeline(
            device,
            &self.shader,
            camera_layout,
            &self.bone_layout,
            &self.material_layout,
            shadow_layout,
            format,
            samples,
        );
    }

    /// Upload this frame's poses.
    pub fn prepare(&mut self, queue: &wgpu::Queue, poses: &[ActorPose]) {
        let count = poses.len().min(MAX_ACTORS);
        self.actors = count as u32;
        if count == 0 {
            return;
        }
        self.staging.clear();
        self.staging.resize(self.stride as usize * count, 0);
        for (index, pose) in poses.iter().take(count).enumerate() {
            let base = index * self.stride as usize;
            self.staging[base..base + 16]
                .copy_from_slice(bytemuck::cast_slice(&pose.tint.to_array()));
            // A pose carrying fewer matrices than the rig has bones would leave
            // the tail as identity, which is a character with a detached limb
            // rather than an error — so the write is bounded by the rig.
            let bones = &pose.bones[..pose.bones.len().min(self.bone_count)];
            let start = base + 16;
            let bytes: &[u8] = bytemuck::cast_slice(bones);
            self.staging[start..start + bytes.len()].copy_from_slice(bytes);
        }
        queue.write_buffer(&self.bones, 0, &self.staging);
    }

    /// Draw every prepared actor.
    ///
    /// One draw per primitive per player — nine primitives is nine draws each,
    /// which at eight players is 72 calls a frame. Instancing them would need
    /// the bone array indexed by instance rather than bound per draw, and the
    /// materials differ per primitive anyway, so the batching would buy a
    /// fraction of a millisecond for a whole second binding scheme.
    pub fn draw<'a>(
        &'a self,
        pass: &mut wgpu::RenderPass<'a>,
        camera: &'a wgpu::BindGroup,
        shadow: &'a wgpu::BindGroup,
    ) {
        if self.actors == 0 {
            return;
        }
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, camera, &[]);
        // Players receive the sun's shadow but never cast it — the map is
        // rendered once, before any of them exist. See `shadow.rs`.
        pass.set_bind_group(3, shadow, &[]);
        pass.set_vertex_buffer(0, self.vertices.slice(..));
        for actor in 0..self.actors {
            pass.set_bind_group(1, &self.bone_group, &[actor * self.stride]);
            for primitive in &self.primitives {
                let Some(material) = self.materials.get(primitive.material) else {
                    continue;
                };
                pass.set_bind_group(2, material, &[]);
                pass.draw(
                    primitive.first_vertex..primitive.first_vertex + primitive.vertex_count,
                    0..1,
                );
            }
        }
    }
}

fn upload_texture(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    image: &crate::character::TextureImage,
) -> wgpu::TextureView {
    let size = wgpu::Extent3d {
        width: image.width.max(1),
        height: image.height.max(1),
        depth_or_array_layers: 1,
    };
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("operator-texture"),
        size,
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        // `Srgb`, and this is not cosmetic: the maps are authored in sRGB, and
        // sampling them as linear washes the character out to a pale grey that
        // reads as "the lighting is broken".
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
    bone_layout: &wgpu::BindGroupLayout,
    material_layout: &wgpu::BindGroupLayout,
    shadow_layout: &wgpu::BindGroupLayout,
    format: wgpu::TextureFormat,
    samples: u32,
) -> wgpu::RenderPipeline {
    let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("operator-pipeline-layout"),
        bind_group_layouts: &[
            Some(camera_layout),
            Some(bone_layout),
            Some(material_layout),
            Some(shadow_layout),
        ],
        immediate_size: 0,
    });
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("operator-pipeline"),
        layout: Some(&layout),
        vertex: wgpu::VertexState {
            module: shader,
            entry_point: Some("vs_skin"),
            buffers: &[Some(SkinVertex::layout())],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: shader,
            entry_point: Some("fs_skin"),
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
            // **No culling, deliberately.** The world can cull because its
            // mesher emits every surface once, facing the space you can stand
            // in. A character's kit is modelled as single-sided sheets — straps,
            // pouch flaps, the cut edges of a vest — and culling them makes a
            // limb vanish when the camera crosses its plane. Drawing both sides
            // costs fragments on a body that is a few hundred pixels tall.
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
