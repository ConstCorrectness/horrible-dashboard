//! What happens between the scene and the window: bloom and sharpening.
//!
//! This replaced the blit, and the blit's job is still the last step — the scene,
//! drawn at the render scale, stretched into the window — it just has two
//! optional things done on the way. See `post.wgsl` for what each pass is and
//! why the bloom works on the already-tone-mapped scene.
//!
//! **Off costs nothing extra.** With bloom at zero the chain is skipped entirely
//! and the composite is the old blit plus one uniform branch; with sharpening at
//! zero, that branch is not taken either.

use wgpu::util::DeviceExt;

/// How many bloom mips below the half-resolution first one. Five levels reach
/// 1/64 of the screen, which is a glow as wide as a room at 1080p — enough tail
/// without the smallest levels becoming a single blurred pixel.
const LEVELS: usize = 5;

/// The format the bloom chain is held in. Half-float so the additive up-sweep
/// cannot clip, which an 8-bit target would at the second level.
const BLOOM_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba16Float;

/// What passes the threshold, on the display-referred scene: only what is
/// already near white. Lower and a sunlit wall starts to glow.
const THRESHOLD: f32 = 0.82;
const KNEE: f32 = 0.12;

#[repr(C)]
#[derive(Copy, Clone, bytemuck::Pod, bytemuck::Zeroable)]
struct PostUniform {
    params: [f32; 4],
}

pub struct Post {
    pass_layout: wgpu::BindGroupLayout,
    composite_layout: wgpu::BindGroupLayout,
    prefilter: wgpu::RenderPipeline,
    down: wgpu::RenderPipeline,
    up: wgpu::RenderPipeline,
    composite: wgpu::RenderPipeline,
    sampler: wgpu::Sampler,
    uniform: wgpu::Buffer,
    /// The chain, largest first, and the bind groups that read each level.
    mips: Vec<wgpu::TextureView>,
    /// Reads the scene: the prefilter's source.
    scene_group: Option<wgpu::BindGroup>,
    /// `mip_groups[i]` reads mip `i`.
    mip_groups: Vec<wgpu::BindGroup>,
    composite_group: Option<wgpu::BindGroup>,
    bloom: f32,
    sharpen: f32,
}

impl Post {
    pub fn new(device: &wgpu::Device, output_format: wgpu::TextureFormat) -> Post {
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("post"),
            source: wgpu::ShaderSource::Wgsl(include_str!("post.wgsl").into()),
        });
        let texture_entry = |binding| wgpu::BindGroupLayoutEntry {
            binding,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
                view_dimension: wgpu::TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        };
        let base_entries = [
            texture_entry(0),
            wgpu::BindGroupLayoutEntry {
                binding: 1,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                count: None,
            },
            wgpu::BindGroupLayoutEntry {
                binding: 2,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            },
        ];
        // Two layouts, because a pass that writes a bloom level cannot also have
        // the top of the chain bound for reading — the final up-sweep writes mip
        // 0, and a texture both sampled and attached in one pass is a
        // validation error. Only the composite reads the chain whole.
        let pass_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("post-pass-layout"),
            entries: &base_entries,
        });
        let mut composite_entries = base_entries.to_vec();
        composite_entries.push(texture_entry(3));
        let composite_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("post-composite-layout"),
            entries: &composite_entries,
        });

        let pipeline = |layout: &wgpu::BindGroupLayout,
                        entry: &str,
                        format: wgpu::TextureFormat,
                        blend: wgpu::BlendState| {
            let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
                label: Some("post-pipeline-layout"),
                bind_group_layouts: &[Some(layout)],
                immediate_size: 0,
            });
            device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
                label: Some(entry),
                layout: Some(&pipeline_layout),
                vertex: wgpu::VertexState {
                    module: &shader,
                    entry_point: Some("vs_fullscreen"),
                    buffers: &[],
                    compilation_options: Default::default(),
                },
                fragment: Some(wgpu::FragmentState {
                    module: &shader,
                    entry_point: Some(entry),
                    targets: &[Some(wgpu::ColorTargetState {
                        format,
                        blend: Some(blend),
                        write_mask: wgpu::ColorWrites::ALL,
                    })],
                    compilation_options: Default::default(),
                }),
                primitive: wgpu::PrimitiveState::default(),
                depth_stencil: None,
                multisample: wgpu::MultisampleState::default(),
                multiview_mask: None,
                cache: None,
            })
        };
        let additive = wgpu::BlendState {
            color: wgpu::BlendComponent {
                src_factor: wgpu::BlendFactor::One,
                dst_factor: wgpu::BlendFactor::One,
                operation: wgpu::BlendOperation::Add,
            },
            alpha: wgpu::BlendComponent::REPLACE,
        };
        let prefilter = pipeline(
            &pass_layout,
            "fs_prefilter",
            BLOOM_FORMAT,
            wgpu::BlendState::REPLACE,
        );
        let down = pipeline(
            &pass_layout,
            "fs_down",
            BLOOM_FORMAT,
            wgpu::BlendState::REPLACE,
        );
        let up = pipeline(&pass_layout, "fs_up", BLOOM_FORMAT, additive);
        let composite = pipeline(
            &composite_layout,
            "fs_composite",
            output_format,
            wgpu::BlendState::REPLACE,
        );

        // Linear and clamped: a bloom tap past the edge must not wrap round to
        // the opposite side of the screen, which puts a lamp's glow on the far
        // wall of the frame.
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("post-sampler"),
            address_mode_u: wgpu::AddressMode::ClampToEdge,
            address_mode_v: wgpu::AddressMode::ClampToEdge,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let uniform = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("post-uniform"),
            contents: bytemuck::cast_slice(&[PostUniform {
                params: [0.0, 0.0, THRESHOLD, KNEE],
            }]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });

        Post {
            pass_layout,
            composite_layout,
            prefilter,
            down,
            up,
            composite,
            sampler,
            uniform,
            mips: Vec::new(),
            scene_group: None,
            mip_groups: Vec::new(),
            composite_group: None,
            bloom: 0.0,
            sharpen: 0.0,
        }
    }

    /// Rebuild what depends on the scene target: the bloom chain is sized from
    /// it, and every bind group holds a view of it. Called whenever the scene is
    /// reallocated — a stale view is a validation error on the next pass.
    pub fn resize(
        &mut self,
        device: &wgpu::Device,
        scene: &wgpu::TextureView,
        width: u32,
        height: u32,
    ) {
        self.mips.clear();
        let (mut w, mut h) = ((width / 2).max(1), (height / 2).max(1));
        for _ in 0..LEVELS {
            let view = device
                .create_texture(&wgpu::TextureDescriptor {
                    label: Some("bloom-mip"),
                    size: wgpu::Extent3d {
                        width: w,
                        height: h,
                        depth_or_array_layers: 1,
                    },
                    mip_level_count: 1,
                    sample_count: 1,
                    dimension: wgpu::TextureDimension::D2,
                    format: BLOOM_FORMAT,
                    usage: wgpu::TextureUsages::RENDER_ATTACHMENT
                        | wgpu::TextureUsages::TEXTURE_BINDING,
                    view_formats: &[],
                })
                .create_view(&wgpu::TextureViewDescriptor::default());
            self.mips.push(view);
            w = (w / 2).max(1);
            h = (h / 2).max(1);
        }
        let group = |view: &wgpu::TextureView| {
            device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("post-pass"),
                layout: &self.pass_layout,
                entries: &[
                    wgpu::BindGroupEntry {
                        binding: 0,
                        resource: wgpu::BindingResource::TextureView(view),
                    },
                    wgpu::BindGroupEntry {
                        binding: 1,
                        resource: wgpu::BindingResource::Sampler(&self.sampler),
                    },
                    wgpu::BindGroupEntry {
                        binding: 2,
                        resource: self.uniform.as_entire_binding(),
                    },
                ],
            })
        };
        let scene_group = group(scene);
        let mip_groups: Vec<wgpu::BindGroup> = self.mips.iter().map(group).collect();
        self.scene_group = Some(scene_group);
        self.mip_groups = mip_groups;
        self.composite_group = Some(device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("post-composite"),
            layout: &self.composite_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(scene),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: self.uniform.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 3,
                    resource: wgpu::BindingResource::TextureView(&self.mips[0]),
                },
            ],
        }));
    }

    pub fn set(&mut self, queue: &wgpu::Queue, bloom: f32, sharpen: f32) {
        self.bloom = bloom.clamp(0.0, 1.0);
        self.sharpen = sharpen.clamp(0.0, 1.0);
        queue.write_buffer(
            &self.uniform,
            0,
            bytemuck::cast_slice(&[PostUniform {
                params: [self.bloom, self.sharpen, THRESHOLD, KNEE],
            }]),
        );
    }

    /// Record the chain: bloom (when on), then the composite into `output`.
    pub fn encode(&self, encoder: &mut wgpu::CommandEncoder, output: &wgpu::TextureView) {
        let fullscreen = |encoder: &mut wgpu::CommandEncoder,
                          target: &wgpu::TextureView,
                          load: wgpu::LoadOp<wgpu::Color>,
                          pipeline: &wgpu::RenderPipeline,
                          group: &wgpu::BindGroup| {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("post"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: target,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load,
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            pass.set_pipeline(pipeline);
            pass.set_bind_group(0, group, &[]);
            pass.draw(0..3, 0..1);
        };
        let (Some(scene_group), Some(composite_group)) =
            (self.scene_group.as_ref(), self.composite_group.as_ref())
        else {
            return;
        };
        let clear = wgpu::LoadOp::Clear(wgpu::Color::BLACK);

        if self.bloom > 0.001 && !self.mips.is_empty() {
            fullscreen(encoder, &self.mips[0], clear, &self.prefilter, scene_group);
            for i in 1..self.mips.len() {
                fullscreen(
                    encoder,
                    &self.mips[i],
                    clear,
                    &self.down,
                    &self.mip_groups[i - 1],
                );
            }
            // Back up: each smaller level added onto the one above it.
            for i in (1..self.mips.len()).rev() {
                fullscreen(
                    encoder,
                    &self.mips[i - 1],
                    wgpu::LoadOp::Load,
                    &self.up,
                    &self.mip_groups[i],
                );
            }
        }
        fullscreen(encoder, output, clear, &self.composite, composite_group);
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_chain_ends_well_above_a_pixel_at_1080p() {
        // Five levels below half resolution is 1/64: 30 pixels tall at 1080p.
        let mut h = 1080 / 2;
        for _ in 1..super::LEVELS {
            h /= 2;
        }
        assert!(h >= 16, "the smallest bloom level is {h} pixels tall");
    }
}
