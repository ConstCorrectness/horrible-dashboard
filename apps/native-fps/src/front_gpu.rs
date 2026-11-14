//! The front end's renderer: one window, one pass, the overlay pipeline.
//!
//! The match's `Renderer` is a scene: shadow maps, a depth buffer, a post
//! chain, every weapon and map texture resident. It takes a world to build and
//! a moment to start, and none of it is wanted by a screen that is rectangles
//! and text. So the menu draws with this instead — the same `OverlayGpu` the
//! HUD is drawn with, pointed straight at the window — and is on screen before
//! anything has been fetched.
//!
//! **One surface per window, so this is dropped before a match starts.** The
//! shell hands the window to the match's renderer and takes it back when the
//! match is over; the two never hold it at once.

use std::sync::Arc;

use winit::window::Window;

use crate::gpu::GpuChoice;
use crate::hud::OverlayVertex;
use crate::map_art::{ArtGpu, ArtVertex, Atlas};
use crate::overlay_gpu::OverlayGpu;
use crate::text_kit::AtlasUpload;

/// As many as the match's HUD is allowed. The loading screen's floor plan is
/// the only thing here that gets near it, and it caps itself well below.
const MAX_VERTS: usize = 65536;

pub struct FrontGpu {
    surface: wgpu::Surface<'static>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    overlay: OverlayGpu,
    /// The map art, drawn under the overlay. Empty until `set_art`.
    art: ArtGpu,
    /// The adapter this is drawing on, for the log line.
    pub adapter_name: String,
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
        let overlay = OverlayGpu::new(&device, format, MAX_VERTS);
        let art = ArtGpu::new(&device, format);
        Ok(FrontGpu {
            surface,
            device,
            queue,
            config,
            overlay,
            art,
            adapter_name,
        })
    }

    /// Hand over the decoded map art. Once per `FrontGpu`: a new one, after
    /// a match, is a new device with nothing on it.
    pub fn set_art(&mut self, atlas: &Atlas) {
        self.art.upload(&self.device, &self.queue, atlas);
    }

    pub fn has_art(&self) -> bool {
        self.art.ready()
    }

    pub fn size(&self) -> (u32, u32) {
        (self.config.width, self.config.height)
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        // A minimised window reports zero, and a zero-sized surface is a
        // validation error for a frame nobody can see.
        if width == 0 || height == 0 {
            return;
        }
        self.config.width = width;
        self.config.height = height;
        self.surface.configure(&self.device, &self.config);
    }

    /// Draw one frame of overlay geometry.
    ///
    /// `Ok(false)` is a frame that did not happen — minimised, occluded, or a
    /// surface that has moved on — which is routine and not worth stopping for.
    pub fn draw(
        &mut self,
        vertices: &[OverlayVertex],
        atlas: Option<&AtlasUpload>,
        art: &[ArtVertex],
    ) -> Result<bool, String> {
        use wgpu::CurrentSurfaceTexture as Cst;
        self.overlay.set_vertices(&self.queue, vertices);
        self.art.set_vertices(&self.queue, art);
        if let Some(upload) = atlas {
            self.overlay.upload_atlas(&self.queue, upload);
        }
        let (frame, suboptimal) = match self.surface.get_current_texture() {
            Cst::Success(frame) => (frame, false),
            // Reconfigured once presented, never while held — see `Renderer::render`.
            Cst::Suboptimal(frame) => (frame, true),
            Cst::Outdated | Cst::Lost => {
                self.surface.configure(&self.device, &self.config);
                return Ok(false);
            }
            Cst::Occluded | Cst::Timeout => return Ok(false),
            Cst::Validation => return Err("the surface rejected this frame".into()),
        };
        let view = frame
            .texture
            .create_view(&wgpu::TextureViewDescriptor::default());
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("front"),
            });
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("front-overlay"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        // The front end paints its own backdrop over the whole
                        // window; black is what shows if it ever does not.
                        load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            // Pictures first, then everything that is drawn over them.
            self.art.draw(&mut pass);
            self.overlay.draw(&mut pass);
        }
        self.queue.submit([encoder.finish()]);
        self.queue.present(frame);
        if suboptimal {
            self.surface.configure(&self.device, &self.config);
        }
        Ok(true)
    }
}
