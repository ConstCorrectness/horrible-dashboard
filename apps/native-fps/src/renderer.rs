//! The GPU side: one pipeline, two vertex buffers, a depth buffer.
//!
//! `wgpu` rather than a hand-rolled anything, and rather than raw Vulkan: it
//! selects **DX12 or Vulkan on Windows, Vulkan on Linux, Metal on macOS** from one
//! backend, which is the entire cross-platform claim this client makes. Writing to
//! Vulkan directly would mean shipping nothing for macOS, where Vulkan does not
//! exist without a translation layer.
//!
//! The scene is two draws, both from the same pipeline:
//!
//! - **the world**, uploaded once. A map is 13k–113k triangles and never changes,
//!   so it is a static buffer written at load and never touched again. Rebuilding
//!   it per frame would be the single most expensive thing this client does, for
//!   no reason.
//! - **the bodies**, rebuilt each frame. There are at most sixteen of them and
//!   they move every tick, so the buffer is written per frame — but into a
//!   **pre-allocated** buffer, not a fresh allocation, because allocating on the
//!   GPU every frame is how a renderer develops a stutter that profiles as
//!   "driver".
//!
//! What makes this feel different from the browser client is not here — it is in
//! `main.rs`'s use of raw device input and an uncapped present mode. This file
//! just has to not get in the way: no per-frame allocation, no pipeline rebuilds,
//! no readbacks.

use std::sync::Arc;

use bytemuck::{Pod, Zeroable};
use wgpu::util::DeviceExt;
use winit::window::Window;

use crate::camera::Camera;
use crate::geometry::MeshData;
use crate::hud::OverlayVertex;

/// One vertex, laid out exactly as the shader declares it.
///
/// `#[repr(C)]` is not decoration: the GPU reads this by byte offset, and Rust's
/// default layout is explicitly allowed to reorder fields. Without it the colours
/// would arrive in the normal's slot on some future compiler version, with no
/// error anywhere.
#[repr(C)]
#[derive(Copy, Clone, Debug, Pod, Zeroable)]
pub struct Vertex {
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub color: [f32; 3],
}

impl Vertex {
    const ATTRS: [wgpu::VertexAttribute; 3] =
        wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3, 2 => Float32x3];

    fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<Vertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRS,
        }
    }
}

impl OverlayVertex {
    const ATTRS: [wgpu::VertexAttribute; 2] =
        wgpu::vertex_attr_array![0 => Float32x2, 1 => Float32x4];

    fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<OverlayVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRS,
        }
    }
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Pod, Zeroable)]
struct CameraUniform {
    view_proj: [[f32; 4]; 4],
}

/// How many body vertices the dynamic buffer can hold.
///
/// 16 players (the server's `MAX_PLAYERS`) × 6 box faces × 6 vertices = 576, and
/// this leaves generous room. Sized once at startup so the per-frame path never
/// allocates; overflow is **truncated rather than grown**, because a frame that
/// silently reallocates is a frame that stutters, and dropping the seventeenth
/// body is not a thing anyone can see.
const MAX_BODY_VERTS: usize = 4096;

/// How many view-model vertices the weapon buffer can hold.
///
/// The largest weapon is a couple of hundred triangles of boxes and cylinders,
/// plus a five-sided muzzle flare. Sized once, like the bodies, so the per-frame
/// path never allocates.
const MAX_VIEWMODEL_VERTS: usize = 4096;

/// How many overlay vertices the HUD can hold.
///
/// The HUD is drawn as geometry, and its font is one quad per *lit pixel* — a
/// full kill feed plus every counter is a few thousand quads at worst. Generous,
/// and still under a megabyte.
const MAX_OVERLAY_VERTS: usize = 65536;

/// The view model's own near plane. It lives about 1.4 cubes from the eye and a
/// stock can reach half that, so the world's 0.05 is fine — but this pass has
/// its own depth range and no reason to spend it out at 2000.
const VIEWMODEL_NEAR: f32 = 0.02;
const VIEWMODEL_FAR: f32 = 50.0;

pub const DEPTH_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Depth32Float;

pub struct Renderer {
    surface: wgpu::Surface<'static>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    pipeline: wgpu::RenderPipeline,
    camera_buffer: wgpu::Buffer,
    camera_bind_group: wgpu::BindGroup,
    depth_view: wgpu::TextureView,
    world_buffer: wgpu::Buffer,
    world_verts: u32,
    body_buffer: wgpu::Buffer,
    body_verts: u32,
    /// The weapon in your hands, in **camera space**. Its own uniform, because
    /// the pass that draws it uses a different projection and an identity view.
    viewmodel_buffer: wgpu::Buffer,
    viewmodel_verts: u32,
    viewmodel_camera_buffer: wgpu::Buffer,
    viewmodel_bind_group: wgpu::BindGroup,
    overlay_pipeline: wgpu::RenderPipeline,
    overlay_buffer: wgpu::Buffer,
    overlay_verts: u32,
    pub backend: String,
    pub adapter_name: String,
}

impl Renderer {
    /// Stand the device up and upload the world.
    ///
    /// Async because `wgpu`'s adapter and device requests are; the caller blocks
    /// on it once at startup with `pollster`. Bringing a whole async runtime in
    /// for two awaits that happen once would be the tail wagging the dog.
    pub async fn new(window: Arc<Window>, mesh: &MeshData) -> Result<Renderer, String> {
        let size = window.inner_size();
        // `new_without_display_handle` rather than a `Default`, which wgpu 30
        // does not provide: the display handle is only required on GLES/Wayland,
        // and the surface is created from the window itself below.
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor::new_without_display_handle());
        let surface = instance
            .create_surface(window.clone())
            .map_err(|e| format!("no drawing surface: {e}"))?;

        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                compatible_surface: Some(&surface),
                force_fallback_adapter: false,
                ..Default::default()
            })
            .await
            .map_err(|e| format!("no usable GPU: {e}"))?;

        let info = adapter.get_info();
        let (device, queue) = adapter
            .request_device(&wgpu::DeviceDescriptor {
                label: Some("hassault"),
                // Nothing exotic is needed, and asking for more than the scene
                // uses is how a client refuses to start on a perfectly capable
                // integrated GPU.
                required_features: wgpu::Features::empty(),
                required_limits: wgpu::Limits::downlevel_defaults()
                    .using_resolution(adapter.limits()),
                ..Default::default()
            })
            .await
            .map_err(|e| format!("could not open the GPU: {e}"))?;

        let caps = surface.get_capabilities(&adapter);
        // Prefer an sRGB target: the mesher's colours are computed in sRGB-ish
        // space, and presenting them to a linear target washes the whole map out.
        let format = caps
            .formats
            .iter()
            .copied()
            .find(|f| f.is_srgb())
            .unwrap_or(caps.formats[0]);

        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format,
            // Let the driver decide: the mesher's colours are ordinary sRGB and
            // there is no HDR content to opt into.
            color_space: wgpu::SurfaceColorSpace::Auto,
            width: size.width.max(1),
            height: size.height.max(1),
            // **The latency decision, and the point of a native client.**
            // `Immediate` presents without waiting for vblank: it tears, and it
            // is the lowest-latency mode there is. `Mailbox` is the tear-free
            // fallback that still does not block. `Fifo` (vsync) is last because
            // it adds up to a frame of input lag, which is exactly what this
            // client exists to avoid — but it is always supported, so it is the
            // guaranteed floor.
            present_mode: pick_present_mode(&caps.present_modes),
            alpha_mode: caps.alpha_modes[0],
            view_formats: vec![],
            // One frame in flight. The default is two, which is the right answer
            // for throughput and the wrong one here: every queued frame is a
            // frame of input latency, and latency is the reason this client
            // exists.
            desired_maximum_frame_latency: 1,
        };
        surface.configure(&device, &config);

        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("hassault-shader"),
            source: wgpu::ShaderSource::Wgsl(include_str!("shader.wgsl").into()),
        });

        let camera_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("camera"),
            contents: bytemuck::cast_slice(&[CameraUniform {
                view_proj: glam::Mat4::IDENTITY.to_cols_array_2d(),
            }]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });

        let camera_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("camera-layout"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::VERTEX,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Uniform,
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            }],
        });

        let camera_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("camera-bind-group"),
            layout: &camera_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: camera_buffer.as_entire_binding(),
            }],
        });

        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("hassault-layout"),
            // wgpu 30 takes optional slots, so an unused group can be a hole
            // rather than forcing every layout to be contiguous.
            bind_group_layouts: &[Some(&camera_layout)],
            immediate_size: 0,
        });

        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("hassault-pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_main"),
                buffers: &[Some(Vertex::layout())],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_main"),
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
                // The mesher winds every quad counter-clockwise as seen from the
                // open side, which is precisely what makes back-face culling free
                // here: every surface exists once and faces the space you can
                // stand in, so there is nothing to draw on the far side.
                front_face: wgpu::FrontFace::Ccw,
                cull_mode: Some(wgpu::Face::Back),
                polygon_mode: wgpu::PolygonMode::Fill,
                unclipped_depth: false,
                conservative: false,
            },
            depth_stencil: Some(wgpu::DepthStencilState {
                format: DEPTH_FORMAT,
                depth_write_enabled: Some(true),
                depth_compare: Some(wgpu::CompareFunction::Less),
                stencil: wgpu::StencilState::default(),
                bias: wgpu::DepthBiasState::default(),
            }),
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });

        let vertices = mesh_vertices(mesh);
        let world_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("world"),
            contents: bytemuck::cast_slice(&vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });

        let body_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("bodies"),
            size: (MAX_BODY_VERTS * std::mem::size_of::<Vertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let viewmodel_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("viewmodel"),
            size: (MAX_VIEWMODEL_VERTS * std::mem::size_of::<Vertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let viewmodel_camera_buffer =
            device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("viewmodel-camera"),
                contents: bytemuck::cast_slice(&[CameraUniform {
                    view_proj: glam::Mat4::IDENTITY.to_cols_array_2d(),
                }]),
                usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            });

        let viewmodel_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("viewmodel-bind-group"),
            layout: &camera_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: viewmodel_camera_buffer.as_entire_binding(),
            }],
        });

        // The HUD's own pipeline: no camera, no depth, and **alpha blending**,
        // which is the one state that differs from everything else drawn here.
        // Without it a panel behind the kill feed is opaque black rather than a
        // wash, and a fading note vanishes at full brightness instead.
        let overlay_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("overlay-layout"),
            bind_group_layouts: &[],
            immediate_size: 0,
        });

        let overlay_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("overlay-pipeline"),
            layout: Some(&overlay_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_overlay"),
                buffers: &[Some(OverlayVertex::layout())],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_overlay"),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                strip_index_format: None,
                front_face: wgpu::FrontFace::Ccw,
                // **No culling.** The world and the bodies are closed shapes
                // wound outward; the HUD is flat quads, and a quad has no
                // outside. Culling here would drop whichever half of the layout
                // happened to be wound the other way, silently.
                cull_mode: None,
                polygon_mode: wgpu::PolygonMode::Fill,
                unclipped_depth: false,
                conservative: false,
            },
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });

        let overlay_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("overlay"),
            size: (MAX_OVERLAY_VERTS * std::mem::size_of::<OverlayVertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let depth_view = create_depth_view(&device, &config);

        Ok(Renderer {
            surface,
            device,
            queue,
            config,
            pipeline,
            camera_buffer,
            camera_bind_group,
            depth_view,
            world_buffer,
            world_verts: vertices.len() as u32,
            body_buffer,
            body_verts: 0,
            viewmodel_buffer,
            viewmodel_verts: 0,
            viewmodel_camera_buffer,
            viewmodel_bind_group,
            overlay_pipeline,
            overlay_buffer,
            overlay_verts: 0,
            backend: format!("{:?}", info.backend),
            adapter_name: info.name,
        })
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        if width == 0 || height == 0 {
            // A minimised window reports zero. Configuring a zero-sized surface
            // is a validation error, and the frame we would skip is one nobody
            // can see anyway.
            return;
        }
        self.config.width = width;
        self.config.height = height;
        self.surface.configure(&self.device, &self.config);
        // The depth texture is sized to the surface, so it has to be rebuilt
        // with it — a stale one is a validation error on the next pass.
        self.depth_view = create_depth_view(&self.device, &self.config);
    }

    pub fn size(&self) -> (u32, u32) {
        (self.config.width, self.config.height)
    }

    /// Replace the body geometry for this frame.
    pub fn set_bodies(&mut self, vertices: &[Vertex]) {
        let count = vertices.len().min(MAX_BODY_VERTS);
        self.body_verts = count as u32;
        if count > 0 {
            self.queue.write_buffer(
                &self.body_buffer,
                0,
                bytemuck::cast_slice(&vertices[..count]),
            );
        }
    }

    /// Replace the weapon geometry for this frame.
    ///
    /// Already in camera space: `viewmodel.rs` applies the pivot's transform on
    /// the CPU, which is a few hundred matrix multiplies and saves a per-object
    /// uniform this renderer has no other use for.
    pub fn set_viewmodel(&mut self, vertices: &[Vertex]) {
        let count = vertices.len().min(MAX_VIEWMODEL_VERTS);
        self.viewmodel_verts = count as u32;
        if count > 0 {
            self.queue.write_buffer(
                &self.viewmodel_buffer,
                0,
                bytemuck::cast_slice(&vertices[..count]),
            );
        }
    }

    /// Replace the HUD geometry for this frame.
    pub fn set_overlay(&mut self, vertices: &[OverlayVertex]) {
        // Truncated rather than grown, like the bodies: a HUD that reallocated
        // mid-frame would stutter, and the vertices past the cap are the tail of
        // a kill feed nobody can read at that length anyway.
        let count = vertices.len().min(MAX_OVERLAY_VERTS);
        self.overlay_verts = count as u32;
        if count > 0 {
            self.queue.write_buffer(
                &self.overlay_buffer,
                0,
                bytemuck::cast_slice(&vertices[..count]),
            );
        }
    }

    /// Draw one frame.
    ///
    /// `Ok(false)` means the surface needs reconfiguring before the next attempt
    /// — routine on a resize or a monitor change, and **not** an error worth
    /// stopping for. wgpu 30 reports this as an enum from `get_current_texture`
    /// rather than as an error type, which is the more honest shape: "the frame
    /// did not happen" is not the same event as "the GPU is gone".
    pub fn render(&mut self, camera: &Camera) -> Result<bool, String> {
        let vp = camera.view_projection(self.config.width, self.config.height);
        self.queue.write_buffer(
            &self.camera_buffer,
            0,
            bytemuck::cast_slice(&[CameraUniform {
                view_proj: vp.to_cols_array_2d(),
            }]),
        );

        // The view model's projection, rebuilt with the window: its view matrix
        // is the identity, because its vertices *are* camera space. That is the
        // whole of "parented to the camera" without a scene graph to do it.
        self.queue.write_buffer(
            &self.viewmodel_camera_buffer,
            0,
            bytemuck::cast_slice(&[CameraUniform {
                view_proj: viewmodel_projection(camera.fov, self.config.width, self.config.height)
                    .to_cols_array_2d(),
            }]),
        );

        use wgpu::CurrentSurfaceTexture as Cst;
        let frame = match self.surface.get_current_texture() {
            Cst::Success(frame) => frame,
            // Acquired, but the surface has moved on. Draw it anyway — a
            // slightly stale frame beats a dropped one — and reconfigure after.
            Cst::Suboptimal(frame) => {
                self.reconfigure();
                frame
            }
            Cst::Outdated | Cst::Lost => {
                self.reconfigure();
                return Ok(false);
            }
            // Minimised or behind another window: there is nothing to draw to,
            // and spinning on it would burn a core for no pixels.
            Cst::Occluded | Cst::Timeout => return Ok(false),
            Cst::Validation => return Err("the surface rejected this frame".into()),
        };
        let view = frame
            .texture
            .create_view(&wgpu::TextureViewDescriptor::default());
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("frame"),
            });

        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("main"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        // The fog colour, so geometry fading into the distance
                        // meets a matching background rather than a hard edge
                        // against the void.
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: 0.02,
                            g: 0.024,
                            b: 0.035,
                            a: 1.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.depth_view,
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

            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &self.camera_bind_group, &[]);
            pass.set_vertex_buffer(0, self.world_buffer.slice(..));
            pass.draw(0..self.world_verts, 0..1);
            if self.body_verts > 0 {
                pass.set_vertex_buffer(0, self.body_buffer.slice(..));
                pass.draw(0..self.body_verts, 0..1);
            }
        }

        if self.viewmodel_verts > 0 {
            // A second pass purely to **clear the depth buffer**. A weapon is
            // two and a half cubes long and lives inside the player's own
            // collision radius, so drawn against the world's depth it is sawn in
            // half by every wall you stand near — and it keeps its own depth
            // sorting, so the barrel still occludes the magazine behind it.
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("viewmodel"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Load,
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.depth_view,
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
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &self.viewmodel_bind_group, &[]);
            pass.set_vertex_buffer(0, self.viewmodel_buffer.slice(..));
            pass.draw(0..self.viewmodel_verts, 0..1);
        }

        if self.overlay_verts > 0 {
            // Last, over everything, with no depth attachment at all: the HUD is
            // not in the world and has nothing to be behind.
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("overlay"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Load,
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            pass.set_pipeline(&self.overlay_pipeline);
            pass.set_vertex_buffer(0, self.overlay_buffer.slice(..));
            pass.draw(0..self.overlay_verts, 0..1);
        }

        self.queue.submit(Some(encoder.finish()));
        // Presented through the queue in wgpu 30, not off the texture.
        self.queue.present(frame);
        Ok(true)
    }

    fn reconfigure(&mut self) {
        self.surface.configure(&self.device, &self.config);
        self.depth_view = create_depth_view(&self.device, &self.config);
    }
}

/// Lowest latency first. See the `present_mode` comment above.
fn pick_present_mode(available: &[wgpu::PresentMode]) -> wgpu::PresentMode {
    for wanted in [
        wgpu::PresentMode::Immediate,
        wgpu::PresentMode::Mailbox,
        wgpu::PresentMode::Fifo,
    ] {
        if available.contains(&wanted) {
            return wanted;
        }
    }
    // Always supported by the spec, so this is unreachable in practice.
    wgpu::PresentMode::Fifo
}

/// The view model's projection.
///
/// **The camera's own field of view**, not a narrower one. Many shooters give a
/// view model its own FOV, but this one is a port: three.js draws the browser's
/// weapon through the single scene camera, so a different angle here would make
/// the same weapon at the same `HOME` a different size on screen — which is the
/// one thing the port exists not to do. Only the depth range differs, and only
/// because a weapon 1.4 cubes away has no use for a far plane at 2000.
fn viewmodel_projection(fov_degrees: f32, width: u32, height: u32) -> glam::Mat4 {
    let aspect = if height == 0 {
        1.0
    } else {
        (width.max(1) as f32) / (height as f32)
    };
    // `directx`, matching `camera.rs` — 0..1 depth, Y-up. `vulkan` is also 0..1
    // and Y-*down*, which would hang the weapon off the top of the screen.
    glam::camera::rh::proj::directx::perspective(
        fov_degrees.to_radians(),
        aspect,
        VIEWMODEL_NEAR,
        VIEWMODEL_FAR,
    )
}

fn create_depth_view(
    device: &wgpu::Device,
    config: &wgpu::SurfaceConfiguration,
) -> wgpu::TextureView {
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("depth"),
        size: wgpu::Extent3d {
            width: config.width.max(1),
            height: config.height.max(1),
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: DEPTH_FORMAT,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
        view_formats: &[],
    });
    texture.create_view(&wgpu::TextureViewDescriptor::default())
}

/// Interleave the mesher's parallel arrays into what the GPU wants.
///
/// The mesher emits three separate `Vec<f32>` because that keeps it free of any
/// renderer type and testable with no device. This is the one place that costs
/// anything, and it runs once per map load.
pub fn mesh_vertices(mesh: &MeshData) -> Vec<Vertex> {
    let count = mesh.positions.len() / 3;
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        out.push(Vertex {
            position: [
                mesh.positions[i * 3],
                mesh.positions[i * 3 + 1],
                mesh.positions[i * 3 + 2],
            ],
            normal: [
                mesh.normals[i * 3],
                mesh.normals[i * 3 + 1],
                mesh.normals[i * 3 + 2],
            ],
            color: [
                mesh.colors[i * 3],
                mesh.colors[i * 3 + 1],
                mesh.colors[i * 3 + 2],
            ],
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn present_modes_are_chosen_lowest_latency_first() {
        use wgpu::PresentMode::*;
        assert_eq!(pick_present_mode(&[Fifo, Mailbox, Immediate]), Immediate);
        assert_eq!(pick_present_mode(&[Fifo, Mailbox]), Mailbox);
        // Vsync last, but always available — the guaranteed floor.
        assert_eq!(pick_present_mode(&[Fifo]), Fifo);
        assert_eq!(pick_present_mode(&[]), Fifo);
    }

    #[test]
    fn a_vertex_is_laid_out_the_way_the_shader_reads_it() {
        // The shader indexes by byte offset. If `repr(C)` were ever dropped,
        // Rust would be free to reorder these and the colours would arrive in
        // the normal's slot with no error anywhere.
        assert_eq!(std::mem::size_of::<Vertex>(), 9 * 4);
        let v = Vertex {
            position: [1.0, 2.0, 3.0],
            normal: [4.0, 5.0, 6.0],
            color: [7.0, 8.0, 9.0],
        };
        let bytes: &[f32] = bytemuck::cast_slice(std::slice::from_ref(&v));
        assert_eq!(bytes, &[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]);
    }

    #[test]
    fn an_overlay_vertex_is_laid_out_the_way_the_shader_reads_it() {
        assert_eq!(std::mem::size_of::<OverlayVertex>(), 6 * 4);
        let v = OverlayVertex {
            position: [1.0, 2.0],
            color: [3.0, 4.0, 5.0, 6.0],
        };
        let floats: &[f32] = bytemuck::cast_slice(std::slice::from_ref(&v));
        assert_eq!(floats, &[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]);
    }

    #[test]
    fn the_view_model_projection_keeps_the_weapon_in_front_of_the_eye() {
        // Camera space looks down -Z. A point in front of the eye has to land
        // inside the 0..1 depth range; the `vulkan` convention would put it
        // there too but upside down, so the y sign is the half worth pinning.
        let m = viewmodel_projection(75.0, 1280, 800);
        let p = m * glam::Vec4::new(0.0, -0.5, -1.35, 1.0);
        assert!(p.w > 0.0, "the weapon fell behind the eye");
        let ndc = p / p.w;
        assert!((0.0..=1.0).contains(&ndc.z), "depth {ndc:?}");
        assert!(ndc.y < 0.0, "a weapon below the sight line drew above it");
    }

    #[test]
    fn interleaving_preserves_every_vertex() {
        let mesh = MeshData {
            positions: vec![0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
            normals: vec![0.0, 1.0, 0.0, 0.0, 1.0, 0.0],
            colors: vec![0.5, 0.5, 0.5, 0.25, 0.25, 0.25],
            triangles: 0,
        };
        let verts = mesh_vertices(&mesh);
        assert_eq!(verts.len(), 2);
        assert_eq!(verts[1].position, [3.0, 4.0, 5.0]);
        assert_eq!(verts[1].color, [0.25, 0.25, 0.25]);
    }
}
