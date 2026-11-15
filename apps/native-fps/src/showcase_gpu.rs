//! The menu's stage on the GPU: the match's own model pipelines, in a box.
//!
//! `front_gpu.rs` explains why the menu does not use the match's `Renderer` —
//! it is a whole scene, and the menu is rectangles and text. The stage is the
//! exception, and it is kept the size of one: it borrows the two pipelines that
//! draw a weapon and an operator (`Props`, `Characters`) and the tone curve
//! (`Post`), points them at a small target of its own, and hands the menu a
//! finished picture to place. No world, no shadow cascade, no effects.
//!
//! Using the match's pipelines rather than a simpler copy is the point: what is
//! turned over in the menu is lit and shaded exactly as it will be in the
//! player's hands a minute later.
//!
//! Three passes a frame: the model into a half-float scene, the tone curve into
//! a picture in the window's own format, and that picture into its rectangle of
//! the menu (`showcase.wgsl`), which is the only one that touches the window.

use glam::{Mat4, Vec3};
use wgpu::util::DeviceExt;

use crate::animator::Squad;
use crate::atmosphere::{Atmosphere, Lighting};
use crate::characters_gpu::Characters;
use crate::post::Post;
use crate::prop::Prop;
use crate::props_gpu::Props;
use crate::protocol::PlayerRow;
use crate::renderer::{Vertex, DEPTH_FORMAT, SCENE_FORMAT};
use crate::shadow::{ShadowMap, ShadowQuality};
use crate::showcase::{Showcase, Subject};

/// What the stage is cleared to, as the colour it should come out of the tone
/// curve as. It is never seen — the picture is placed by the scene's alpha —
/// but FXAA blends a model's edge pixels with it, so it is the loadout panel's
/// own fill: an edge that fades toward the panel has no dark fringe.
const BACKDROP: u32 = 0x1c222c;
/// The stage's own light: a photographer's, not a map's.
///
/// The default rig is a cube map's interior — one warm sun and a dim fill —
/// and under it a black pistol on a dark backdrop is a hole in the picture.
/// So: a bright key from over the camera's left shoulder, a strong cool rim
/// from behind on the right to cut the silhouette out of the backdrop, and a
/// bright sky for gun metal to have something to reflect. The camera looks
/// along +x with +z to its right.
const STUDIO: Atmosphere = Atmosphere {
    sky_zenith: 0xdfe8ff,
    sky_horizon: 0x8fa4c8,
    hemi_sky: 0xdde8ff,
    hemi_ground: 0x5a4c40,
    hemi_intensity: 2.4,
    sun_color: 0xfff4e4,
    sun_intensity: 2.8,
    sun_dir: [-0.6, 0.7, -0.4],
    fill_color: 0x4fd6ff,
    fill_intensity: 1.6,
    fill_dir: [0.7, 0.3, 0.6],
    fog_color: 0x11161f,
    fog_density: 0.0,
    exposure: 1.5,
    sun_disc: false,
};
/// The stage is drawn at this many pixels per pixel of its rectangle and
/// scaled down, which is its anti-aliasing: the pipelines here run unsampled.
const SUPERSAMPLE: f32 = 1.5;
/// An operator's box, in render space: feet at the origin, 5.2 cubes tall,
/// and as wide as its shoulders. A rifle held out may cross the faded edge.
const OPERATOR_BOUNDS: (Vec3, Vec3) = (Vec3::new(-0.9, 0.0, -0.9), Vec3::new(0.9, 5.3, 0.9));
/// The id of the one player the stage's `Squad` animates.
const STAGE_ID: &str = "stage";

/// The `CameraUniform` every scene pipeline reads: `view_proj`, `params`,
/// `reveal`, `light_transform`.
type CameraUniform = [f32; 40];

fn camera_uniform(view_proj: Mat4, to_world: Mat4) -> CameraUniform {
    let mut uniform = [0f32; 40];
    uniform[..16].copy_from_slice(&view_proj.to_cols_array());
    // Shading detail: the full rig. No fog and no shadow, as in the hands.
    uniform[17] = 2.0;
    uniform[24..40].copy_from_slice(&to_world.to_cols_array());
    uniform
}

/// The targets for one size of stage.
struct Targets {
    width: u32,
    height: u32,
    scene: wgpu::TextureView,
    depth: wgpu::TextureView,
    picture: wgpu::TextureView,
    picture_texture: wgpu::Texture,
    /// Reads `picture`, for the pass into the window.
    group: wgpu::BindGroup,
}

/// Both sides' operators, once they have been parsed.
struct Operators {
    squad: Squad,
    ct: Characters,
    t: Characters,
    shadow: ShadowMap,
}

pub struct ShowcaseGpu {
    format: wgpu::TextureFormat,
    camera_layout: wgpu::BindGroupLayout,
    camera_buffer: wgpu::Buffer,
    camera_group: wgpu::BindGroup,
    props: Props,
    /// The bounds of each prop uploaded, by id.
    bounds: std::collections::HashMap<String, (Vec3, Vec3)>,
    operators: Option<Operators>,
    post: Post,
    targets: Option<Targets>,
    blit: wgpu::RenderPipeline,
    blit_layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    /// Whether the last `render` left a picture worth placing.
    drawn: bool,
}

impl ShowcaseGpu {
    /// `format` is the window's: the picture is made in it, so the copy into
    /// the window is a copy.
    pub fn new(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        format: wgpu::TextureFormat,
    ) -> ShowcaseGpu {
        let camera_layout = crate::atmosphere::camera_layout(device);
        let lights = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("stage-lights"),
            contents: bytemuck::cast_slice(&[
                Lighting::new(STUDIO, &[]).uniform(Vec3::ZERO, 0, 1.0)
            ]),
            usage: wgpu::BufferUsages::UNIFORM,
        });
        let camera_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("stage-camera"),
            size: std::mem::size_of::<CameraUniform>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let camera_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("stage-camera"),
            layout: &camera_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: camera_buffer.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: lights.as_entire_binding(),
                },
            ],
        });
        let props = Props::new(device, &camera_layout, SCENE_FORMAT, 1);

        let mut post = Post::new(device, format);
        // No bloom or sharpening on something this small; FXAA over the
        // supersampled picture takes the last of the stair-steps off the
        // barrel's long edges.
        post.set(device, queue, 0.0, 0.0, true);

        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("stage-blit"),
            source: wgpu::ShaderSource::Wgsl(include_str!("showcase.wgsl").into()),
        });
        let blit_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("stage-blit"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
                // The scene, for its alpha: where a model was drawn.
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
            ],
        });
        let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("stage-blit"),
            bind_group_layouts: &[Some(&blit_layout)],
            immediate_size: 0,
        });
        let blit = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("stage-blit"),
            layout: Some(&layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_stage"),
                buffers: &[],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_stage"),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("stage-blit"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });

        ShowcaseGpu {
            format,
            camera_layout,
            camera_buffer,
            camera_group,
            props,
            bounds: std::collections::HashMap::new(),
            operators: None,
            post,
            targets: None,
            blit,
            blit_layout,
            sampler,
            drawn: false,
        }
    }

    /// Upload a parsed weapon prop. The parse is the slow part and is the
    /// caller's to do off the frame thread (`prop::preload`).
    pub fn add_prop(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, id: &str, prop: &Prop) {
        self.props.set(device, queue, id, prop);
        self.bounds.insert(id.to_string(), prop.bounds());
    }

    /// Upload both operators. `squad` is `Squad::load`'s, which is slow the
    /// first time it is called in a process and a lookup after that — so the
    /// caller makes the first call on a worker.
    pub fn add_operators(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, squad: Squad) {
        // The skin pipeline binds the sun's shadow whether or not anything
        // casts one. This one holds a single triangle far under the floor.
        let below = [
            Vertex::new([-1.0, -1.0, -50.0], [0.0, 0.0, 1.0], [0.0; 3]),
            Vertex::new([1.0, -1.0, -50.0], [0.0, 0.0, 1.0], [0.0; 3]),
            Vertex::new([0.0, 1.0, -50.0], [0.0, 0.0, 1.0], [0.0; 3]),
        ];
        let buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("stage-shadow-caster"),
            contents: bytemuck::cast_slice(&below),
            usage: wgpu::BufferUsages::VERTEX,
        });
        let shadow = ShadowMap::new(
            device,
            queue,
            &buffer,
            below.len() as u32,
            (Vec3::new(-4.0, -4.0, -1.0), Vec3::new(4.0, 4.0, 8.0)),
            crate::atmosphere::Atmosphere::CUBE.sun_direction(),
            ShadowQuality::default(),
        );
        let characters = |operator| {
            Characters::new(
                device,
                queue,
                operator,
                &self.camera_layout,
                &shadow.layout,
                SCENE_FORMAT,
                1,
            )
        };
        self.operators = Some(Operators {
            ct: characters(squad.operator()),
            t: characters(squad.operator_t()),
            squad,
            shadow,
        });
    }

    /// Whether `subject` has arrived and can be drawn. Until it has, the menu
    /// keeps its silhouette.
    pub fn ready(&self, subject: &Subject) -> bool {
        match subject {
            Subject::Weapon(id) => self.bounds.contains_key(id),
            Subject::Operator(_) => self.operators.is_some(),
        }
    }

    fn resize(&mut self, device: &wgpu::Device, width: u32, height: u32) {
        if self
            .targets
            .as_ref()
            .is_some_and(|t| t.width == width && t.height == height)
        {
            return;
        }
        let texture = |label, format, usage| {
            device.create_texture(&wgpu::TextureDescriptor {
                    label: Some(label),
                    size: wgpu::Extent3d {
                        width,
                        height,
                        depth_or_array_layers: 1,
                    },
                    mip_level_count: 1,
                    sample_count: 1,
                    dimension: wgpu::TextureDimension::D2,
                    format,
                    usage,
                    view_formats: &[],
                })
        };
        let view = |texture: &wgpu::Texture| texture.create_view(&wgpu::TextureViewDescriptor::default());
        let sampled = wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING;
        let scene = view(&texture("stage-scene", SCENE_FORMAT, sampled));
        let depth = view(&texture(
            "stage-depth",
            DEPTH_FORMAT,
            wgpu::TextureUsages::RENDER_ATTACHMENT,
        ));
        // Readable back, for the preview example; the menu only samples it.
        let picture_texture = texture(
            "stage-picture",
            self.format,
            sampled | wgpu::TextureUsages::COPY_SRC,
        );
        let picture = view(&picture_texture);
        let group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("stage-blit"),
            layout: &self.blit_layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(&picture),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: wgpu::BindingResource::TextureView(&scene),
                },
            ],
        });
        self.post.resize(device, &scene, width, height);
        self.targets = Some(Targets {
            width,
            height,
            scene,
            depth,
            picture,
            picture_texture,
            group,
        });
    }

    /// Draw the subject into the stage's own picture, for a rectangle of
    /// `width` by `height` pixels. Nothing here touches the window; `place`
    /// does, inside the menu's pass.
    ///
    /// `dt` advances the operator's animation, which is the client's own
    /// `Squad` and so breathes, and aims where it is turned, as it will in a
    /// match.
    #[allow(clippy::too_many_arguments)]
    pub fn render(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        encoder: &mut wgpu::CommandEncoder,
        show: &Showcase,
        width: f32,
        height: f32,
        dt: f32,
    ) {
        self.drawn = false;
        let Some(subject) = show.subject().filter(|s| self.ready(s)) else {
            return;
        };
        let cap = device.limits().max_texture_dimension_2d.min(2048) as f32;
        let w = (width * SUPERSAMPLE).clamp(16.0, cap) as u32;
        let h = (height * SUPERSAMPLE).clamp(16.0, cap) as u32;
        self.resize(device, w, h);
        let aspect = w as f32 / h as f32;

        let uniform = match subject {
            Subject::Weapon(id) => {
                let bounds = self.bounds[id];
                self.props.select(id);
                self.props.tint(queue, id, show.tint());
                let model = show.weapon_model((bounds.0 + bounds.1) * 0.5);
                camera_uniform(
                    show.camera(bounds, aspect).view_projection(w, h) * model,
                    model,
                )
            }
            Subject::Operator(side) => {
                let Some(operators) = self.operators.as_mut() else {
                    return;
                };
                // Facing the camera, which looks along cube +x, then turned.
                let row = PlayerRow {
                    id: STAGE_ID.to_string(),
                    team: *side as i32,
                    yaw: std::f32::consts::PI - show.turn(),
                    alive: true,
                    ground: true,
                    hp: 100.0,
                    weapon: 2,
                    ..Default::default()
                };
                let poses = operators.squad.update(dt, std::slice::from_ref(&row), "");
                let (drawn, hidden) = if *side == 1 {
                    (&mut operators.t, &mut operators.ct)
                } else {
                    (&mut operators.ct, &mut operators.t)
                };
                drawn.prepare(queue, poses);
                hidden.prepare(queue, &[]);
                camera_uniform(
                    show.camera(OPERATOR_BOUNDS, aspect).view_projection(w, h),
                    Mat4::IDENTITY,
                )
            }
        };
        queue.write_buffer(&self.camera_buffer, 0, bytemuck::cast_slice(&uniform));

        let Some(targets) = self.targets.as_ref() else {
            return;
        };
        let [r, g, b] = crate::atmosphere::display_hex_to_scene(BACKDROP);
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("stage"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &targets.scene,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: r as f64,
                            g: g as f64,
                            b: b as f64,
                            // Nothing here: `showcase.wgsl` reads this back
                            // as the picture's coverage.
                            a: 0.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &targets.depth,
                    depth_ops: Some(wgpu::Operations {
                        load: wgpu::LoadOp::Clear(1.0),
                        store: wgpu::StoreOp::Store,
                    }),
                    stencil_ops: None,
                }),
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            match (subject, self.operators.as_ref()) {
                (Subject::Weapon(_), _) => {
                    self.props
                        .draw(&mut pass, std::slice::from_ref(&self.camera_group));
                }
                (Subject::Operator(side), Some(operators)) => {
                    let characters = if *side == 1 {
                        &operators.t
                    } else {
                        &operators.ct
                    };
                    characters.draw(&mut pass, &self.camera_group, &operators.shadow.bind_group);
                }
                (Subject::Operator(_), None) => {}
            }
        }
        self.post.encode(encoder, &targets.picture);
        self.drawn = true;
    }

    /// The stage's picture, for a caller that wants it somewhere other than
    /// the menu — the preview example reads it back.
    pub fn picture(&self) -> Option<(&wgpu::Texture, u32, u32)> {
        self.targets
            .as_ref()
            .filter(|_| self.drawn)
            .map(|t| (&t.picture_texture, t.width, t.height))
    }

    /// Place the last `render`'s picture in a rectangle of the pass's target,
    /// in pixels. Restores the viewport to `full` afterwards, since the pass
    /// is the menu's and has more to draw.
    pub fn place(
        &self,
        pass: &mut wgpu::RenderPass<'_>,
        rect: (f32, f32, f32, f32),
        full: (u32, u32),
    ) {
        let Some(targets) = self.targets.as_ref().filter(|_| self.drawn) else {
            return;
        };
        let (fw, fh) = (full.0 as f32, full.1 as f32);
        // A viewport outside the target is a validation error, and a stage
        // half off a resized window is not worth one.
        let x = rect.0.clamp(0.0, fw - 1.0);
        let y = rect.1.clamp(0.0, fh - 1.0);
        let w = rect.2.min(fw - x);
        let h = rect.3.min(fh - y);
        if w < 1.0 || h < 1.0 {
            return;
        }
        pass.set_viewport(x, y, w, h, 0.0, 1.0);
        pass.set_pipeline(&self.blit);
        pass.set_bind_group(0, &targets.group, &[]);
        pass.draw(0..3, 0..1);
        pass.set_viewport(0.0, 0.0, fw, fh, 0.0, 1.0);
    }
}
