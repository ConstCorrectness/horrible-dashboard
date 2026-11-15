//! The front end's renderer: one window, the overlay, the pictures and the stage.
//!
//! **Two ways to own the window.** Flat, it opens its own device and draws the
//! menu straight onto the window: rectangles, text, the map art and the stage,
//! on screen before anything has been fetched. Live, it holds the match's
//! `Renderer` with a map in it, flies a camera through that map behind the
//! menu (`backdrop.rs`), and draws the same passes into each finished frame
//! (`Renderer::render_with`). The shell starts flat and goes live once a map
//! has loaded; nothing above `draw` knows which it is.
//!
//! The one thing here that is not rectangles and text is the **stage**
//! (`showcase_gpu.rs`): a weapon or an operator in three dimensions, drawn into
//! a small target of its own and placed over the overlay as a picture. It is
//! built with the device and costs nothing until a model is handed to it.
//!
//! **One surface per window, so this is dropped before a match starts.** The
//! shell hands the window to the match's renderer and takes it back when the
//! match is over; the two never hold it at once.

use std::sync::Arc;

use winit::window::Window;

use crate::camera::Camera;
use crate::gpu::GpuChoice;
use crate::hud::OverlayVertex;
use crate::map_art::{ArtGpu, ArtVertex, Atlas};
use crate::overlay_gpu::OverlayGpu;
use crate::renderer::Renderer;
use crate::showcase::Showcase;
use crate::showcase_gpu::ShowcaseGpu;
use crate::text_kit::AtlasUpload;

/// As many as the match's HUD is allowed. The loading screen's floor plan is
/// the only thing here that gets near it, and it caps itself well below.
const MAX_VERTS: usize = 65536;

/// Where the stage goes this frame, as `(x, y, w, h)` in pixels, and how its
/// subject has been turned.
pub type StagePlace<'a> = ((f32, f32, f32, f32), &'a Showcase);

/// What the menu draws, on whichever device it was given.
pub struct FrontPasses {
    overlay: OverlayGpu,
    /// Live, the dark laid over the map: its own pass, under the pictures,
    /// because the map tiles are drawn before the overlay and a scrim painted
    /// in the overlay would darken them with the world.
    scrim: OverlayGpu,
    /// The map art, drawn under the overlay. Empty until `set_art`.
    art: ArtGpu,
    /// The 3D stage. Public so the shell can hand it models as they load.
    pub stage: ShowcaseGpu,
}

impl FrontPasses {
    pub fn new(device: &wgpu::Device, queue: &wgpu::Queue, format: wgpu::TextureFormat) -> Self {
        FrontPasses {
            overlay: OverlayGpu::new(device, format, MAX_VERTS),
            scrim: OverlayGpu::new(device, format, 6),
            art: ArtGpu::new(device, format),
            stage: ShowcaseGpu::new(device, queue, format),
        }
    }

    /// One frame of the menu into `view`. `clear` paints the window black
    /// first — flat, where the menu is all there is; live, the map is already
    /// on it and is left alone.
    #[allow(clippy::too_many_arguments)]
    fn encode(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        encoder: &mut wgpu::CommandEncoder,
        view: &wgpu::TextureView,
        size: (u32, u32),
        frame: &MenuFrame<'_>,
        clear: bool,
    ) {
        self.overlay.set_vertices(queue, frame.vertices);
        if !clear {
            let shade = [0.0, 0.0, 0.0, frame.dim.clamp(0.0, 1.0)];
            let [a, b, c, d] = [[-1.0, 1.0], [1.0, 1.0], [1.0, -1.0], [-1.0, -1.0]];
            let quad = [a, b, c, a, c, d].map(|p| OverlayVertex::solid(p, shade));
            self.scrim.set_vertices(queue, &quad);
        }
        self.art.set_vertices(queue, frame.art);
        if let Some(upload) = frame.atlas {
            self.overlay.upload_atlas(queue, upload);
        }
        if let Some((rect, show)) = frame.stage {
            // Into its own target, before the window's pass begins.
            self.stage
                .render(device, queue, encoder, show, rect.2, rect.3, frame.dt);
        }
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("front-overlay"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view,
                depth_slice: None,
                resolve_target: None,
                ops: wgpu::Operations {
                    // The front end paints its own backdrop over the whole
                    // window; black is what shows if it ever does not.
                    load: if clear {
                        wgpu::LoadOp::Clear(wgpu::Color::BLACK)
                    } else {
                        wgpu::LoadOp::Load
                    },
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: None,
            timestamp_writes: None,
            occlusion_query_set: None,
            multiview_mask: None,
        });
        if !clear {
            self.scrim.draw(&mut pass);
        }
        // Pictures first, then everything that is drawn over them.
        self.art.draw(&mut pass);
        self.overlay.draw(&mut pass);
        // The stage last: it sits in a part of the panel the overlay leaves
        // clear, and anything drawn after it would be on top of a weapon
        // somebody is trying to look at.
        if let Some((rect, _)) = frame.stage {
            self.stage.place(&mut pass, rect, size);
        }
    }
}

/// One frame's worth of what the menu draws.
pub struct MenuFrame<'a> {
    pub vertices: &'a [OverlayVertex],
    pub atlas: Option<&'a AtlasUpload>,
    pub art: &'a [ArtVertex],
    pub stage: Option<StagePlace<'a>>,
    pub dt: f32,
    /// Live only: how much of the map is darkened away, 0 to 1.
    pub dim: f32,
}

/// Who owns the window.
enum Target {
    Flat {
        surface: wgpu::Surface<'static>,
        device: wgpu::Device,
        queue: wgpu::Queue,
        config: wgpu::SurfaceConfiguration,
    },
    Live {
        renderer: Box<Renderer>,
        camera: Camera,
    },
}

pub struct FrontGpu {
    target: Target,
    pub passes: FrontPasses,
    /// The adapter this is drawing on, for the log line.
    pub adapter_name: String,
    /// The map drawn behind the menu, when live.
    pub live_map: Option<String>,
}

impl FrontGpu {
    /// Open the same GPU the match will: the player's saved adapter and API.
    /// A menu on the integrated chip that hands over to a match on the discrete
    /// one would move the window between two devices mid-session.
    pub async fn new(window: Arc<Window>, gpu: &GpuChoice) -> Result<FrontGpu, String> {
        let size = window.inner_size();
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: gpu.backend.backends(),
            ..wgpu::InstanceDescriptor::new_without_display_handle()
        });
        let surface = instance
            .create_surface(window)
            .map_err(|e| format!("no drawing surface: {e}"))?;
        let adapter = crate::gpu::pick_adapter(&instance, &surface, gpu).await?;
        let adapter_name = adapter.get_info().name;
        let (device, queue) = adapter
            .request_device(&wgpu::DeviceDescriptor {
                label: Some("hassault-front"),
                required_limits: wgpu::Limits::downlevel_defaults()
                    .using_resolution(adapter.limits()),
                ..Default::default()
            })
            .await
            .map_err(|e| format!("could not open the GPU: {e}"))?;

        let caps = surface.get_capabilities(&adapter);
        // sRGB, as the match prefers: the palette is authored for it, and a
        // menu in a different colour space from the HUD it hands over to would
        // visibly shift on the first frame of the match.
        let format = caps
            .formats
            .iter()
            .copied()
            .find(|f| f.is_srgb())
            .unwrap_or(caps.formats[0]);
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format,
            color_space: wgpu::SurfaceColorSpace::Auto,
            width: size.width.max(1),
            height: size.height.max(1),
            // Synced, unlike the match. A menu has no input latency worth a
            // torn frame, and an uncapped one would spin the GPU at a thousand
            // frames a second to draw a screen that is not moving.
            present_mode: if caps.present_modes.contains(&wgpu::PresentMode::AutoVsync) {
                wgpu::PresentMode::AutoVsync
            } else {
                wgpu::PresentMode::Fifo
            },
            alpha_mode: caps.alpha_modes[0],
            view_formats: vec![],
            desired_maximum_frame_latency: 2,
        };
        surface.configure(&device, &config);
        let passes = FrontPasses::new(&device, &queue, format);
        Ok(FrontGpu {
            target: Target::Flat {
                surface,
                device,
                queue,
                config,
            },
            passes,
            adapter_name,
            live_map: None,
        })
    }

    /// The menu over a map: the match's renderer, already holding `map`'s
    /// world, with the menu's passes built on its device. The caller drops the
    /// flat one first — one surface per window.
    pub fn live(renderer: Renderer, map: &str) -> FrontGpu {
        let passes = FrontPasses::new(renderer.device(), renderer.queue(), renderer.format());
        FrontGpu {
            adapter_name: renderer.adapter_name.clone(),
            target: Target::Live {
                renderer: Box::new(renderer),
                camera: Camera::default(),
            },
            passes,
            live_map: Some(map.to_string()),
        }
    }

    /// The live renderer, to change its map or its light. `None` when flat.
    pub fn renderer(&mut self) -> Option<&mut Renderer> {
        match &mut self.target {
            Target::Live { renderer, .. } => Some(renderer),
            Target::Flat { .. } => None,
        }
    }

    /// Where the backdrop's eye is this frame. Ignored when flat.
    pub fn set_camera(&mut self, eye: Camera) {
        if let Target::Live { camera, .. } = &mut self.target {
            *camera = eye;
        }
    }

    /// Hand over the decoded map art. Once per `FrontGpu`: a new one, after
    /// a match, is a new device with nothing on it.
    pub fn set_art(&mut self, atlas: &Atlas) {
        let (device, queue) = match &self.target {
            Target::Flat { device, queue, .. } => (device, queue),
            Target::Live { renderer, .. } => (renderer.device(), renderer.queue()),
        };
        self.passes.art.upload(device, queue, atlas);
    }

    /// Upload a parsed weapon prop to the stage.
    pub fn add_prop(&mut self, id: &str, prop: &crate::prop::Prop) {
        let (device, queue) = match &self.target {
            Target::Flat { device, queue, .. } => (device, queue),
            Target::Live { renderer, .. } => (renderer.device(), renderer.queue()),
        };
        self.passes.stage.add_prop(device, queue, id, prop);
    }

    /// Upload both operators to the stage.
    pub fn add_operators(&mut self, squad: crate::animator::Squad) {
        let (device, queue) = match &self.target {
            Target::Flat { device, queue, .. } => (device, queue),
            Target::Live { renderer, .. } => (renderer.device(), renderer.queue()),
        };
        self.passes.stage.add_operators(device, queue, squad);
    }

    pub fn has_art(&self) -> bool {
        self.passes.art.ready()
    }

    pub fn size(&self) -> (u32, u32) {
        match &self.target {
            Target::Flat { config, .. } => (config.width, config.height),
            Target::Live { renderer, .. } => renderer.size(),
        }
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        // A minimised window reports zero, and a zero-sized surface is a
        // validation error for a frame nobody can see.
        if width == 0 || height == 0 {
            return;
        }
        match &mut self.target {
            Target::Flat {
                surface,
                device,
                config,
                ..
            } => {
                config.width = width;
                config.height = height;
                surface.configure(device, config);
            }
            Target::Live { renderer, .. } => renderer.resize(width, height),
        }
    }

    /// Draw one frame: the map behind it when live, the menu, the stage.
    ///
    /// `Ok(false)` is a frame that did not happen — minimised, occluded, or a
    /// surface that has moved on — which is routine and not worth stopping for.
    pub fn draw(&mut self, frame: MenuFrame<'_>) -> Result<bool, String> {
        let passes = &mut self.passes;
        match &mut self.target {
            Target::Live { renderer, camera } => {
                // The match's own HUD pass has nothing to draw here.
                renderer.set_overlay(&[]);
                renderer.render_with(camera, &mut |extra| {
                    passes.encode(
                        extra.device,
                        extra.queue,
                        extra.encoder,
                        extra.view,
                        extra.size,
                        &frame,
                        false,
                    );
                })
            }
            Target::Flat {
                surface,
                device,
                queue,
                config,
            } => {
                use wgpu::CurrentSurfaceTexture as Cst;
                let (texture, suboptimal) = match surface.get_current_texture() {
                    Cst::Success(t) => (t, false),
                    // Reconfigured once presented, never while held — see
                    // `Renderer::render`.
                    Cst::Suboptimal(t) => (t, true),
                    Cst::Outdated | Cst::Lost => {
                        surface.configure(device, config);
                        return Ok(false);
                    }
                    Cst::Occluded | Cst::Timeout => return Ok(false),
                    Cst::Validation => return Err("the surface rejected this frame".into()),
                };
                let view = texture
                    .texture
                    .create_view(&wgpu::TextureViewDescriptor::default());
                let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
                    label: Some("front"),
                });
                passes.encode(
                    device,
                    queue,
                    &mut encoder,
                    &view,
                    (config.width, config.height),
                    &frame,
                    true,
                );
                queue.submit([encoder.finish()]);
                queue.present(texture);
                if suboptimal {
                    surface.configure(device, config);
                }
                Ok(true)
            }
        }
    }
}
