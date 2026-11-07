//! What happens between the scene and the window: bloom, the tone curve, FXAA,
//! sharpening, the flashbang's afterimage and the dither.
//!
//! This replaced the blit, and the blit's job is still the last step — the scene,
//! drawn at the render scale, stretched into the window — it just has optional
//! things done on the way. It is also where the scene is **tone mapped**, once:
//! the scene is pre-exposed linear light in a half-float target (`SCENE_FORMAT`
//! in `renderer.rs`), the bloom is thresholded on and added back into that
//! light, and `display` runs after. See `post.wgsl` for what each pass is.
//!
//! **Off costs nothing extra.** With bloom at zero the chain is skipped entirely;
//! with FXAA off there is no tone-map target and the composite runs the curve
//! itself; with sharpening at zero its branch is not taken.

use wgpu::util::DeviceExt;

/// How many bloom mips below the half-resolution first one. Five levels reach
/// 1/64 of the screen, which is a glow as wide as a room at 1080p — enough tail
/// without the smallest levels becoming a single blurred pixel.
const LEVELS: usize = 5;

/// The format the bloom chain is held in. Half-float so the additive up-sweep
/// cannot clip, which an 8-bit target would at the second level.
const BLOOM_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba16Float;

/// What passes the threshold, in the scene's **linear** light: brighter than
/// white. A sunlit wall on Dust II is about 0.95, so it stays dark under the
/// knee; a lamp's emissive or the sun's disc is several times over and
/// glows by how far over it is.
const THRESHOLD: f32 = 1.4;
/// The soft knee's half-width: the glow fades in from `THRESHOLD - KNEE`, so
/// the edge of a lamp is not a hard ring.
const KNEE: f32 = 0.5;

/// What `fs_tonemap` writes for FXAA to read: display-referred, but half float,
/// so the gradient a sky makes is not cut to 8-bit steps before the dither
/// gets to it.
const TONEMAP_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba16Float;

#[repr(C)]
#[derive(Copy, Clone, bytemuck::Pod, bytemuck::Zeroable)]
struct PostUniform {
    params: [f32; 4],
    /// x: afterimage strength, 0..1. The rest is padding to a 16-byte row.
    flash: [f32; 4],
}

pub struct Post {
    pass_layout: wgpu::BindGroupLayout,
    composite_layout: wgpu::BindGroupLayout,
    prefilter: wgpu::RenderPipeline,
    down: wgpu::RenderPipeline,
    up: wgpu::RenderPipeline,
    composite: wgpu::RenderPipeline,
    /// The scene plus its bloom through the curve, for FXAA (`fs_tonemap`).
    tonemap: wgpu::RenderPipeline,
    sampler: wgpu::Sampler,
    uniform: wgpu::Buffer,
    /// The chain, largest first, and the bind groups that read each level.
    mips: Vec<wgpu::TextureView>,
    /// Reads the scene: the prefilter's source.
    scene_group: Option<wgpu::BindGroup>,
    /// `mip_groups[i]` reads mip `i`.
    mip_groups: Vec<wgpu::BindGroup>,
    composite_group: Option<wgpu::BindGroup>,
    /// `fs_tonemap`'s output and the composite group that reads it in place of
    /// the scene. Allocated only while FXAA is on.
    tonemapped: Option<(wgpu::TextureView, wgpu::BindGroup)>,
    /// What `resize` was last given, so turning FXAA on can allocate its target
    /// without the renderer resizing.
    scene_view: Option<(wgpu::TextureView, u32, u32)>,
    bloom: f32,
    sharpen: f32,
    fxaa: bool,
    /// Whether the composite dithers its write. On in the client; off in the
    /// previews, whose coverage statistics count exact clear-colour bytes.
    dither: bool,
    /// Whether the output target encodes sRGB on write, which decides the
    /// space the dither's one step is measured in.
    srgb_output: bool,
    /// The flashbang's ghost: a half-resolution copy of the scene taken the
    /// frame a flash landed, blended back in under the white as it fades. The
    /// browser's is a canvas copy (`flash-layer.ts`); this is the same frame,
    /// kept on the GPU.
    capture: wgpu::RenderPipeline,
    afterimage: Option<wgpu::TextureView>,
    afterimage_strength: f32,
    /// A capture is owed on the next `encode`. A `Cell` because `encode` takes
    /// `&self`, like every other pass the renderer records.
    capture_pending: std::cell::Cell<bool>,
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
        composite_entries.push(texture_entry(4));
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
        let capture = pipeline(
            &pass_layout,
            "fs_capture",
            BLOOM_FORMAT,
            wgpu::BlendState::REPLACE,
        );
        let tonemap = pipeline(
            &composite_layout,
            "fs_tonemap",
            TONEMAP_FORMAT,
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
                flash: [0.0; 4],
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
            tonemap,
            sampler,
            uniform,
            mips: Vec::new(),
            scene_group: None,
            mip_groups: Vec::new(),
            composite_group: None,
            tonemapped: None,
            scene_view: None,
            bloom: 0.0,
            sharpen: 0.0,
            fxaa: false,
            dither: true,
            srgb_output: output_format.is_srgb(),
            capture,
            afterimage: None,
            afterimage_strength: 0.0,
            capture_pending: std::cell::Cell::new(false),
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
        let afterimage = device
            .create_texture(&wgpu::TextureDescriptor {
                label: Some("flash-afterimage"),
                size: wgpu::Extent3d {
                    width: (width / 2).max(1),
                    height: (height / 2).max(1),
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: BLOOM_FORMAT,
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING,
                view_formats: &[],
            })
            .create_view(&wgpu::TextureViewDescriptor::default());
        let scene_group = group(scene);
        let mip_groups: Vec<wgpu::BindGroup> = self.mips.iter().map(group).collect();
        self.scene_group = Some(scene_group);
        self.mip_groups = mip_groups;
        self.afterimage = Some(afterimage);
        self.composite_group = Some(self.composite_group_reading(device, scene));
        self.scene_view = Some((scene.clone(), width, height));
        self.tonemapped = None;
        self.ensure_tonemapped(device);
        // A resize throws the old ghost away; one owed is still owed.
    }

    /// The composite's group with `source` as its first input: the scene, or
    /// `fs_tonemap`'s image with FXAA on.
    fn composite_group_reading(&self, device: &wgpu::Device, source: &wgpu::TextureView) -> wgpu::BindGroup {
        device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("post-composite"),
            layout: &self.composite_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(source),
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
                wgpu::BindGroupEntry {
                    binding: 4,
                    resource: wgpu::BindingResource::TextureView(
                        self.afterimage.as_ref().expect("resize builds the afterimage first"),
                    ),
                },
            ],
        })
    }

    /// FXAA's target at the scene's size, allocated while FXAA is on and
    /// dropped while it is off: at 1080p it is 16 MB nobody else needs.
    fn ensure_tonemapped(&mut self, device: &wgpu::Device) {
        if !self.fxaa {
            self.tonemapped = None;
            return;
        }
        if self.tonemapped.is_some() {
            return;
        }
        let Some((_, width, height)) = self.scene_view.as_ref() else {
            return;
        };
        let view = device
            .create_texture(&wgpu::TextureDescriptor {
                label: Some("post-tonemapped"),
                size: wgpu::Extent3d {
                    width: *width,
                    height: *height,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: TONEMAP_FORMAT,
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING,
                view_formats: &[],
            })
            .create_view(&wgpu::TextureViewDescriptor::default());
        let group = self.composite_group_reading(device, &view);
        self.tonemapped = Some((view, group));
    }

    /// Bloom and sharpening strengths, 0..1, and whether FXAA runs.
    pub fn set(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, bloom: f32, sharpen: f32, fxaa: bool) {
        self.bloom = bloom.clamp(0.0, 1.0);
        self.sharpen = sharpen.clamp(0.0, 1.0);
        self.fxaa = fxaa;
        self.ensure_tonemapped(device);
        self.write(queue);
    }

    /// Whether the composite dithers. The client leaves it on.
    pub fn set_dither(&mut self, queue: &wgpu::Queue, dither: bool) {
        self.dither = dither;
        self.write(queue);
    }

    /// The flashbang's afterimage: how strongly the ghost shows this frame, and
    /// whether to take a new one from this frame's scene first.
    pub fn set_flash(&mut self, queue: &wgpu::Queue, strength: f32, capture: bool) {
        let strength = strength.clamp(0.0, 1.0);
        if capture {
            self.capture_pending.set(true);
        }
        if (strength - self.afterimage_strength).abs() > 1e-4 {
            self.afterimage_strength = strength;
            self.write(queue);
        }
    }

    fn write(&self, queue: &wgpu::Queue) {
        queue.write_buffer(
            &self.uniform,
            0,
            bytemuck::cast_slice(&[PostUniform {
                params: [self.bloom, self.sharpen, THRESHOLD, KNEE],
                flash: [
                    self.afterimage_strength,
                    if self.fxaa_ready() { 1.0 } else { 0.0 },
                    if self.dither { 1.0 } else { 0.0 },
                    if self.srgb_output { 1.0 } else { 0.0 },
                ],
            }]),
        );
    }

    /// FXAA is on and its target exists (it does not before the first resize).
    fn fxaa_ready(&self) -> bool {
        self.fxaa && self.tonemapped.is_some()
    }

    /// Record the chain: bloom (when on), the tone-map target (with FXAA), then
    /// the composite into `output`.
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

        // Before the composite, from the scene this frame drew: the ghost of a
        // flash is the last thing you saw, which is this frame.
        if self.capture_pending.get() {
            if let Some(afterimage) = self.afterimage.as_ref() {
                fullscreen(encoder, afterimage, clear, &self.capture, scene_group);
                self.capture_pending.set(false);
            }
        }

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
        match self.tonemapped.as_ref().filter(|_| self.fxaa) {
            Some((tonemapped, reads_tonemapped)) => {
                fullscreen(encoder, tonemapped, clear, &self.tonemap, composite_group);
                fullscreen(encoder, output, clear, &self.composite, reads_tonemapped);
            }
            None => fullscreen(encoder, output, clear, &self.composite, composite_group),
        }
    }
}

/// Tone map an HDR scene into `output` the way the client's final pass does,
/// bloom, sharpening, FXAA and the dither off: for the preview examples and
/// tests, which draw the scene pipelines into their own targets and would
/// otherwise read back linear light as if it were a picture.
pub fn display_once(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    encoder: &mut wgpu::CommandEncoder,
    scene: &wgpu::TextureView,
    width: u32,
    height: u32,
    output: &wgpu::TextureView,
    output_format: wgpu::TextureFormat,
) {
    display_with(device, queue, encoder, scene, width, height, output, output_format, PostLook::default());
}

/// The final pass's optional steps, for a preview that wants to show them.
#[derive(Debug, Clone, Copy, Default)]
pub struct PostLook {
    pub bloom: f32,
    pub sharpen: f32,
    pub fxaa: bool,
    pub dither: bool,
}

/// `display_once` with the client's optional steps as `look` asks.
#[allow(clippy::too_many_arguments)]
pub fn display_with(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    encoder: &mut wgpu::CommandEncoder,
    scene: &wgpu::TextureView,
    width: u32,
    height: u32,
    output: &wgpu::TextureView,
    output_format: wgpu::TextureFormat,
    look: PostLook,
) {
    let mut post = Post::new(device, output_format);
    post.dither = look.dither;
    post.resize(device, scene, width, height);
    post.set(device, queue, look.bloom, look.sharpen, look.fxaa);
    post.encode(encoder, output);
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
