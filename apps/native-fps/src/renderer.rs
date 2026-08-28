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
use crate::settings::Video;

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

/// A vertex of a translucent volume: smoke, fire.
///
/// A separate type from `Vertex` rather than a fourth field on it, because the
/// alpha is only ever needed by this one pass and widening `Vertex` would put
/// four bytes on every one of a map's ~200,000 world vertices to carry a number
/// that is always 1.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct VolumeVertex {
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub color: [f32; 4],
    /// Which shading this vertex wants: `MODE_CLOUD` or `MODE_FLAT`.
    ///
    /// An explicit field rather than an implicit signal — the tempting trick is
    /// a zero normal, since no sphere produces one — because the whole pass is
    /// shared by two things that look nothing alike, and a convention encoded in
    /// the *absence* of data is one nobody finds when it breaks. Four bytes on a
    /// buffer that holds a few thousand vertices is not a cost worth being
    /// clever about.
    pub mode: f32,
}

/// Volume shading: the noisy interior of a smoke or fire cloud.
pub const MODE_CLOUD: f32 = 0.0;
/// Volume shading: flat colour, for tracers, impacts and blast shells, which are
/// *thin* and would be mottled to nothing by the cloud noise.
pub const MODE_FLAT: f32 = 1.0;

impl VolumeVertex {
    const ATTRS: [wgpu::VertexAttribute; 4] =
        wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3, 2 => Float32x4, 3 => Float32];

    fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<VolumeVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRS,
        }
    }
}

/// How many volume vertices the cloud buffer can hold.
///
/// This buffer carries **both** the clouds and the effects, and the budget test
/// below has now moved it twice: 16384 → 32768 when a full team's smokes came to
/// 18,432, then → 65536 when tracers and impacts joined the same pass and a
/// shotgun volley from everyone at once put it past 48,000. Neither number was
/// wrong when it was written; both were wrong the moment something else started
/// sharing the buffer, which is exactly the failure `MAX_BODY_VERTS` had and
/// nobody noticed for a whole rewrite.
///
/// Overflow is reported (see `set_volumes`) rather than quietly dropping a smoke
/// somebody is standing behind.
const MAX_VOLUME_VERTS: usize = 65536;

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
    /// `[fog_density, detail, reveal_height, receives_shadow]` — the quality
    /// level, as the shader reads it. Packed into the camera's own buffer rather
    /// than given a second binding: a uniform buffer's minimum size is 16 bytes,
    /// so a few floats and some padding is exactly what a separate one would
    /// have cost. The last slot was that padding.
    params: [f32; 4],
    /// `[progress, centre_x, centre_y, radius]` for the build-in. See `Reveal`.
    ///
    /// Here, in the *camera's* uniform, for a reason that is the whole design:
    /// the world and the view model share one pipeline and one shader, and
    /// differ only in which camera bind group is bound. Putting the reveal here
    /// means the weapon in your hands is excluded from it by construction — its
    /// own uniform simply carries a finished reveal — rather than by a branch
    /// somebody has to remember to write.
    reveal: [f32; 4],
    /// Whatever takes a vertex of this draw into **world space, for lighting**.
    ///
    /// The identity for the world and the operators, whose vertices are already
    /// there. For the view model it is the camera-to-world matrix, because that
    /// pass's vertices are camera space and the sun, the fill and the shadow map
    /// are all world space — see `Camera::camera_to_world`.
    ///
    /// Deliberately *not* folded into `view_proj`: the two answer different
    /// questions (where does this land on screen, and where is this in the room),
    /// and the view model is the whole reason they are allowed to disagree.
    light_transform: [[f32; 4]; 4],
}

impl CameraUniform {
    fn new(view_proj: glam::Mat4, video: Video, reveal: crate::reveal::Reveal) -> CameraUniform {
        CameraUniform {
            view_proj: view_proj.to_cols_array_2d(),
            params: [
                video.quality.fog_density(),
                video.quality.detail(),
                reveal.height(),
                // Everything drawn in world space receives the sun's shadow,
                // unless the player has turned shadows off. The view model opts
                // out either way — see `attached_to`.
                if video.shadows { 1.0 } else { 0.0 },
            ],
            reveal: reveal.uniform(),
            light_transform: glam::Mat4::IDENTITY.to_cols_array_2d(),
        }
    }

    /// The same camera, for a pass whose vertices are **camera space**.
    ///
    /// Two things, and they belong together because they are the same fact about
    /// this pass. `camera_to_world` is what the lighting needs to shade a
    /// camera-space normal against a world-space sun. And the shadow is switched
    /// **off**, matching the browser: three only shadows a mesh with
    /// `receiveShadow` set, and `HorribleAssaultPanel.tsx` sets it on the map's
    /// mesh and on nothing else. Leaving it on here is not merely a difference —
    /// the weapon is a hand's width from the eye, so it crosses a shadow edge as
    /// a hard flicker across the whole model rather than as a shadow moving over
    /// something.
    fn attached_to(mut self, camera_to_world: glam::Mat4) -> CameraUniform {
        self.light_transform = camera_to_world.to_cols_array_2d();
        self.params[3] = 0.0;
        self
    }
}

/// How many body vertices the dynamic buffer can hold.
///
/// **This was 4096, and it was wrong.** The comment justifying that number read
/// "16 players × 6 box faces × 6 vertices = 576, generous room" — arithmetic
/// that was true when a body *was* a box. `bodies.rs` became an articulated
/// operator of some nineteen boxes, one body became **684** vertices, and the
/// cap silently stopped fitting six players. Nothing failed: `set_bodies`
/// truncates, so a full match simply stopped drawing its last players, and a
/// body cut off mid-torso is not a symptom anyone would trace to a buffer size.
///
/// The budget is now asserted from the real builders rather than estimated —
/// see `the_body_buffer_fits_a_full_match`. Sized once at startup so the
/// per-frame path never allocates.
const MAX_BODY_VERTS: usize = 32768;

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
    world_buffer: wgpu::Buffer,
    world_verts: u32,
    body_buffer: wgpu::Buffer,
    body_verts: u32,
    /// Smoke and fire: drawn in the main pass, after everything opaque, with
    /// blending on and depth writes off. See `volume_pipeline`.
    /// The build-in's clock, uploaded with the camera each frame.
    reveal: crate::reveal::Reveal,
    /// The same view projection with the build-in already **finished**.
    ///
    /// Bodies, grenades and hitbox wireframes are drawn with the world's
    /// pipeline and would otherwise rise out of the floor with the map. The
    /// browser reveals only the world material, and `skin.wgsl` declares a
    /// `Camera` without the reveal field at all — so the skinned operators were
    /// already exempt while the box fallback was not, which is two body paths
    /// behaving differently for no reason anyone chose.
    settled_camera_buffer: wgpu::Buffer,
    settled_bind_group: wgpu::BindGroup,
    volume_pipeline: wgpu::RenderPipeline,
    volume_buffer: wgpu::Buffer,
    volume_verts: u32,
    /// The weapon in your hands, in **camera space**. Its own uniform, because
    /// the pass that draws it uses a different projection and an identity view.
    /// The weapon props, and the camera they are drawn through.
    ///
    /// A **third** camera uniform, and it has to be: the prop's vertices are in
    /// its own model space, so its `view_proj` carries the view model's pose
    /// baked in and its `light_transform` carries that pose *and* the way back
    /// out to world space. The box view model's uniform has neither, because its
    /// vertices arrive already posed.
    props: crate::props_gpu::Props,
    /// This frame's prop pose, from `WeaponViewModel::prop_model`.
    prop_model: Option<glam::Mat4>,
    prop_camera_buffer: wgpu::Buffer,
    prop_bind_group: wgpu::BindGroup,
    viewmodel_buffer: wgpu::Buffer,
    viewmodel_verts: u32,
    viewmodel_camera_buffer: wgpu::Buffer,
    viewmodel_bind_group: wgpu::BindGroup,
    overlay_pipeline: wgpu::RenderPipeline,
    overlay_buffer: wgpu::Buffer,
    overlay_verts: u32,
    pub backend: String,
    pub adapter_name: String,
    /// Kept for one reason: switching vsync re-reads the surface's present
    /// modes, and `get_capabilities` needs the adapter that opened it.
    adapter: wgpu::Adapter,
    /// Kept so the pipelines can be rebuilt when the sample count changes.
    shader: wgpu::ShaderModule,
    camera_layout: wgpu::BindGroupLayout,
    /// The world pipeline's layout, **kept rather than rebuilt**.
    ///
    /// `set_video` used to construct a second one when the sample count changed,
    /// and the second one was wrong: it named `[camera, detail]` where this one
    /// names `[camera, detail, shadow]`. The pass binds the shadow map at group
    /// 2 either way, so the first frame after switching quality bound a group the
    /// pipeline's layout did not have — a wgpu validation error, which is a
    /// **panic**, not a darker picture. Selecting High crashed the client every
    /// time, and so did selecting anything else afterwards.
    ///
    /// A layout is immutable and independent of the multisample state, so there
    /// was never a reason to build a second one. Holding the single definition
    /// here is what makes the two paths unable to disagree again.
    world_layout: wgpu::PipelineLayout,
    /// The translucent-volume pipeline's layout, kept for the same reason.
    volume_layout: wgpu::PipelineLayout,
    detail_bind_group: wgpu::BindGroup,
    shadow: crate::shadow::ShadowMap,
    video: Video,
    /// Where the world is drawn: a texture at `render_scale` of the window, and
    /// multisampled at the quality level's count. The swapchain never sees the
    /// world directly any more — only this, scaled up by the blit.
    scene: SceneTargets,
    blit_pipeline: wgpu::RenderPipeline,
    blit_layout: wgpu::BindGroupLayout,
    blit_bind_group: wgpu::BindGroup,
    sampler: wgpu::Sampler,
    /// The skinned operator. `None` only if the asset failed to parse, which is
    /// reported once at startup and then falls back to `bodies.rs` rather than
    /// leaving the match with invisible players.
    characters: Option<crate::characters_gpu::Characters>,
}

/// The offscreen target the world is rendered into.
///
/// It exists for two settings that both need one: **render scale** (draw fewer
/// pixels than the window has) and **MSAA** (draw more samples than the window
/// has). The HUD is deliberately *not* drawn here — it goes onto the swapchain
/// after the blit, at native resolution, because 5×7 text upscaled from 50% is
/// unreadable and the HUD costs nothing to draw at full size.
struct SceneTargets {
    /// What the passes attach to: multisampled when the count is above 1.
    color: wgpu::TextureView,
    /// Where a multisampled pass resolves to. `None` at 1×, where `color` is
    /// already single-sampled and is what the blit samples.
    resolve: Option<wgpu::TextureView>,
    depth: wgpu::TextureView,
    width: u32,
    height: u32,
}

impl SceneTargets {
    /// The view the blit reads: the resolve target when there is one, otherwise
    /// the colour target itself. Sampling a multisampled texture directly is a
    /// different shader binding type — getting this wrong is a validation error,
    /// not a wrong-looking frame.
    fn sampled(&self) -> &wgpu::TextureView {
        self.resolve.as_ref().unwrap_or(&self.color)
    }
}

impl Renderer {
    /// Stand the device up and upload the world.
    ///
    /// Async because `wgpu`'s adapter and device requests are; the caller blocks
    /// on it once at startup with `pollster`. Bringing a whole async runtime in
    /// for two awaits that happen once would be the tail wagging the dog.
    pub async fn new(
        window: Arc<Window>,
        mesh: &MeshData,
        video: Video,
    ) -> Result<Renderer, String> {
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
            present_mode: pick_present_mode(&caps.present_modes, video.vsync),
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
            source: wgpu::ShaderSource::Wgsl(
                // Concatenated rather than imported: WGSL has no include, and the
                // lighting has to be one copy shared with the other shader.
                concat!(
                    include_str!("lighting.wgsl.inc"),
                    include_str!("shader.wgsl")
                )
                .into(),
            ),
        });

        let camera_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("camera"),
            contents: bytemuck::cast_slice(&[CameraUniform::new(
                glam::Mat4::IDENTITY,
                video,
                crate::reveal::Reveal::done(),
            )]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });

        let camera_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("camera-layout"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                // **Both stages.** The matrix is the vertex shader's and the
                // quality parameters are the fragment shader's, and they share a
                // buffer. A `VERTEX`-only visibility here is a validation error
                // at pipeline creation, not a wrong-looking frame.
                visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
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

        // The surface grain. Generated, uploaded once, and never touched again:
        // it is the same tile for every map, because it is a *material* rather
        // than a map's artwork. Built before the pipeline layout, which needs it.
        let detail_layout = crate::detail::bind_group_layout(&device);
        let detail_bind_group = crate::detail::bind_group(&device, &queue, &detail_layout);

        let vertices = mesh_vertices(mesh);
        let world_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("world"),
            contents: bytemuck::cast_slice(&vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });

        // Rendered here, once, from the map that was just uploaded. The sun and
        // the geometry are both static, so this never has to happen again — see
        // `shadow.rs`.
        let shadow = crate::shadow::ShadowMap::new(
            &device,
            &queue,
            &world_buffer,
            vertices.len() as u32,
            crate::shadow::bounds_of(&vertices),
        );

        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("hassault-layout"),
            // wgpu 30 takes optional slots, so an unused group can be a hole
            // rather than forcing every layout to be contiguous.
            bind_group_layouts: &[
                Some(&camera_layout),
                Some(&detail_layout),
                Some(&shadow.layout),
            ],
            immediate_size: 0,
        });

        let pipeline = world_pipeline(&device, &pipeline_layout, &shader, format, video.samples());
        let world_layout = pipeline_layout;

        let body_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("bodies"),
            size: (MAX_BODY_VERTS * std::mem::size_of::<Vertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let volume_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("volumes"),
            size: (MAX_VOLUME_VERTS * std::mem::size_of::<VolumeVertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        // Its own layout, carrying only the camera: this shader reads neither
        // the detail texture nor the shadow map, and a pipeline layout that
        // declared them would oblige every caller to keep those groups bound
        // for a pass that never looks at them.
        let volume_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("volume-layout"),
            bind_group_layouts: &[Some(&camera_layout)],
            immediate_size: 0,
        });
        let volume_pipeline =
            volume_pipeline(&device, &volume_layout, &shader, format, video.samples());

        let viewmodel_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("viewmodel"),
            size: (MAX_VIEWMODEL_VERTS * std::mem::size_of::<Vertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let settled_camera_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("settled-camera"),
            contents: bytemuck::cast_slice(&[CameraUniform::new(
                glam::Mat4::IDENTITY,
                video,
                crate::reveal::Reveal::done(),
            )]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });
        let settled_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("settled-bind-group"),
            layout: &camera_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: settled_camera_buffer.as_entire_binding(),
            }],
        });

        let viewmodel_camera_buffer =
            device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("viewmodel-camera"),
                contents: bytemuck::cast_slice(&[CameraUniform::new(
                    glam::Mat4::IDENTITY,
                    video,
                    crate::reveal::Reveal::done(),
                )]),
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

        let prop_camera_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("prop-camera"),
            contents: bytemuck::cast_slice(&[CameraUniform::new(
                glam::Mat4::IDENTITY,
                video,
                crate::reveal::Reveal::done(),
            )]),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });
        let prop_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("prop-bind-group"),
            layout: &camera_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: prop_camera_buffer.as_entire_binding(),
            }],
        });
        let props = crate::props_gpu::Props::new(&device, &camera_layout, format, video.samples());

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

        let scene = create_scene(&device, &config, video.render_scale, video.samples());

        // Linear, so a scaled-up frame is smoothed rather than blocky — nearest
        // at 50% looks like a rendering fault rather than a setting.
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("scene-sampler"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let blit_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("blit-layout"),
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
            ],
        });
        let blit_bind_group =
            create_blit_bind_group(&device, &blit_layout, scene.sampled(), &sampler);
        let blit_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("blit-pipeline"),
            layout: Some(
                &device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
                    label: Some("blit-pipeline-layout"),
                    bind_group_layouts: &[Some(&blit_layout)],
                    immediate_size: 0,
                }),
            ),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_blit"),
                // No vertex buffer at all: three vertices computed from the
                // index are cheaper than binding one.
                buffers: &[],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_blit"),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    blend: Some(wgpu::BlendState::REPLACE),
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

        Ok(Renderer {
            surface,
            device,
            queue,
            config,
            pipeline,
            camera_buffer,
            camera_bind_group,
            world_buffer,
            world_verts: vertices.len() as u32,
            body_buffer,
            body_verts: 0,
            reveal: crate::reveal::Reveal::done(),
            settled_camera_buffer,
            settled_bind_group,
            volume_pipeline,
            volume_buffer,
            volume_verts: 0,
            props,
            prop_model: None,
            prop_camera_buffer,
            prop_bind_group,
            viewmodel_buffer,
            viewmodel_verts: 0,
            viewmodel_camera_buffer,
            viewmodel_bind_group,
            overlay_pipeline,
            overlay_buffer,
            overlay_verts: 0,
            backend: format!("{:?}", info.backend),
            adapter_name: info.name.clone(),
            adapter,
            shader,
            camera_layout,
            world_layout,
            volume_layout,
            detail_bind_group,
            shadow,
            video,
            scene,
            blit_pipeline,
            blit_layout,
            blit_bind_group,
            sampler,
            characters: None,
        })
    }

    /// Upload the operator's geometry, textures and materials.
    ///
    /// Separate from `new` so the asset is **parsed once**: the CPU side needs
    /// the same `Operator` to pose from, and having the renderer load its own
    /// copy would mean two parses of an 8 MB file and two decodes of fourteen
    /// textures, one of each thrown away.
    pub fn install_characters(&mut self, operator: &crate::character::Operator) {
        self.characters = Some(crate::characters_gpu::Characters::new(
            &self.device,
            &self.queue,
            operator,
            &self.camera_layout,
            &self.shadow.layout,
            self.config.format,
            self.video.samples(),
        ));
    }

    /// Whether the skinned operator is available this run.
    ///
    /// The caller uses it to decide whether to also build the old box bodies —
    /// drawing both would put two overlapping characters on every player.
    pub fn has_characters(&self) -> bool {
        self.characters.is_some()
    }

    /// Upload this frame's operator poses.
    pub fn set_characters(&mut self, poses: &[crate::animator::ActorPose]) {
        if let Some(characters) = self.characters.as_mut() {
            characters.prepare(&self.queue, poses);
        }
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
        // The scene is sized *from* the surface, so it has to be rebuilt with
        // it — a stale attachment is a validation error on the next pass.
        self.rebuild_scene();
    }

    pub fn size(&self) -> (u32, u32) {
        (self.config.width, self.config.height)
    }

    /// Replace the body geometry for this frame.
    pub fn set_bodies(&mut self, vertices: &[Vertex]) {
        if vertices.len() > MAX_BODY_VERTS {
            // Truncation is still the behaviour — a frame that reallocated would
            // stutter — but it is no longer silent. This exact overflow went
            // unnoticed through a whole rewrite of `bodies.rs` because the only
            // symptom was a player not being there.
            crate::divergence::note_overflow("bodies", vertices.len(), MAX_BODY_VERTS);
        }
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

    /// Set the build-in's state for this frame. See `reveal.rs`.
    pub fn set_reveal(&mut self, reveal: crate::reveal::Reveal) {
        self.reveal = reveal;
    }

    /// Replace this frame's translucent volumes.
    pub fn set_volumes(&mut self, vertices: &[VolumeVertex]) {
        if vertices.len() > MAX_VOLUME_VERTS {
            crate::divergence::note_overflow("volumes", vertices.len(), MAX_VOLUME_VERTS);
        }
        let count = vertices.len().min(MAX_VOLUME_VERTS);
        self.volume_verts = count as u32;
        if count > 0 {
            self.queue.write_buffer(
                &self.volume_buffer,
                0,
                bytemuck::cast_slice(&vertices[..count]),
            );
        }
    }

    /// Upload a parsed weapon prop into the cache. It is drawn once `use_prop`
    /// picks it, which is a different event: props are preloaded off the frame
    /// thread and land in whatever order they finish parsing.
    pub fn set_prop(&mut self, weapon: &str, prop: &crate::prop::Prop) {
        self.props.set(&self.device, &self.queue, weapon, prop);
    }

    /// Draw this weapon's uploaded prop, reporting its bounds for the fit.
    /// `None` when it has not been uploaded — the boxes stay.
    pub fn use_prop(&mut self, weapon: &str) -> Option<(glam::Vec3, glam::Vec3)> {
        self.props.select(weapon)
    }

    /// Go back to the box model.
    pub fn clear_prop(&mut self) {
        self.props.clear();
    }

    /// Where the resident prop is drawn, in the view model's own space.
    ///
    /// `None` leaves the prop pass drawing nothing, which is what the boxes
    /// being the fallback means in practice.
    pub fn set_prop_model(&mut self, model: Option<glam::Mat4>) {
        self.prop_model = model;
    }

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
            bytemuck::cast_slice(&[CameraUniform::new(vp, self.video, self.reveal)]),
        );
        // The same camera, with the build-in already over. See
        // `settled_bind_group`.
        self.queue.write_buffer(
            &self.settled_camera_buffer,
            0,
            bytemuck::cast_slice(&[CameraUniform::new(
                vp,
                self.video,
                crate::reveal::Reveal::done(),
            )]),
        );

        // The view model's projection, rebuilt with the window: its view matrix
        // is the identity, because its vertices *are* camera space. That is the
        // whole of "parented to the camera" without a scene graph to do it — and
        // `attached_to` is the other half of that bargain, handing the shader the
        // way back out to world space so the lighting still knows where the eye
        // is standing.
        self.queue.write_buffer(
            &self.viewmodel_camera_buffer,
            0,
            bytemuck::cast_slice(&[CameraUniform::new(
                viewmodel_projection(camera.fov, self.config.width, self.config.height),
                self.video,
                // Always finished: the weapon in your hands does not rise out of
                // the floor with the map. This is the whole reason the reveal
                // lives in the camera's uniform rather than in one of its own.
                crate::reveal::Reveal::done(),
            )
            .attached_to(camera.camera_to_world())]),
        );

        // The prop's camera. Its vertices are in the model's own space, so the
        // view model's pose is folded into **both** matrices: into `view_proj`
        // to place it on screen, and into `light_transform` so a model-space
        // normal reaches world space through the same pose. Feeding the second
        // one without the pose gives a weapon whose shading ignores every kick,
        // bob and sway the first one applies — lit as though it never moved.
        let pose = self.prop_model.unwrap_or(glam::Mat4::IDENTITY);
        let projection = viewmodel_projection(camera.fov, self.config.width, self.config.height);
        self.queue.write_buffer(
            &self.prop_camera_buffer,
            0,
            bytemuck::cast_slice(&[CameraUniform::new(
                projection * pose,
                self.video,
                crate::reveal::Reveal::done(),
            )
            .attached_to(camera.camera_to_world() * pose)]),
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
                    view: &self.scene.color,
                    depth_slice: None,
                    // Resolving here rather than in the view-model pass would
                    // resolve a frame that is not finished; resolving in *both*
                    // would resolve twice. It belongs on the last pass that
                    // touches the scene, which is the view model's when there is
                    // one — see below.
                    resolve_target: if self.viewmodel_verts > 0 {
                        None
                    } else {
                        self.scene.resolve.as_ref()
                    },
                    ops: wgpu::Operations {
                        // The fog colour, so geometry fading into the distance
                        // meets a matching background rather than a hard edge
                        // against the void. `FOG_COLOR` in `lighting.wgsl.inc`,
                        // which is the browser's `0x11161f` horizon decoded to
                        // linear — this is written to an sRGB surface, so a raw
                        // hex here would come out three shades too pale.
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: 0.0056,
                            g: 0.0080,
                            b: 0.0137,
                            a: 1.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.scene.depth,
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
            pass.set_bind_group(1, &self.detail_bind_group, &[]);
            pass.set_bind_group(2, &self.shadow.bind_group, &[]);
            pass.set_vertex_buffer(0, self.world_buffer.slice(..));
            pass.draw(0..self.world_verts, 0..1);
            if self.body_verts > 0 {
                // Bodies, grenades and hitbox wireframes: the same pipeline, a
                // camera whose build-in is finished. A player standing in a map
                // that is still arriving does not arrive with it.
                pass.set_bind_group(0, &self.settled_bind_group, &[]);
                pass.set_vertex_buffer(0, self.body_buffer.slice(..));
                pass.draw(0..self.body_verts, 0..1);
                pass.set_bind_group(0, &self.camera_bind_group, &[]);
            }
            // After the world and the untextured bodies, into the same depth
            // buffer: an operator behind a wall is hidden by the wall, and the
            // hitbox wireframes still overlay it.
            if let Some(characters) = self.characters.as_ref() {
                // Settled, like the boxes above: `skin.wgsl` declares a
                // `Camera` without the reveal field, so the skinned path ignored
                // it either way — handing it the settled group makes the two
                // body paths agree explicitly rather than by omission.
                characters.draw(&mut pass, &self.settled_bind_group, &self.shadow.bind_group);
            }

            // Translucent volumes last, inside the *same* pass. Deliberately not
            // a pass of its own: a second one would have to take over resolving
            // the multisampled target, and that choice already moves between the
            // main and view-model passes depending on whether a weapon is drawn.
            // Adding a third claimant to it is how a frame ends up resolved
            // twice, or not at all.
            //
            // Last because they blend: everything opaque has to be in the colour
            // buffer already for a cloud to be blended *over* it.
            if self.volume_verts > 0 {
                pass.set_pipeline(&self.volume_pipeline);
                // Re-bound because the volume pipeline has its own layout, which
                // invalidates the groups the world pipeline had set.
                pass.set_bind_group(0, &self.camera_bind_group, &[]);
                pass.set_vertex_buffer(0, self.volume_buffer.slice(..));
                pass.draw(0..self.volume_verts, 0..1);
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
                    view: &self.scene.color,
                    depth_slice: None,
                    // The last pass into the scene, so this is where a
                    // multisampled target resolves.
                    resolve_target: self.scene.resolve.as_ref(),
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Load,
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &self.scene.depth,
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
            // The prop first, then the box geometry — which with a prop loaded
            // is only the muzzle flare, and has to land on top of the barrel it
            // comes out of.
            self.props.draw(&mut pass, &self.prop_bind_group);

            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &self.viewmodel_bind_group, &[]);
            // The same pipeline, so the same layout: group 1 must be bound even
            // though the weapon in your hands is one flat colour per face and
            // the grain on it is invisible at that scale.
            pass.set_bind_group(1, &self.detail_bind_group, &[]);
            pass.set_bind_group(2, &self.shadow.bind_group, &[]);
            pass.set_vertex_buffer(0, self.viewmodel_buffer.slice(..));
            pass.draw(0..self.viewmodel_verts, 0..1);
        }

        {
            // The scene, scaled into the window. Always drawn, even at 100%:
            // branching on "the scale happens to be 1" would give the common
            // case its own untested code path, and a full-screen textured
            // triangle is nothing next to the world it is copying.
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("blit"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        // Cleared rather than loaded: this covers every pixel,
                        // and a load would be reading a surface nothing wrote.
                        load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            pass.set_pipeline(&self.blit_pipeline);
            pass.set_bind_group(0, &self.blit_bind_group, &[]);
            pass.draw(0..3, 0..1);
        }

        if self.overlay_verts > 0 {
            // Last, over everything, with no depth attachment at all: the HUD is
            // not in the world and has nothing to be behind.
            //
            // **On the swapchain, not the scene**: the HUD is drawn at native
            // resolution whatever the render scale is. 5×7 glyphs upscaled from
            // 50% are unreadable, and the HUD costs nothing to draw at full size.
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
        self.rebuild_scene();
    }

    fn rebuild_scene(&mut self) {
        self.scene = create_scene(
            &self.device,
            &self.config,
            self.video.render_scale,
            self.video.samples(),
        );
        // The bind group holds a *view*, so it is stale the moment the texture
        // behind it is replaced. Forgetting this draws the previous frame's
        // scene, or a texture that has been freed.
        self.blit_bind_group = create_blit_bind_group(
            &self.device,
            &self.blit_layout,
            self.scene.sampled(),
            &self.sampler,
        );
    }

    /// Apply a change from the pause menu.
    ///
    /// Each knob rebuilds only what it invalidates: vsync is a surface
    /// reconfigure, the sample count is a pipeline rebuild *and* a scene
    /// rebuild, and the render scale is a scene rebuild alone. Quality also
    /// carries the shader's fog and detail, which need nothing rebuilt at all —
    /// they are uniform data, written next frame.
    pub fn set_video(&mut self, video: Video) {
        let samples_changed = video.samples() != self.video.samples();
        let scale_changed = (video.render_scale - self.video.render_scale).abs() > 1e-4;
        let vsync_changed = video.vsync != self.video.vsync;
        self.video = video;

        if samples_changed {
            self.pipeline = world_pipeline(
                &self.device,
                &self.world_layout,
                &self.shader,
                self.config.format,
                video.samples(),
            );
            // The translucent volumes draw **inside the same pass** — see the
            // comment at the `volume_pipeline` call — so their multisample state
            // has to move with it too. Missed here originally, and the failure is
            // the nastiest of the three: it needs a smoke cloud on screen to
            // fire, so switching quality in an empty room looks like it worked
            // and the crash lands on somebody's grenade a match later.
            self.volume_pipeline = volume_pipeline(
                &self.device,
                &self.volume_layout,
                &self.shader,
                self.config.format,
                video.samples(),
            );
            // The character pass draws into the same attachment, so its
            // multisample state has to move with it — a pipeline left at the old
            // count is a validation error on the next frame, not a soft failure.
            if let Some(characters) = self.characters.as_mut() {
                characters.rebuild(
                    &self.device,
                    &self.camera_layout,
                    &self.shadow.layout,
                    self.config.format,
                    video.samples(),
                );
            }
            // And the prop pass, for the same reason and with the same failure:
            // it draws into that attachment too, so a pipeline left at the old
            // sample count is a validation error on the next frame rather than
            // a weapon that merely looks wrong.
            self.props.rebuild(
                &self.device,
                &self.camera_layout,
                self.config.format,
                video.samples(),
            );
        }
        if vsync_changed {
            let caps = self.surface.get_capabilities(&self.adapter);
            self.config.present_mode = pick_present_mode(&caps.present_modes, video.vsync);
            self.surface.configure(&self.device, &self.config);
        }
        if samples_changed || scale_changed {
            self.rebuild_scene();
        }
    }

    pub fn video(&self) -> Video {
        self.video
    }

    /// The resolution the world is actually drawn at, for the title bar. Worth
    /// reporting: a render scale is invisible in a screenshot and the number is
    /// the whole point of the setting.
    pub fn scene_size(&self) -> (u32, u32) {
        (self.scene.width, self.scene.height)
    }
}

/// The world/bodies/view-model pipeline.
///
/// A function rather than an inline block because the **sample count is a
/// setting**: changing quality rebuilds this, and a pipeline whose multisample
/// state disagrees with the attachment it draws into is a validation error, not
/// a wrong-looking frame.
fn world_pipeline(
    device: &wgpu::Device,
    layout: &wgpu::PipelineLayout,
    shader: &wgpu::ShaderModule,
    format: wgpu::TextureFormat,
    samples: u32,
) -> wgpu::RenderPipeline {
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("hassault-pipeline"),
        layout: Some(layout),
        vertex: wgpu::VertexState {
            module: shader,
            entry_point: Some("vs_main"),
            buffers: &[Some(Vertex::layout())],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: shader,
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
            // here: every surface exists once and faces the space you can stand
            // in, so there is nothing to draw on the far side.
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
        multisample: wgpu::MultisampleState {
            count: samples,
            ..Default::default()
        },
        multiview_mask: None,
        cache: None,
    })
}

/// The translucent pass: smoke and fire.
///
/// Three departures from `world_pipeline`, each of which is a visible bug if
/// forgotten:
///
/// - **`depth_write_enabled: false`.** A cloud that wrote depth would hide
///   whatever is drawn after it — including the operators standing in it, which
///   is the one thing a smoke must never do.
/// - **`cull_mode: None`.** Walking into smoke has to fill the screen, and that
///   means drawing the far wall of the sphere as seen from inside. With back
///   faces culled a cloud simply vanishes as you enter it.
/// - **Alpha blending**, obviously — but note it still `depth_compare: Less`, so
///   a cloud behind a wall is hidden by the wall.
fn volume_pipeline(
    device: &wgpu::Device,
    layout: &wgpu::PipelineLayout,
    shader: &wgpu::ShaderModule,
    format: wgpu::TextureFormat,
    samples: u32,
) -> wgpu::RenderPipeline {
    device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("volume-pipeline"),
        layout: Some(layout),
        vertex: wgpu::VertexState {
            module: shader,
            entry_point: Some("vs_volume"),
            buffers: &[Some(VolumeVertex::layout())],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: shader,
            entry_point: Some("fs_volume"),
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
            cull_mode: None,
            polygon_mode: wgpu::PolygonMode::Fill,
            unclipped_depth: false,
            conservative: false,
        },
        depth_stencil: Some(wgpu::DepthStencilState {
            format: DEPTH_FORMAT,
            depth_write_enabled: Some(false),
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

/// Allocate the offscreen scene at `scale` of the surface, with `samples`.
fn create_scene(
    device: &wgpu::Device,
    config: &wgpu::SurfaceConfiguration,
    scale: f32,
    samples: u32,
) -> SceneTargets {
    // At least one pixel each way: a window dragged to nothing, times a 50%
    // scale, is a zero-sized texture and a validation error.
    let width = ((config.width as f32 * scale).round() as u32).max(1);
    let height = ((config.height as f32 * scale).round() as u32).max(1);
    let size = wgpu::Extent3d {
        width,
        height,
        depth_or_array_layers: 1,
    };
    let color = device
        .create_texture(&wgpu::TextureDescriptor {
            label: Some("scene-color"),
            size,
            mip_level_count: 1,
            sample_count: samples,
            dimension: wgpu::TextureDimension::D2,
            format: config.format,
            // `TEXTURE_BINDING` even when multisampled: it costs nothing, and a
            // usage flag missing at 4× only shows up when somebody selects High.
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING,
            view_formats: &[],
        })
        .create_view(&wgpu::TextureViewDescriptor::default());
    let resolve = (samples > 1).then(|| {
        device
            .create_texture(&wgpu::TextureDescriptor {
                label: Some("scene-resolve"),
                size,
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: config.format,
                usage: wgpu::TextureUsages::RENDER_ATTACHMENT
                    | wgpu::TextureUsages::TEXTURE_BINDING,
                view_formats: &[],
            })
            .create_view(&wgpu::TextureViewDescriptor::default())
    });
    let depth = device
        .create_texture(&wgpu::TextureDescriptor {
            label: Some("depth"),
            size,
            mip_level_count: 1,
            // The depth attachment's count must match the colour's, always.
            sample_count: samples,
            dimension: wgpu::TextureDimension::D2,
            format: DEPTH_FORMAT,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            view_formats: &[],
        })
        .create_view(&wgpu::TextureViewDescriptor::default());
    SceneTargets {
        color,
        resolve,
        depth,
        width,
        height,
    }
}

fn create_blit_bind_group(
    device: &wgpu::Device,
    layout: &wgpu::BindGroupLayout,
    view: &wgpu::TextureView,
    sampler: &wgpu::Sampler,
) -> wgpu::BindGroup {
    device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("blit-bind-group"),
        layout,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: wgpu::BindingResource::TextureView(view),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: wgpu::BindingResource::Sampler(sampler),
            },
        ],
    })
}

/// Lowest latency first, unless vsync was asked for. See the `present_mode`
/// comment above.
///
/// `Fifo` is the *only* mode the spec guarantees, which is why it is the tail of
/// both lists rather than only the vsync one: a surface that supports neither
/// `Immediate` nor `Mailbox` still has to present something.
fn pick_present_mode(available: &[wgpu::PresentMode], vsync: bool) -> wgpu::PresentMode {
    let wanted: &[wgpu::PresentMode] = if vsync {
        &[wgpu::PresentMode::Fifo]
    } else {
        &[
            wgpu::PresentMode::Immediate,
            wgpu::PresentMode::Mailbox,
            wgpu::PresentMode::Fifo,
        ]
    };
    for wanted in wanted.iter().copied() {
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
        assert_eq!(
            pick_present_mode(&[Fifo, Mailbox, Immediate], false),
            Immediate
        );
        assert_eq!(pick_present_mode(&[Fifo, Mailbox], false), Mailbox);
        // Vsync last, but always available — the guaranteed floor.
        assert_eq!(pick_present_mode(&[Fifo], false), Fifo);
        assert_eq!(pick_present_mode(&[], false), Fifo);
    }

    #[test]
    fn asking_for_vsync_gets_vsync_even_where_something_faster_exists() {
        use wgpu::PresentMode::*;
        // The whole point of the setting: somebody who can see tearing asked for
        // it to stop, and a "lowest latency first" list that ignored them would
        // make the toggle do nothing on exactly the hardware that has the
        // faster modes.
        assert_eq!(pick_present_mode(&[Fifo, Mailbox, Immediate], true), Fifo);
        // And a surface with no Fifo at all still presents something.
        assert_eq!(pick_present_mode(&[Immediate], true), Fifo);
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
#[cfg(test)]
mod budget {
    use super::*;
    use crate::api::HitboxSpec;
    use crate::nades::NadePool;
    use crate::protocol::{NadeRow, PlayerRow, ZoneRow};

    /// The server's `MAX_PLAYERS`.
    const MAX_PLAYERS: usize = 16;

    fn crowd() -> Vec<PlayerRow> {
        (0..MAX_PLAYERS)
            .map(|i| PlayerRow {
                id: format!("p{i}"),
                alive: true,
                x: i as f32 * 4.0,
                y: 10.0,
                z: 2.0,
                ..Default::default()
            })
            .collect()
    }

    #[test]
    fn the_body_buffer_fits_a_full_match() {
        // The test that was missing. `MAX_BODY_VERTS` was 4096, justified by
        // arithmetic that treated a body as one box — true when it was written,
        // false once `bodies.rs` grew into an articulated operator of nineteen.
        // A body became 684 vertices, six players stopped fitting, and the only
        // symptom was a player not being drawn.
        //
        // Computed from the real builders rather than from a remembered number,
        // so it re-measures itself whenever a body changes shape.
        let rows = crowd();
        let hitbox = HitboxSpec::default();
        let mut verts = crate::bodies::build(&rows, "nobody", &hitbox);
        // The debug overlay rides the same buffer, so it is part of the budget.
        verts.extend(crate::bodies::build_hitboxes(&rows, "nobody", &hitbox));

        // Plus every grenade a round could plausibly have in the air at once.
        let mut pool = NadePool::default();
        let thrown: Vec<NadeRow> = (0..MAX_PLAYERS)
            .map(|i| NadeRow {
                id: format!("n{i}"),
                kind: "he".into(),
                fuse: 0.4,
                ..Default::default()
            })
            .collect();
        pool.sync(&thrown, &[]);
        pool.vertices(&mut verts);

        assert!(
            verts.len() <= MAX_BODY_VERTS,
            "a full match needs {} body vertices and the buffer holds {MAX_BODY_VERTS}; \
             the excess is silently not drawn",
            verts.len()
        );
    }

    #[test]
    fn the_volume_buffer_fits_a_round_full_of_smoke() {
        let mut pool = NadePool::default();
        // Effects share this buffer, so they share the budget: a full team's
        // worth of smoke *and* a shotgun volley from everyone at once.
        let clouds: Vec<ZoneRow> = (0..MAX_PLAYERS)
            .map(|i| ZoneRow {
                id: format!("z{i}"),
                kind: "smoke".into(),
                r: 6.0,
                left: 6.0,
                duration: 12.0,
                ..Default::default()
            })
            .collect();
        pool.sync(&[], &clouds);
        let mut out = Vec::new();
        crate::nades::volume_vertices(&pool, &mut out);

        let mut fx = crate::effects::EffectsPool::default();
        for i in 0..MAX_PLAYERS {
            // A shotgun is eight pellets, so eight tracers and eight impacts.
            let ends: Vec<[f32; 3]> = (0..8).map(|p| [i as f32, p as f32, 2.0]).collect();
            fx.shot([i as f32, 0.0, 2.0], &ends, false);
        }
        fx.vertices(&mut out);

        assert!(
            out.len() <= MAX_VOLUME_VERTS,
            "{} volume vertices, buffer holds {MAX_VOLUME_VERTS}",
            out.len()
        );
    }
}
