//! What the in-game menu edits, and where it lives between sessions.
//!
//! **The node is the store, not a file next to the binary.** Every other setting
//! in this app is a row in the node's settings bag — read live by the pane,
//! editable from the Settings page, backed up with the data directory — and a
//! native client that kept its own `settings.json` would be the one surface
//! whose preferences nothing else could see. So the menu reads
//! `GET /api/settings` at startup and writes `PUT /api/settings/{key}` on each
//! change, exactly as the browser does.
//!
//! Two consequences worth being explicit about:
//!
//! - **Writes never block a frame.** An HTTP round trip on the frame somebody
//!   nudges a slider is a visible hitch, and a slider produces a lot of them. A
//!   worker thread owns the writes and the menu hands it values; the in-memory
//!   value is authoritative for this session either way, so a failed write costs
//!   a preference and never a frame.
//! - **A node that is down is not an error.** Train runs against a node that only
//!   served the map; if the settings read fails, the defaults here are what the
//!   game uses and it says so once. Refusing to start over a crosshair colour
//!   would be absurd.
//!
//! The keys are declared in the module manifest
//! (`packages/core/src/modules/hassault/index.ts`) so they appear in the pane's
//! Settings page too — a native-only key would be invisible to every other
//! surface, which is exactly the split this file exists to avoid.

use std::sync::mpsc::{self, Sender};
use std::thread;

use crate::api::NodeApi;

pub const KEY_SENSITIVITY: &str = "hassault.sensitivity";
pub const KEY_FULLSCREEN: &str = "hassault.video.fullscreen";
pub const KEY_RENDER_SCALE: &str = "hassault.video.renderScale";
pub const KEY_QUALITY: &str = "hassault.video.quality";
pub const KEY_VSYNC: &str = "hassault.video.vsync";
pub const KEY_FOV: &str = "hassault.video.fov";
/// The old on/off anti-aliasing row. Still written (as `msaa > 1`) and read when
/// `KEY_MSAA` is absent, so a bag saved before the count existed keeps its choice.
pub const KEY_ANTIALIAS: &str = "hassault.video.antialias";
pub const KEY_MSAA: &str = "hassault.video.msaa";
pub const KEY_ANISOTROPY: &str = "hassault.video.anisotropy";
pub const KEY_TEXTURES: &str = "hassault.video.textureQuality";
pub const KEY_SHADOW_QUALITY: &str = "hassault.video.shadowQuality";
pub const KEY_BLOOM: &str = "hassault.video.bloom";
pub const KEY_MAP_LIGHTS: &str = "hassault.video.mapLights";
pub const KEY_SKY: &str = "hassault.video.sky";
pub const KEY_SHARPEN: &str = "hassault.video.sharpen";
pub const KEY_BRIGHTNESS: &str = "hassault.video.brightness";
pub const KEY_FRAME_LATENCY: &str = "hassault.video.frameLatency";
pub const KEY_DISPLAY_MODE: &str = "hassault.video.displayMode";
pub const KEY_MONITOR: &str = "hassault.video.monitor";
pub const KEY_EXCLUSIVE_MODE: &str = "hassault.video.exclusiveMode";
pub const KEY_GPU_ADAPTER: &str = "hassault.video.adapter";
pub const KEY_GPU_BACKEND: &str = "hassault.video.backend";
pub const KEY_GPU_POWER: &str = "hassault.video.powerPreference";
pub const KEY_SHADOWS: &str = "hassault.video.shadows";
pub const KEY_FPS_LIMIT: &str = "hassault.video.fpsLimit";
pub const KEY_CROSSHAIR_STYLE: &str = "hassault.crosshair.style";
pub const KEY_CROSSHAIR_SIZE: &str = "hassault.crosshair.size";
pub const KEY_CROSSHAIR_GAP: &str = "hassault.crosshair.gap";
pub const KEY_CROSSHAIR_THICKNESS: &str = "hassault.crosshair.thickness";
pub const KEY_CROSSHAIR_COLOR: &str = "hassault.crosshair.color";
pub const KEY_CROSSHAIR_OUTLINE: &str = "hassault.crosshair.outline";
pub const KEY_CROSSHAIR_DOT: &str = "hassault.crosshair.dot";
pub const KEY_CROSSHAIR_ALPHA: &str = "hassault.crosshair.alpha";
pub const KEY_HUD_SCALE: &str = "hassault.video.hudScale";
pub const KEY_SHOW_HITBOXES: &str = "hassault.debug.hitboxes";
/// `GRENADE_ARC_KEY` in the pane's `menu-panels.tsx`.
pub const KEY_GRENADE_ARC: &str = "hassault.debug.grenadeArc";
/// `CONTROLS_KEY` in the pane's `menu-panels.tsx`.
pub const KEY_CONTROLS: &str = "hassault.controls";

/// How much the renderer is allowed to spend on looking good.
///
/// Since the Advanced Video page this is a **preset**: picking a level writes
/// every row under it (`Video::apply_preset`), and the level itself only keeps
/// the two things no row owns — how much of the light rig shades a surface, and
/// how far the fog reaches. A row changed afterwards leaves the level where it
/// was and the menu shows `CUSTOM`, so the preset never shadows a choice.
///
/// **High is the browser's picture**: its fog, its light rig, 4× MSAA and a
/// 2048 shadow map, which is what the pane draws. Ultra is where this client
/// goes past the browser — bloom, every map light, finer shadows and textures —
/// and Low and Medium trade distance and detail for fill rate on a laptop.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Quality {
    Low,
    #[default]
    Medium,
    High,
    Ultra,
}

impl Quality {
    pub const ALL: [Quality; 4] = [Quality::Low, Quality::Medium, Quality::High, Quality::Ultra];

    pub fn label(self) -> &'static str {
        match self {
            Quality::Low => "LOW",
            Quality::Medium => "MEDIUM",
            Quality::High => "HIGH",
            Quality::Ultra => "ULTRA",
        }
    }

    fn key(self) -> &'static str {
        match self {
            Quality::Low => "low",
            Quality::Medium => "medium",
            Quality::High => "high",
            Quality::Ultra => "ultra",
        }
    }

    fn parse(s: &str) -> Quality {
        match s {
            "low" => Quality::Low,
            "high" => Quality::High,
            "ultra" => Quality::Ultra,
            _ => Quality::Medium,
        }
    }

    /// How much denser than the map's own fog this level draws it.
    ///
    /// Denser is cheaper only in the sense that it hides distance — but it is
    /// also the most visible difference between the levels, and a quality
    /// setting whose effect is invisible is one people flip back and forth
    /// wondering whether it did anything.
    ///
    /// **High and Ultra draw the map's density exactly**, which is what the
    /// browser draws: that is the level at which the two clients are meant to be
    /// the same picture. The lower levels keep the old ratios (0.0110 and 0.0075
    /// against the cube default's 0.0055).
    pub fn fog_scale(self) -> f32 {
        match self {
            Quality::Low => 2.0,
            Quality::Medium => 0.0075 / 0.0055,
            Quality::High | Quality::Ultra => 1.0,
        }
    }

    /// Shading detail, read by the fragment shader: 0 flat, 1 the hemisphere
    /// and sun, 2 the fill as well.
    pub fn detail(self) -> f32 {
        match self {
            Quality::Low => 0.0,
            Quality::Medium => 1.0,
            Quality::High | Quality::Ultra => 2.0,
        }
    }

    /// Every row this level writes, as the video settings it produces.
    fn preset(self) -> Preset {
        match self {
            Quality::Low => Preset {
                msaa: 1,
                anisotropy: 4,
                textures: TextureQuality::Low,
                shadows: ShadowLevel::Low,
                bloom: 0.0,
                map_lights: 0,
                sky: false,
            },
            Quality::Medium => Preset {
                msaa: 1,
                anisotropy: 8,
                textures: TextureQuality::Medium,
                shadows: ShadowLevel::Medium,
                bloom: 0.0,
                map_lights: 8,
                sky: true,
            },
            Quality::High => Preset {
                msaa: 4,
                anisotropy: 16,
                textures: TextureQuality::Medium,
                shadows: ShadowLevel::High,
                bloom: 0.0,
                map_lights: 16,
                sky: true,
            },
            Quality::Ultra => Preset {
                msaa: 8,
                anisotropy: 16,
                textures: TextureQuality::High,
                shadows: ShadowLevel::Ultra,
                bloom: 0.5,
                map_lights: 64,
                sky: true,
            },
        }
    }
}

/// The rows a quality preset writes. Everything else on the page — FOV, the
/// frame cap, the render scale, brightness, sharpening, the display and the GPU
/// — is a preference about the machine or the player, not about how pretty the
/// scene is, and a preset leaves it alone.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Preset {
    msaa: u32,
    anisotropy: u16,
    textures: TextureQuality,
    shadows: ShadowLevel,
    bloom: f32,
    map_lights: u32,
    sky: bool,
}

/// The resolution of the generated surface textures on modelled maps.
///
/// Generated on the CPU at load, so a higher level costs startup time and VRAM
/// (18 layers with a full mip chain: 6 MB at Medium, 24 MB at High), never a
/// frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TextureQuality {
    Low,
    #[default]
    Medium,
    High,
}

impl TextureQuality {
    pub const ALL: [TextureQuality; 3] = [
        TextureQuality::Low,
        TextureQuality::Medium,
        TextureQuality::High,
    ];

    pub fn size(self) -> u32 {
        match self {
            TextureQuality::Low => 128,
            TextureQuality::Medium => 256,
            TextureQuality::High => 512,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            TextureQuality::Low => "LOW",
            TextureQuality::Medium => "MEDIUM",
            TextureQuality::High => "HIGH",
        }
    }

    fn key(self) -> &'static str {
        match self {
            TextureQuality::Low => "low",
            TextureQuality::Medium => "medium",
            TextureQuality::High => "high",
        }
    }

    fn parse(s: &str) -> TextureQuality {
        match s {
            "low" => TextureQuality::Low,
            "high" => TextureQuality::High,
            _ => TextureQuality::Medium,
        }
    }
}

/// The sun's shadow: its map's resolution and its filter.
///
/// The map is rendered **once per map** (see `shadow.rs`), so resolution costs
/// memory and a moment at load rather than frame time; the taps are the
/// per-pixel cost. High is the browser's 2048.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ShadowLevel {
    Low,
    Medium,
    #[default]
    High,
    Ultra,
    Extreme,
}

impl ShadowLevel {
    pub const ALL: [ShadowLevel; 5] = [
        ShadowLevel::Low,
        ShadowLevel::Medium,
        ShadowLevel::High,
        ShadowLevel::Ultra,
        ShadowLevel::Extreme,
    ];

    pub fn quality(self) -> crate::shadow::ShadowQuality {
        let (size, taps) = match self {
            ShadowLevel::Low => (1024, 4),
            ShadowLevel::Medium => (2048, 8),
            ShadowLevel::High => (2048, 16),
            ShadowLevel::Ultra => (4096, 16),
            ShadowLevel::Extreme => (8192, 16),
        };
        crate::shadow::ShadowQuality { size, taps }
    }

    pub fn label(self) -> &'static str {
        match self {
            ShadowLevel::Low => "LOW",
            ShadowLevel::Medium => "MEDIUM",
            ShadowLevel::High => "HIGH",
            ShadowLevel::Ultra => "ULTRA",
            ShadowLevel::Extreme => "EXTREME",
        }
    }

    fn key(self) -> &'static str {
        match self {
            ShadowLevel::Low => "low",
            ShadowLevel::Medium => "medium",
            ShadowLevel::High => "high",
            ShadowLevel::Ultra => "ultra",
            ShadowLevel::Extreme => "extreme",
        }
    }

    fn parse(s: &str) -> ShadowLevel {
        match s {
            "low" => ShadowLevel::Low,
            "medium" => ShadowLevel::Medium,
            "ultra" => ShadowLevel::Ultra,
            "extreme" => ShadowLevel::Extreme,
            _ => ShadowLevel::High,
        }
    }
}
/// Crosshair shapes. Deliberately few: this is a reticle, not a drawing program,
/// and every one of these is a shape people actually play with.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CrosshairStyle {
    /// Four ticks with a gap — the default, and the one that shows spread.
    #[default]
    Cross,
    /// Four ticks and a centre dot.
    CrossDot,
    /// A dot alone. The smallest thing that still says where the shot goes.
    Dot,
    /// A ring at the spread radius, which is the honest picture of a cone.
    Circle,
}

impl CrosshairStyle {
    pub const ALL: [CrosshairStyle; 4] = [
        CrosshairStyle::Cross,
        CrosshairStyle::CrossDot,
        CrosshairStyle::Dot,
        CrosshairStyle::Circle,
    ];

    pub fn label(self) -> &'static str {
        match self {
            CrosshairStyle::Cross => "CROSS",
            CrosshairStyle::CrossDot => "CROSS + DOT",
            CrosshairStyle::Dot => "DOT",
            CrosshairStyle::Circle => "CIRCLE",
        }
    }

    fn key(self) -> &'static str {
        match self {
            CrosshairStyle::Cross => "cross",
            CrosshairStyle::CrossDot => "crossDot",
            CrosshairStyle::Dot => "dot",
            CrosshairStyle::Circle => "circle",
        }
    }

    pub fn parse(s: &str) -> CrosshairStyle {
        match s {
            "crossDot" => CrosshairStyle::CrossDot,
            "dot" => CrosshairStyle::Dot,
            "circle" => CrosshairStyle::Circle,
            _ => CrosshairStyle::Cross,
        }
    }
}

/// Named colours rather than a picker.
///
/// A hex field in a menu driven by a gamepad-shaped cursor is a bad time, and
/// the reason people change crosshair colour is contrast against a particular
/// map — which six well-separated hues cover. They are also all *bright*: a dark
/// crosshair on a dark map is the one choice that would make the game worse.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CrosshairColor {
    #[default]
    White,
    Green,
    Cyan,
    Amber,
    Magenta,
    Red,
}

impl CrosshairColor {
    pub const ALL: [CrosshairColor; 6] = [
        CrosshairColor::White,
        CrosshairColor::Green,
        CrosshairColor::Cyan,
        CrosshairColor::Amber,
        CrosshairColor::Magenta,
        CrosshairColor::Red,
    ];

    pub fn label(self) -> &'static str {
        match self {
            CrosshairColor::White => "WHITE",
            CrosshairColor::Green => "GREEN",
            CrosshairColor::Cyan => "CYAN",
            CrosshairColor::Amber => "AMBER",
            CrosshairColor::Magenta => "MAGENTA",
            CrosshairColor::Red => "RED",
        }
    }

    fn key(self) -> &'static str {
        match self {
            CrosshairColor::White => "white",
            CrosshairColor::Green => "green",
            CrosshairColor::Cyan => "cyan",
            CrosshairColor::Amber => "amber",
            CrosshairColor::Magenta => "magenta",
            CrosshairColor::Red => "red",
        }
    }

    fn parse(s: &str) -> CrosshairColor {
        match s {
            "green" => CrosshairColor::Green,
            "cyan" => CrosshairColor::Cyan,
            "amber" => CrosshairColor::Amber,
            "magenta" => CrosshairColor::Magenta,
            "red" => CrosshairColor::Red,
            _ => CrosshairColor::White,
        }
    }

    pub fn rgba(self) -> [f32; 4] {
        match self {
            CrosshairColor::White => [0.92, 0.94, 0.96, 0.9],
            CrosshairColor::Green => [0.45, 0.95, 0.45, 0.92],
            CrosshairColor::Cyan => [0.40, 0.90, 0.95, 0.92],
            CrosshairColor::Amber => [0.97, 0.78, 0.35, 0.92],
            CrosshairColor::Magenta => [0.95, 0.45, 0.90, 0.92],
            CrosshairColor::Red => [0.97, 0.35, 0.32, 0.92],
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Crosshair {
    pub style: CrosshairStyle,
    /// Arm length in pixels at a 1080p-ish window; scaled with the window like
    /// the rest of the HUD, so a setting means the same thing on every monitor.
    pub size: f32,
    /// Distance from the centre to the inside end of each arm. **This one moves
    /// on its own too**: the crosshair opens with the weapon's cone, and this is
    /// the floor it opens from.
    pub gap: f32,
    pub thickness: f32,
    pub color: CrosshairColor,
    /// A dark border around every element.
    ///
    /// On by default, and the one setting here that changes whether the reticle
    /// *works* rather than how it looks: a single colour is invisible against
    /// any surface near its own brightness, and the way a player finds that out
    /// is by missing. Offered as a setting anyway, because somebody running a
    /// very thin reticle may prefer the cleaner line.
    pub outline: bool,
    /// Whether the centre dot is drawn, independent of the style.
    ///
    /// Separate from `style` because "cross, no dot" and "cross with dot" were
    /// two of the four styles, which meant picking a ring cost you the choice.
    pub dot: bool,
    /// Overall opacity, 0.15–1.0.
    pub alpha: f32,
}

impl Default for Crosshair {
    fn default() -> Crosshair {
        Crosshair {
            style: CrosshairStyle::default(),
            size: 3.0,
            gap: 4.0,
            thickness: 0.6,
            color: CrosshairColor::default(),
            outline: true,
            dot: true,
            alpha: 1.0,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Video {
    pub fullscreen: bool,
    /// Exclusive fullscreen: the display switches to `exclusive_mode` and this
    /// window owns it. Off by default, for the reason the borderless default
    /// gives — a mode switch at each end, a black screen with it, and every other
    /// window on the monitor rearranged when the game exits. What it buys on some
    /// drivers is a present path with no compositor at all, and a refresh rate
    /// the desktop is not running at.
    pub exclusive: bool,
    /// Which monitor fullscreen lands on: 0 is the one the window is on, 1.. the
    /// system's list in its own order.
    pub monitor: u32,
    /// The exclusive mode, `(width, height, refresh in millihertz)`; zeros for
    /// the monitor's current mode.
    pub exclusive_mode: (u32, u32, u32),
    /// Fraction of the window's pixels the world is rendered at, 0.5–2.0. The
    /// HUD is **not** scaled with it — text drawn at half resolution and stretched
    /// is unreadable, and the HUD costs nothing to draw at native size.
    ///
    /// Above 1.0 this is **supersampling**: the world is drawn larger than the
    /// window and the blit's linear filter averages it back down. It is the only
    /// anti-aliasing that touches shader aliasing and alpha edges, which MSAA does
    /// not. The cost is quadratic: 2.0 is four times the pixels, so it is offered
    /// but not defaulted.
    pub render_scale: f32,
    pub quality: Quality,
    /// Vertical sync. First in the present-mode list when on, because tearing is
    /// real and somebody who can see it should be able to say so; off, a frame of
    /// queued latency is precisely what this client exists to avoid.
    pub vsync: bool,
    /// Vertical field of view, in **degrees**, before the scope divides it.
    ///
    /// 75 is the browser pane's, so the default is the same picture on both
    /// clients. The only knob on this page that changes how the game *plays*
    /// rather than how it looks.
    pub fov: f32,
    /// Multisample count asked for: 1, 2, 4 or 8.
    ///
    /// **Asked for, not guaranteed.** Only 1 and 4 are guaranteed by the spec;
    /// 2 and 8 exist behind `TEXTURE_ADAPTER_SPECIFIC_FORMAT_FEATURES`, which the
    /// renderer now requests **only when the adapter offers it** and then asks
    /// the adapter which counts its formats support. `samples()` snaps this to
    /// that list, so a count the GPU cannot do is never handed to a pipeline —
    /// which is what crashed the client on the first frame when 2× was a
    /// constant (see `gpu::snap_samples`).
    pub msaa: u32,
    /// Anisotropic filtering on the world's surfaces: 1 (off), 2, 4, 8 or 16.
    pub anisotropy: u16,
    pub textures: TextureQuality,
    /// Whether world surfaces sample the sun's shadow map at all.
    ///
    /// **A look, not a frame rate**: the map is baked once at load, and turning
    /// this off only stops the fragment shader taking the taps. `shadow_level` is
    /// where the cost lives.
    pub shadows: bool,
    pub shadow_level: ShadowLevel,
    /// Bloom strength, 0 (off) to 1. Only emissive surfaces and the brightest
    /// highlights pass its threshold, so it is a glow on lamps, screens and
    /// muzzle flashes rather than a haze over the scene.
    pub bloom: f32,
    /// How many of the map's point lights shade a frame, nearest first. 0 turns
    /// them off; the browser's High draws 8.
    pub map_lights: u32,
    /// The sky dome on maps open to the sky. Off draws the flat horizon colour
    /// the renderer used to clear to.
    pub sky: bool,
    /// Contrast-adaptive sharpening strength, 0 (off) to 1. Most useful below a
    /// render scale of 100%, where it gives back the edge contrast the upscale
    /// took away.
    pub sharpen: f32,
    /// A multiplier on the map's exposure, 0.6–1.6.
    pub brightness: f32,
    /// Frames the GPU may queue: 1 is the lowest latency and the default; 2 or 3
    /// smooths frame pacing on a GPU that is close to its limit.
    pub frame_latency: u32,
    /// How large the HUD is drawn, 0.75–1.5 of its derived size.
    pub hud_scale: f32,
    /// Frames per second to cap at, or **0 for uncapped**. Unlike vsync, a cap
    /// adds no queued latency, it only sleeps.
    pub fps_limit: u32,
}

impl Video {
    /// The multisample count the renderer builds every pipeline against.
    ///
    /// The one place the count is decided, so the pipelines, the scene texture
    /// and the resolve target cannot disagree about it — a mismatch there is a
    /// validation error at pipeline creation rather than a softer picture.
    pub fn samples(self) -> u32 {
        crate::gpu::snap_samples(self.msaa)
    }

    /// Picking a quality level writes the individual rows rather than shadowing
    /// them, so the menu never shows `HIGH` next to a row that contradicts it.
    pub fn apply_preset(&mut self, quality: Quality) {
        let p = quality.preset();
        self.quality = quality;
        self.msaa = p.msaa;
        self.anisotropy = p.anisotropy;
        self.textures = p.textures;
        self.shadow_level = p.shadows;
        self.bloom = p.bloom;
        self.map_lights = p.map_lights;
        self.sky = p.sky;
    }

    /// Whether every row a preset owns is where `quality`'s preset puts it. The
    /// menu shows `CUSTOM` when it is not.
    pub fn matches_preset(&self) -> bool {
        let p = self.quality.preset();
        self.msaa == p.msaa
            && self.anisotropy == p.anisotropy
            && self.textures == p.textures
            && self.shadow_level == p.shadows
            && (self.bloom - p.bloom).abs() < 1e-3
            && self.map_lights == p.map_lights
            && self.sky == p.sky
    }

    /// The keys `apply_preset` writes, for persisting a preset whole: saving only
    /// the level would bring it back next session over the old rows.
    pub const PRESET_KEYS: [&'static str; 8] = [
        KEY_QUALITY,
        KEY_MSAA,
        KEY_ANISOTROPY,
        KEY_TEXTURES,
        KEY_SHADOW_QUALITY,
        KEY_BLOOM,
        KEY_MAP_LIGHTS,
        KEY_SKY,
    ];
}

/// The FOV range, in degrees. Narrow enough that nobody can zoom out to a
/// fish-eye that renders every enemy a pixel wide and calls it a setting.
pub const FOV_RANGE: (f32, f32) = (70.0, 120.0);

/// The frame caps offered, `0` being uncapped. Not a free-form number: the useful
/// values are the refresh rates displays actually run at, plus one below all of
/// them for a laptop that would rather stay quiet.
pub const FPS_LIMITS: [u32; 6] = [0, 60, 120, 144, 240, 360];

/// The multisample counts a player can ask for. What the GPU grants is
/// `gpu::sample_counts`.
pub const MSAA_COUNTS: [u32; 4] = [1, 2, 4, 8];
pub const ANISOTROPY_LEVELS: [u16; 5] = [1, 2, 4, 8, 16];
pub const MAP_LIGHT_COUNTS: [u32; 5] = [0, 8, 16, 32, 64];
pub const BLOOM_RANGE: (f32, f32) = (0.0, 1.0);
pub const SHARPEN_RANGE: (f32, f32) = (0.0, 1.0);
pub const BRIGHTNESS_RANGE: (f32, f32) = (0.6, 1.6);
pub const FRAME_LATENCY_RANGE: (u32, u32) = (1, 3);

impl Default for Video {
    fn default() -> Video {
        let mut video = Video {
            // **Fullscreen by default.** A shooter that opens in a window with a
            // title bar is one you have to go and configure before it feels like
            // a game, and borderless fullscreen costs nothing to leave.
            fullscreen: true,
            exclusive: false,
            monitor: 0,
            exclusive_mode: (0, 0, 0),
            render_scale: 1.0,
            hud_scale: 1.0,
            quality: Quality::default(),
            vsync: true,
            fov: 75.0,
            msaa: 1,
            anisotropy: 16,
            textures: TextureQuality::default(),
            shadows: true,
            shadow_level: ShadowLevel::default(),
            bloom: 0.0,
            map_lights: 8,
            sky: true,
            sharpen: 0.0,
            brightness: 1.0,
            frame_latency: 1,
            fps_limit: 0,
        };
        video.apply_preset(Quality::default());
        video
    }
}

/// Snap `want` to the nearest of `offered`. For a saved value the menu has to be
/// able to show: honouring 37 FPS or 5× MSAA would leave a row holding a value
/// none of its steps can reach, so it would jump the first time it was touched.
pub fn snap<T: Copy + Into<f64>>(offered: &[T], want: f64) -> T {
    *offered
        .iter()
        .min_by(|a, b| {
            let da = ((**a).into() - want).abs();
            let db = ((**b).into() - want).abs();
            da.total_cmp(&db)
        })
        .expect("a non-empty list")
}

#[derive(Debug, Clone, Copy)]
pub struct Settings {
    pub sensitivity: f32,
    pub crosshair: Crosshair,
    pub video: Video,
    /// Which GPU and API to open next time. Applies on restart — see `gpu.rs`.
    pub gpu: GpuPick,
    /// Draw the served hitbox around every body.
    ///
    /// A setting rather than a build flag, because the question it answers —
    /// "is what I am shooting at where it is drawn?" — is one a *player* asks,
    /// usually right after a shot they were sure of. It is off by default: a
    /// permanent wireframe is a worse picture of the game than the game.
    pub show_hitboxes: bool,
    /// Draw the predicted flight path while a grenade is out. Off by default,
    /// and the app only honours it in Train and in a room this player hosts —
    /// a practice aid, never something to read lineups off in a real match.
    pub grenade_arc: bool,
}

/// The GPU rows, as the menu steps them.
///
/// `adapter` indexes `gpu::adapters()` — 0 for automatic, `i + 1` for the i-th
/// — so `Settings` stays `Copy`. What is **saved** is the adapter's name, never
/// the index: the order adapters enumerate in is not stable across driver
/// updates, and an index that quietly starts meaning the other GPU is worse
/// than a name that stops matching.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct GpuPick {
    pub adapter: usize,
    pub backend: crate::gpu::BackendPref,
    pub low_power: bool,
}

impl GpuPick {
    /// The adapter's name, or `None` for automatic.
    pub fn adapter_name(&self) -> Option<&'static str> {
        self.adapter
            .checked_sub(1)
            .and_then(|i| crate::gpu::adapters().get(i))
            .map(|a| a.name.as_str())
    }

    pub fn choice(&self) -> crate::gpu::GpuChoice {
        crate::gpu::GpuChoice {
            adapter: self.adapter_name().map(str::to_string),
            backend: self.backend,
            low_power: self.low_power,
        }
    }

    /// Resolve a saved name to a row index; an adapter no longer on this
    /// machine is automatic, which is also what the renderer will do with it.
    fn index_of(name: &str) -> usize {
        if name.is_empty() || name.eq_ignore_ascii_case("auto") {
            return 0;
        }
        crate::gpu::adapters()
            .iter()
            .position(|a| a.name.eq_ignore_ascii_case(name))
            .map(|i| i + 1)
            .unwrap_or(0)
    }
}

impl Default for Settings {
    fn default() -> Settings {
        Settings {
            sensitivity: 1.0,
            crosshair: Crosshair::default(),
            video: Video::default(),
            gpu: GpuPick::default(),
            show_hitboxes: false,
            grenade_arc: false,
        }
    }
}

impl Settings {
    /// Read the node's settings bag, falling back to the defaults per key.
    ///
    /// Per *key*, not per document: a bag that has a crosshair colour and no
    /// render scale is the normal state of a fresh install, and a reader that
    /// gave up on the first missing key would leave every later one unread.
    ///
    /// **The quality level is applied first**, and that order is load-bearing:
    /// the level writes a default into every row it owns, so reading the rows
    /// before it would have the preset quietly overwrite choices the player
    /// actually made.
    pub fn from_values(values: &serde_json::Value) -> Settings {
        let mut s = Settings::default();
        let get = |key: &str| values.get(key);
        let num = |key: &str| get(key).and_then(|v| v.as_f64());
        let flag = |key: &str| get(key).and_then(|v| v.as_bool());
        let text = |key: &str| get(key).and_then(|v| v.as_str());

        if let Some(v) = text(KEY_QUALITY) {
            s.video.apply_preset(Quality::parse(v));
        }
        if let Some(v) = num(KEY_SENSITIVITY) {
            s.sensitivity = (v as f32).clamp(0.05, 10.0);
        }
        if let Some(v) = flag(KEY_FULLSCREEN) {
            s.video.fullscreen = v;
        }
        if let Some(v) = text(KEY_DISPLAY_MODE) {
            s.video.exclusive = v == "exclusive";
        }
        if let Some(v) = num(KEY_MONITOR) {
            s.video.monitor = v.clamp(0.0, 16.0) as u32;
        }
        if let Some(v) = text(KEY_EXCLUSIVE_MODE) {
            s.video.exclusive_mode = parse_mode(v).unwrap_or((0, 0, 0));
        }
        if let Some(v) = num(KEY_RENDER_SCALE) {
            s.video.render_scale = (v as f32).clamp(0.5, 2.0);
        }
        if let Some(v) = num(KEY_HUD_SCALE) {
            s.video.hud_scale = (v as f32).clamp(0.75, 1.5);
        }
        if let Some(v) = flag(KEY_VSYNC) {
            s.video.vsync = v;
        }
        if let Some(v) = num(KEY_FOV) {
            s.video.fov = (v as f32).clamp(FOV_RANGE.0, FOV_RANGE.1);
        }
        // The old on/off row, read only when the new one is absent: a bag saved
        // before multisampling had a count still means what it said.
        if let Some(v) = num(KEY_MSAA) {
            s.video.msaa = snap(&MSAA_COUNTS, v);
        } else if let Some(v) = flag(KEY_ANTIALIAS) {
            s.video.msaa = if v { 4 } else { 1 };
        }
        if let Some(v) = num(KEY_ANISOTROPY) {
            s.video.anisotropy = snap(&ANISOTROPY_LEVELS, v);
        }
        if let Some(v) = text(KEY_TEXTURES) {
            s.video.textures = TextureQuality::parse(v);
        }
        if let Some(v) = flag(KEY_SHADOWS) {
            s.video.shadows = v;
        }
        if let Some(v) = text(KEY_SHADOW_QUALITY) {
            s.video.shadow_level = ShadowLevel::parse(v);
        }
        if let Some(v) = num(KEY_BLOOM) {
            s.video.bloom = (v as f32).clamp(BLOOM_RANGE.0, BLOOM_RANGE.1);
        }
        if let Some(v) = num(KEY_MAP_LIGHTS) {
            s.video.map_lights = snap(&MAP_LIGHT_COUNTS, v);
        }
        if let Some(v) = flag(KEY_SKY) {
            s.video.sky = v;
        }
        if let Some(v) = num(KEY_SHARPEN) {
            s.video.sharpen = (v as f32).clamp(SHARPEN_RANGE.0, SHARPEN_RANGE.1);
        }
        if let Some(v) = num(KEY_BRIGHTNESS) {
            s.video.brightness = (v as f32).clamp(BRIGHTNESS_RANGE.0, BRIGHTNESS_RANGE.1);
        }
        if let Some(v) = num(KEY_FRAME_LATENCY) {
            s.video.frame_latency =
                (v.round().max(0.0) as u32).clamp(FRAME_LATENCY_RANGE.0, FRAME_LATENCY_RANGE.1);
        }
        if let Some(v) = num(KEY_FPS_LIMIT) {
            // Snapped to the offered list rather than clamped: a cap of 37 is not
            // wrong so much as meaningless, and honouring it would make the menu
            // unable to show the value it is holding.
            s.video.fps_limit = snap(&FPS_LIMITS, v.max(0.0));
        }
        if let Some(v) = text(KEY_GPU_ADAPTER) {
            s.gpu.adapter = GpuPick::index_of(v);
        }
        if let Some(v) = text(KEY_GPU_BACKEND) {
            s.gpu.backend = crate::gpu::BackendPref::parse(v);
        }
        if let Some(v) = text(KEY_GPU_POWER) {
            s.gpu.low_power = v == "low";
        }
        if let Some(v) = text(KEY_CROSSHAIR_STYLE) {
            s.crosshair.style = CrosshairStyle::parse(v);
        }
        if let Some(v) = num(KEY_CROSSHAIR_SIZE) {
            s.crosshair.size = (v as f32).clamp(1.0, 12.0);
        }
        if let Some(v) = num(KEY_CROSSHAIR_GAP) {
            s.crosshair.gap = (v as f32).clamp(0.0, 20.0);
        }
        if let Some(v) = num(KEY_CROSSHAIR_THICKNESS) {
            s.crosshair.thickness = (v as f32).clamp(0.2, 3.0);
        }
        if let Some(v) = flag(KEY_SHOW_HITBOXES) {
            s.show_hitboxes = v;
        }
        if let Some(v) = flag(KEY_GRENADE_ARC) {
            s.grenade_arc = v;
        }
        if let Some(v) = text(KEY_CROSSHAIR_COLOR) {
            s.crosshair.color = CrosshairColor::parse(v);
        }
        if let Some(v) = flag(KEY_CROSSHAIR_OUTLINE) {
            s.crosshair.outline = v;
        }
        if let Some(v) = flag(KEY_CROSSHAIR_DOT) {
            s.crosshair.dot = v;
        }
        if let Some(v) = num(KEY_CROSSHAIR_ALPHA) {
            s.crosshair.alpha = (v as f32).clamp(0.15, 1.0);
        }
        s
    }

    /// The value to persist for one key, as JSON.
    pub fn value_for(&self, key: &str) -> Option<serde_json::Value> {
        use serde_json::json;
        let v = &self.video;
        Some(match key {
            KEY_SENSITIVITY => json!(self.sensitivity),
            KEY_FULLSCREEN => json!(v.fullscreen),
            KEY_DISPLAY_MODE => json!(if v.exclusive {
                "exclusive"
            } else {
                "borderless"
            }),
            KEY_MONITOR => json!(v.monitor),
            KEY_EXCLUSIVE_MODE => json!(format_mode(v.exclusive_mode)),
            KEY_RENDER_SCALE => json!(v.render_scale),
            KEY_HUD_SCALE => json!(v.hud_scale),
            KEY_QUALITY => json!(v.quality.key()),
            KEY_VSYNC => json!(v.vsync),
            KEY_FOV => json!(v.fov),
            KEY_MSAA => json!(v.msaa),
            // Kept in step for anything still reading the old row.
            KEY_ANTIALIAS => json!(v.msaa > 1),
            KEY_ANISOTROPY => json!(v.anisotropy),
            KEY_TEXTURES => json!(v.textures.key()),
            KEY_SHADOWS => json!(v.shadows),
            KEY_SHADOW_QUALITY => json!(v.shadow_level.key()),
            KEY_BLOOM => json!(v.bloom),
            KEY_MAP_LIGHTS => json!(v.map_lights),
            KEY_SKY => json!(v.sky),
            KEY_SHARPEN => json!(v.sharpen),
            KEY_BRIGHTNESS => json!(v.brightness),
            KEY_FRAME_LATENCY => json!(v.frame_latency),
            KEY_FPS_LIMIT => json!(v.fps_limit),
            KEY_GPU_ADAPTER => json!(self.gpu.adapter_name().unwrap_or("auto")),
            KEY_GPU_BACKEND => json!(self.gpu.backend.key()),
            KEY_GPU_POWER => json!(if self.gpu.low_power { "low" } else { "high" }),
            KEY_CROSSHAIR_STYLE => json!(self.crosshair.style.key()),
            KEY_CROSSHAIR_SIZE => json!(self.crosshair.size),
            KEY_CROSSHAIR_GAP => json!(self.crosshair.gap),
            KEY_CROSSHAIR_THICKNESS => json!(self.crosshair.thickness),
            KEY_CROSSHAIR_COLOR => json!(self.crosshair.color.key()),
            KEY_CROSSHAIR_OUTLINE => json!(self.crosshair.outline),
            KEY_CROSSHAIR_DOT => json!(self.crosshair.dot),
            KEY_CROSSHAIR_ALPHA => json!(self.crosshair.alpha),
            KEY_SHOW_HITBOXES => json!(self.show_hitboxes),
            KEY_GRENADE_ARC => json!(self.grenade_arc),
            _ => return None,
        })
    }
}

/// `"2560x1440@143998"` — width, height, refresh in millihertz, the unit winit
/// reports it in. Empty or malformed is the monitor's current mode.
pub fn parse_mode(s: &str) -> Option<(u32, u32, u32)> {
    let (size, hz) = s.split_once('@')?;
    let (w, h) = size.split_once('x')?;
    Some((
        w.trim().parse().ok()?,
        h.trim().parse().ok()?,
        hz.trim().parse().ok()?,
    ))
}

pub fn format_mode(mode: (u32, u32, u32)) -> String {
    if mode.0 == 0 {
        String::new()
    } else {
        format!("{}x{}@{}", mode.0, mode.1, mode.2)
    }
}
/// A background writer, so no setting change ever costs a frame.
///
/// One thread and a channel rather than a thread per write: a slider dragged
/// across its range produces a change per frame, and spawning sixty threads to
/// PUT sixty values is worse than the hitch it was avoiding.
pub struct SettingsWriter {
    tx: Option<Sender<(String, serde_json::Value)>>,
    handle: Option<thread::JoinHandle<()>>,
}

impl SettingsWriter {
    pub fn new(base: &str) -> SettingsWriter {
        let (tx, rx) = mpsc::channel::<(String, serde_json::Value)>();
        let base = base.to_string();
        let handle = thread::Builder::new()
            .name("settings-writer".into())
            .spawn(move || {
                let api = NodeApi::new(&base);
                // Blocks on the channel, not on a poll: this thread is idle
                // between menu interactions, which is most of a session.
                while let Ok((key, value)) = rx.recv() {
                    if let Err(e) = api.put_setting(&key, &value) {
                        // Said once per failure and never fatal. The value is
                        // already live in this session; all that is lost is it
                        // being live in the next one.
                        eprintln!("hassault: could not save {key}: {e}");
                    }
                }
            })
            .ok();
        SettingsWriter {
            tx: Some(tx),
            handle,
        }
    }

    /// A writer that goes nowhere, for tests and for `--check`.
    pub fn disabled() -> SettingsWriter {
        SettingsWriter {
            tx: None,
            handle: None,
        }
    }

    /// Send everything queued and wait for it to land, then stop.
    ///
    /// For a restart: the next process reads the settings back from the node,
    /// so a write still in the channel when this one exits is a choice that
    /// silently did not happen. Closing the channel ends the thread's loop once
    /// it has drained it; joining waits for exactly that.
    pub fn finish(&mut self) {
        self.tx = None;
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }

    pub fn save(&self, key: &str, value: serde_json::Value) {
        if let Some(tx) = &self.tx {
            // A closed channel means the writer thread is gone, which is not
            // worth reporting per keystroke — the failure was already printed.
            let _ = tx.send((key.to_string(), value));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn an_empty_bag_is_every_default() {
        let s = Settings::from_values(&json!({}));
        assert_eq!(s.sensitivity, 1.0);
        assert!(s.video.fullscreen, "the client opens fullscreen");
        assert_eq!(s.video.quality, Quality::Medium);
        assert_eq!(s.crosshair.style, CrosshairStyle::Cross);
    }

    #[test]
    fn a_partial_bag_reads_every_key_it_does_have() {
        // The normal state of a fresh install: one key set, the rest absent. A
        // reader that stopped at the first missing key would silently ignore
        // everything after it.
        let s = Settings::from_values(&json!({
            "hassault.crosshair.color": "green",
            "hassault.video.quality": "high",
        }));
        assert_eq!(s.crosshair.color, CrosshairColor::Green);
        assert_eq!(s.video.quality, Quality::High);
        assert_eq!(s.crosshair.size, Crosshair::default().size);
    }

    #[test]
    fn nonsense_is_clamped_rather_than_believed() {
        // The bag is shared with a web UI and editable by hand. A render scale of
        // 40 is a texture allocation the GPU will refuse; a sensitivity of zero
        // is a view that cannot turn.
        let s = Settings::from_values(&json!({
            "hassault.video.renderScale": 40.0,
            "hassault.sensitivity": 0.0,
            "hassault.crosshair.thickness": -3.0,
        }));
        // The ceiling is 2.0, not 1.0: above 1.0 the scale is supersampling.
        // 40 is still nonsense and still clamps.
        assert_eq!(s.video.render_scale, 2.0);
        assert_eq!(s.sensitivity, 0.05);
        assert_eq!(s.crosshair.thickness, 0.2);
    }

    #[test]
    fn an_unknown_string_falls_back_rather_than_failing() {
        let s = Settings::from_values(&json!({
            "hassault.video.quality": "cinematic",
            "hassault.crosshair.style": "spinner",
        }));
        assert_eq!(s.video.quality, Quality::Medium);
        assert_eq!(s.crosshair.style, CrosshairStyle::Cross);
    }

    #[test]
    fn every_key_round_trips_through_the_bag() {
        // The property that matters for persistence: what `value_for` writes,
        // `from_values` must read back as the same setting. A key spelled
        // differently in the two directions saves and never loads, which looks
        // exactly like the setting not persisting at all.
        let mut original = Settings::default();
        original.crosshair.style = CrosshairStyle::Circle;
        original.crosshair.color = CrosshairColor::Magenta;
        original.crosshair.size = 5.0;
        original.crosshair.gap = 9.0;
        original.crosshair.thickness = 1.4;
        original.video.quality = Quality::High;
        original.video.render_scale = 0.75;
        original.video.vsync = true;
        original.video.fullscreen = false;
        original.sensitivity = 2.5;

        let mut bag = serde_json::Map::new();
        for key in [
            KEY_SENSITIVITY,
            KEY_FULLSCREEN,
            KEY_RENDER_SCALE,
            KEY_HUD_SCALE,
            KEY_QUALITY,
            KEY_VSYNC,
            KEY_CROSSHAIR_STYLE,
            KEY_CROSSHAIR_SIZE,
            KEY_CROSSHAIR_GAP,
            KEY_CROSSHAIR_THICKNESS,
            KEY_CROSSHAIR_COLOR,
            KEY_CROSSHAIR_OUTLINE,
            KEY_CROSSHAIR_DOT,
            KEY_CROSSHAIR_ALPHA,
            KEY_SHOW_HITBOXES,
            KEY_GRENADE_ARC,
        ] {
            bag.insert(key.into(), original.value_for(key).expect("a value"));
        }
        let read = Settings::from_values(&serde_json::Value::Object(bag));
        assert_eq!(read.crosshair.style, original.crosshair.style);
        assert_eq!(read.crosshair.color, original.crosshair.color);
        assert_eq!(read.crosshair.gap, original.crosshair.gap);
        assert_eq!(read.crosshair.outline, original.crosshair.outline);
        assert_eq!(read.crosshair.dot, original.crosshair.dot);
        assert_eq!(read.crosshair.alpha, original.crosshair.alpha);
        assert_eq!(read.video.quality, original.video.quality);
        assert_eq!(read.video.render_scale, original.video.render_scale);
        assert_eq!(read.video.hud_scale, original.video.hud_scale);
        assert_eq!(read.video.vsync, original.video.vsync);
        assert_eq!(read.video.fullscreen, original.video.fullscreen);
        assert_eq!(read.sensitivity, original.sensitivity);
    }

    #[test]
    fn a_key_nobody_declared_has_no_value() {
        assert!(Settings::default().value_for("hassault.nope").is_none());
    }

    #[test]
    fn without_a_device_video_only_asks_for_sample_counts_the_spec_guarantees() {
        // Before a device has reported what it supports, every request snaps
        // into **1 and 4** — the only counts the spec guarantees. A pipeline
        // built with an unsupported count is a validation error on the first
        // frame rather than a slower one; a 2× level crashed this client on a
        // machine that reports `[1, 2, 4, 8]` behind a feature it had not asked
        // for. The support list is not the guarantee.
        for msaa in MSAA_COUNTS {
            let video = Video {
                msaa,
                ..Video::default()
            };
            assert!(
                matches!(video.samples(), 1 | 4),
                "msaa {msaa} asked for {}",
                video.samples()
            );
        }
    }

    #[test]
    fn a_preset_writes_the_rows_under_it_rather_than_shadowing_them() {
        // The whole reason `apply` returns a list. If picking HIGH left the
        // sample count alone, the menu would show HIGH next to `ANTI-ALIASING
        // OFF`; if it wrote it without reporting the key, the level would come
        // back next session with the old sample count under it.
        let mut video = Video::default();
        video.apply_preset(Quality::High);
        assert_eq!(video.quality, Quality::High);
        assert_eq!(video.msaa, 4);
        assert!(video.matches_preset());
        video.apply_preset(Quality::Low);
        assert_eq!(video.msaa, 1);
        assert_eq!(video.map_lights, 0);
        video.bloom = 0.25;
        assert!(
            !video.matches_preset(),
            "a row moved off the preset is custom"
        );
    }

    #[test]
    fn a_preset_leaves_the_players_own_preferences_alone() {
        let mut video = Video {
            fov: 100.0,
            fps_limit: 144,
            render_scale: 0.75,
            brightness: 1.2,
            sharpen: 0.4,
            ..Video::default()
        };
        video.apply_preset(Quality::Ultra);
        assert_eq!(
            (video.fov, video.fps_limit, video.render_scale),
            (100.0, 144, 0.75)
        );
        assert_eq!((video.brightness, video.sharpen), (1.2, 0.4));
    }

    #[test]
    fn an_explicit_row_survives_a_bag_that_also_names_a_quality() {
        // Read order, and it is load-bearing: the level carries a default for
        // these keys, so applying it after them would have the preset quietly
        // overwrite the choices the player actually made.
        let s = Settings::from_values(&json!({
            "hassault.video.quality": "ultra",
            "hassault.video.msaa": 1,
            "hassault.video.mapLights": 8,
        }));
        assert_eq!(s.video.quality, Quality::Ultra);
        assert_eq!(s.video.msaa, 1);
        assert_eq!(s.video.map_lights, 8);
        // The rows it did not name still come from the preset.
        assert_eq!(s.video.shadow_level, ShadowLevel::Ultra);
    }

    #[test]
    fn an_old_antialias_row_still_means_what_it_said() {
        let s = Settings::from_values(&json!({
            "hassault.video.quality": "high",
            "hassault.video.antialias": false,
        }));
        assert_eq!(s.video.msaa, 1);
        let s = Settings::from_values(&json!({"hassault.video.antialias": true}));
        assert_eq!(s.video.msaa, 4);
        // The new row wins where both exist.
        let s = Settings::from_values(&json!({
            "hassault.video.antialias": false,
            "hassault.video.msaa": 8,
        }));
        assert_eq!(s.video.msaa, 8);
    }

    #[test]
    fn every_new_video_key_round_trips() {
        let mut original = Settings::default();
        original.video.apply_preset(Quality::Ultra);
        original.video.msaa = 2;
        original.video.anisotropy = 4;
        original.video.textures = TextureQuality::Low;
        original.video.shadow_level = ShadowLevel::Extreme;
        original.video.bloom = 0.75;
        original.video.map_lights = 32;
        original.video.sky = false;
        original.video.sharpen = 0.3;
        original.video.brightness = 1.4;
        original.video.frame_latency = 2;
        original.video.exclusive = true;
        original.video.monitor = 2;
        original.video.exclusive_mode = (2560, 1440, 143_998);
        original.gpu.backend = crate::gpu::BackendPref::Vulkan;
        original.gpu.low_power = true;

        let keys = [
            KEY_QUALITY,
            KEY_MSAA,
            KEY_ANISOTROPY,
            KEY_TEXTURES,
            KEY_SHADOW_QUALITY,
            KEY_BLOOM,
            KEY_MAP_LIGHTS,
            KEY_SKY,
            KEY_SHARPEN,
            KEY_BRIGHTNESS,
            KEY_FRAME_LATENCY,
            KEY_DISPLAY_MODE,
            KEY_MONITOR,
            KEY_EXCLUSIVE_MODE,
            KEY_GPU_BACKEND,
            KEY_GPU_POWER,
        ];
        let mut bag = serde_json::Map::new();
        for key in keys {
            bag.insert(key.into(), original.value_for(key).expect("a value"));
        }
        let read = Settings::from_values(&serde_json::Value::Object(bag));
        let (a, b) = (&read.video, &original.video);
        assert_eq!(
            (a.msaa, a.anisotropy, a.textures),
            (b.msaa, b.anisotropy, b.textures)
        );
        assert_eq!(a.shadow_level, b.shadow_level);
        assert_eq!(
            (a.bloom, a.map_lights, a.sky),
            (b.bloom, b.map_lights, b.sky)
        );
        assert_eq!((a.sharpen, a.brightness), (b.sharpen, b.brightness));
        assert_eq!(a.frame_latency, b.frame_latency);
        assert_eq!((a.exclusive, a.monitor), (b.exclusive, b.monitor));
        assert_eq!(a.exclusive_mode, b.exclusive_mode);
        assert_eq!(read.gpu.backend, original.gpu.backend);
        assert_eq!(read.gpu.low_power, original.gpu.low_power);
    }

    #[test]
    fn saved_counts_snap_to_ones_the_menu_can_show() {
        let s = Settings::from_values(&json!({
            "hassault.video.msaa": 5,
            "hassault.video.anisotropy": 11,
            "hassault.video.mapLights": 1000,
        }));
        assert!(MSAA_COUNTS.contains(&s.video.msaa));
        assert!(ANISOTROPY_LEVELS.contains(&s.video.anisotropy));
        assert_eq!(s.video.map_lights, 64);
    }

    #[test]
    fn an_exclusive_mode_parses_and_nonsense_is_the_desktop_mode() {
        assert_eq!(parse_mode("1920x1080@60000"), Some((1920, 1080, 60_000)));
        assert_eq!(parse_mode("big"), None);
        assert_eq!(format_mode((0, 0, 0)), "");
        let s = Settings::from_values(&json!({"hassault.video.exclusiveMode": "wide"}));
        assert_eq!(s.video.exclusive_mode, (0, 0, 0));
    }

    #[test]
    fn a_frame_cap_is_snapped_to_one_the_menu_can_show() {
        // Honouring 37 would leave the menu holding a value none of its steps can
        // reach, so the row would jump the first time it was touched.
        let s = Settings::from_values(&json!({"hassault.video.fpsLimit": 37}));
        assert!(FPS_LIMITS.contains(&s.video.fps_limit));
        let s = Settings::from_values(&json!({"hassault.video.fpsLimit": 144}));
        assert_eq!(s.video.fps_limit, 144);
        // Negative is not a slow cap, it is nonsense; uncapped is the honest read.
        let s = Settings::from_values(&json!({"hassault.video.fpsLimit": -5}));
        assert_eq!(s.video.fps_limit, 0);
    }

    #[test]
    fn a_saved_fov_is_clamped_rather_than_believed() {
        let s = Settings::from_values(&json!({"hassault.video.fov": 400.0}));
        assert_eq!(s.video.fov, FOV_RANGE.1);
        let s = Settings::from_values(&json!({"hassault.video.fov": 10.0}));
        assert_eq!(s.video.fov, FOV_RANGE.0);
    }
}
