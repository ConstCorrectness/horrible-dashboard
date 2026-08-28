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
pub const KEY_ANTIALIAS: &str = "hassault.video.antialias";
pub const KEY_SHADOWS: &str = "hassault.video.shadows";
pub const KEY_FPS_LIMIT: &str = "hassault.video.fpsLimit";
pub const KEY_CROSSHAIR_STYLE: &str = "hassault.crosshair.style";
pub const KEY_CROSSHAIR_SIZE: &str = "hassault.crosshair.size";
pub const KEY_CROSSHAIR_GAP: &str = "hassault.crosshair.gap";
pub const KEY_CROSSHAIR_THICKNESS: &str = "hassault.crosshair.thickness";
pub const KEY_CROSSHAIR_COLOR: &str = "hassault.crosshair.color";
pub const KEY_SHOW_HITBOXES: &str = "hassault.debug.hitboxes";

/// How much the renderer is allowed to spend on looking good.
///
/// The knob that actually costs something here is **not** the shading — this
/// scene is a few tens of thousands of untextured triangles and the fragment
/// work is trivial on anything with a discrete GPU. It is the sample count and
/// the render scale, which is why those are what a quality level moves. On an
/// integrated GPU at 1440p the difference between `Low` and `High` is real; on
/// the machine this was written on it is unmeasurable, and saying so is more
/// useful than implying a placebo does something.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Quality {
    Low,
    #[default]
    Medium,
    High,
}

impl Quality {
    pub const ALL: [Quality; 3] = [Quality::Low, Quality::Medium, Quality::High];

    pub fn label(self) -> &'static str {
        match self {
            Quality::Low => "LOW",
            Quality::Medium => "MEDIUM",
            Quality::High => "HIGH",
        }
    }

    fn key(self) -> &'static str {
        match self {
            Quality::Low => "low",
            Quality::Medium => "medium",
            Quality::High => "high",
        }
    }

    fn parse(s: &str) -> Quality {
        match s {
            "low" => Quality::Low,
            "high" => Quality::High,
            _ => Quality::Medium,
        }
    }

    /// Whether this level turns anti-aliasing on when a preset is applied.
    ///
    /// Only a *default*: `Video::antialias` is the value the renderer reads, and
    /// picking a preset is the only thing that writes this into it. Anti-aliasing
    /// used to be derived from the quality level with no way to say otherwise,
    /// which made the one setting with a measurable cost on an integrated GPU the
    /// one setting nobody could turn off without also flattening the shading.
    pub fn antialias(self) -> bool {
        matches!(self, Quality::High)
    }

    /// The exponential-squared fog's density, in inverse cubes.
    ///
    /// Denser is cheaper only in the sense that it hides distance — but it is
    /// also the most visible difference between the levels, and a quality
    /// setting whose effect is invisible is one people flip back and forth
    /// wondering whether it did anything.
    ///
    /// **High is exactly the browser's `FogExp2` density**, not a value picked
    /// to look close: at High the two clients are meant to be the same picture,
    /// and the lower levels trade distance for fill rate from there. A density
    /// rather than the linear end distance this used to return, because a linear
    /// ramp has an exponential one's shape nowhere along its length — see the
    /// fog note in `lighting.wgsl.inc`.
    pub fn fog_density(self) -> f32 {
        match self {
            Quality::Low => 0.0110,
            Quality::Medium => 0.0075,
            Quality::High => 0.0055,
        }
    }

    /// Shading detail, read by the fragment shader: 0 flat, 1 the directional
    /// wash, 2 the wash plus a rim highlight on edges facing away from it.
    pub fn detail(self) -> f32 {
        match self {
            Quality::Low => 0.0,
            Quality::Medium => 1.0,
            Quality::High => 2.0,
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
}

impl Default for Crosshair {
    fn default() -> Crosshair {
        Crosshair {
            style: CrosshairStyle::default(),
            size: 3.0,
            gap: 4.0,
            thickness: 0.6,
            color: CrosshairColor::default(),
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Video {
    pub fullscreen: bool,
    /// Fraction of the window's pixels the world is rendered at, 0.5–1.0. The
    /// HUD is **not** scaled with it — text drawn at half resolution and stretched
    /// is unreadable, and the HUD costs nothing to draw at native size.
    pub render_scale: f32,
    pub quality: Quality,
    /// Vertical sync. Off by default and first in the present-mode list, because
    /// a frame of queued latency is precisely what this client exists to avoid —
    /// but tearing is real, and somebody who can see it should be able to say so.
    pub vsync: bool,
    /// Vertical field of view, in **degrees**, before the scope divides it.
    ///
    /// 75 is the browser pane's, so the default is the same picture on both
    /// clients; the range is the one every shooter settled on. This is the knob
    /// people go looking for first and the only one on this page that changes how
    /// the game *plays* rather than how it looks — a wider view is more of the
    /// room and a smaller enemy in it.
    pub fov: f32,
    /// 4× multisampling — **on or off, and nothing between**.
    ///
    /// 1 and 4 are the only counts the WebGPU spec guarantees a format supports.
    /// `2` looks like the obvious middle and is not: this device reports
    /// `[1, 2, 4, 8]`, but only behind `TEXTURE_ADAPTER_SPECIFIC_FORMAT_FEATURES`
    /// — a feature this client deliberately does not request, because asking for
    /// more than the scene needs is how a client refuses to start on a perfectly
    /// capable integrated GPU. Without it, 2× is a validation error at pipeline
    /// creation, which is a **crash on the first frame**, not a slower one. It
    /// was exactly that, until a real run found it. So this is a `bool`: a field
    /// that cannot hold 2 cannot be set to 2 by a future edit either.
    pub antialias: bool,
    /// Whether world surfaces sample the sun's shadow map.
    ///
    /// **This is a look, not a frame rate**, and saying so is the point. The map
    /// is static and so is the sun, so `ShadowMap::new` bakes it once at load and
    /// no per-frame pass is skipped by turning this off — all that changes is
    /// whether the fragment shader takes the PCF taps. Offering it as a
    /// performance setting would be the placebo this file's other comments keep
    /// refusing to ship.
    pub shadows: bool,
    /// Frames per second to cap at, or **0 for uncapped**.
    ///
    /// Uncapped by default, which is the whole argument of the note on `MAX_DT`
    /// in `app.rs`: the shortest path from an input to a photon is the point. But
    /// uncapped on a laptop is a fan at full tilt and a thermal throttle a few
    /// minutes in, which costs more frames than the cap would have — and unlike
    /// vsync, a cap adds no queued latency, it only sleeps.
    pub fps_limit: u32,
}

impl Video {
    /// The multisample count the renderer builds every pipeline against.
    ///
    /// The one place the count is decided, so the pipelines, the scene texture
    /// and the resolve target cannot disagree about it — a mismatch there is a
    /// validation error at pipeline creation rather than a softer picture.
    pub fn samples(self) -> u32 {
        if self.antialias {
            4
        } else {
            1
        }
    }

    /// The presets, applied wholesale.
    ///
    /// Picking a quality level writes the individual knobs rather than shadowing
    /// them, so the menu never shows `HIGH` next to a row that contradicts it.
    /// Everything the level does not name — FOV, the frame cap, the render scale
    /// — is deliberately left alone: those are preferences about the machine and
    /// the player, not about how pretty the scene is.
    pub fn apply_preset(&mut self, quality: Quality) {
        self.quality = quality;
        self.antialias = quality.antialias();
    }
}

/// The FOV range, in degrees. Narrow enough that nobody can zoom out to a
/// fish-eye that renders every enemy a pixel wide and calls it a setting.
pub const FOV_RANGE: (f32, f32) = (70.0, 120.0);

/// The frame caps offered, `0` being uncapped. Not a free-form number: the useful
/// values are the refresh rates displays actually run at, plus one below all of
/// them for a laptop that would rather stay quiet.
pub const FPS_LIMITS: [u32; 6] = [0, 60, 120, 144, 240, 360];

impl Default for Video {
    fn default() -> Video {
        Video {
            // **Fullscreen by default.** A shooter that opens in a window with a
            // title bar is one you have to go and configure before it feels like
            // a game, and borderless fullscreen costs nothing to leave.
            fullscreen: true,
            render_scale: 1.0,
            quality: Quality::default(),
            vsync: false,
            fov: 75.0,
            antialias: Quality::default().antialias(),
            shadows: true,
            fps_limit: 0,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Settings {
    pub sensitivity: f32,
    pub crosshair: Crosshair,
    pub video: Video,
    /// Draw the served hitbox around every body.
    ///
    /// A setting rather than a build flag, because the question it answers —
    /// "is what I am shooting at where it is drawn?" — is one a *player* asks,
    /// usually right after a shot they were sure of. It is off by default: a
    /// permanent wireframe is a worse picture of the game than the game.
    pub show_hitboxes: bool,
}

impl Default for Settings {
    fn default() -> Settings {
        Settings {
            sensitivity: 1.0,
            crosshair: Crosshair::default(),
            video: Video::default(),
            show_hitboxes: false,
        }
    }
}

impl Settings {
    /// Read the node's settings bag, falling back to the defaults per key.
    ///
    /// Per *key*, not per document: a bag that has a crosshair colour and no
    /// render scale is the normal state of a fresh install, and a reader that
    /// gave up on the first missing key would leave every later one unread.
    pub fn from_values(values: &serde_json::Value) -> Settings {
        let mut s = Settings::default();
        let get = |key: &str| values.get(key);
        if let Some(v) = get(KEY_SENSITIVITY).and_then(|v| v.as_f64()) {
            s.sensitivity = (v as f32).clamp(0.05, 10.0);
        }
        if let Some(v) = get(KEY_FULLSCREEN).and_then(|v| v.as_bool()) {
            s.video.fullscreen = v;
        }
        if let Some(v) = get(KEY_RENDER_SCALE).and_then(|v| v.as_f64()) {
            s.video.render_scale = (v as f32).clamp(0.5, 1.0);
        }
        if let Some(v) = get(KEY_QUALITY).and_then(|v| v.as_str()) {
            s.video.quality = Quality::parse(v);
        }
        if let Some(v) = get(KEY_VSYNC).and_then(|v| v.as_bool()) {
            s.video.vsync = v;
        }
        if let Some(v) = get(KEY_FOV).and_then(|v| v.as_f64()) {
            s.video.fov = (v as f32).clamp(FOV_RANGE.0, FOV_RANGE.1);
        }
        // Read *after* the quality level, and that order is load-bearing: the
        // level carries a default for this one, so a bag holding both would
        // otherwise have the preset overwrite the explicit choice.
        if let Some(v) = get(KEY_ANTIALIAS).and_then(|v| v.as_bool()) {
            s.video.antialias = v;
        }
        if let Some(v) = get(KEY_SHADOWS).and_then(|v| v.as_bool()) {
            s.video.shadows = v;
        }
        if let Some(v) = get(KEY_FPS_LIMIT).and_then(|v| v.as_i64()) {
            // Snapped to the offered list rather than clamped: a cap of 37 is not
            // wrong so much as meaningless, and honouring it would make the menu
            // unable to show the value it is holding.
            let want = v.max(0) as u32;
            s.video.fps_limit = FPS_LIMITS
                .into_iter()
                .min_by_key(|c| c.abs_diff(want))
                .unwrap_or(0);
        }
        if let Some(v) = get(KEY_CROSSHAIR_STYLE).and_then(|v| v.as_str()) {
            s.crosshair.style = CrosshairStyle::parse(v);
        }
        if let Some(v) = get(KEY_CROSSHAIR_SIZE).and_then(|v| v.as_f64()) {
            s.crosshair.size = (v as f32).clamp(1.0, 12.0);
        }
        if let Some(v) = get(KEY_CROSSHAIR_GAP).and_then(|v| v.as_f64()) {
            s.crosshair.gap = (v as f32).clamp(0.0, 20.0);
        }
        if let Some(v) = get(KEY_CROSSHAIR_THICKNESS).and_then(|v| v.as_f64()) {
            s.crosshair.thickness = (v as f32).clamp(0.2, 3.0);
        }
        if let Some(v) = get(KEY_SHOW_HITBOXES).and_then(|v| v.as_bool()) {
            s.show_hitboxes = v;
        }
        if let Some(v) = get(KEY_CROSSHAIR_COLOR).and_then(|v| v.as_str()) {
            s.crosshair.color = CrosshairColor::parse(v);
        }
        s
    }

    /// The value to persist for one key, as JSON.
    pub fn value_for(&self, key: &str) -> Option<serde_json::Value> {
        use serde_json::json;
        Some(match key {
            KEY_SENSITIVITY => json!(self.sensitivity),
            KEY_FULLSCREEN => json!(self.video.fullscreen),
            KEY_RENDER_SCALE => json!(self.video.render_scale),
            KEY_QUALITY => json!(self.video.quality.key()),
            KEY_VSYNC => json!(self.video.vsync),
            KEY_FOV => json!(self.video.fov),
            KEY_ANTIALIAS => json!(self.video.antialias),
            KEY_SHADOWS => json!(self.video.shadows),
            KEY_FPS_LIMIT => json!(self.video.fps_limit),
            KEY_CROSSHAIR_STYLE => json!(self.crosshair.style.key()),
            KEY_CROSSHAIR_SIZE => json!(self.crosshair.size),
            KEY_CROSSHAIR_GAP => json!(self.crosshair.gap),
            KEY_CROSSHAIR_THICKNESS => json!(self.crosshair.thickness),
            KEY_CROSSHAIR_COLOR => json!(self.crosshair.color.key()),
            KEY_SHOW_HITBOXES => json!(self.show_hitboxes),
            _ => return None,
        })
    }
}

/// A background writer, so no setting change ever costs a frame.
///
/// One thread and a channel rather than a thread per write: a slider dragged
/// across its range produces a change per frame, and spawning sixty threads to
/// PUT sixty values is worse than the hitch it was avoiding.
pub struct SettingsWriter {
    tx: Option<Sender<(String, serde_json::Value)>>,
}

impl SettingsWriter {
    pub fn new(base: &str) -> SettingsWriter {
        let (tx, rx) = mpsc::channel::<(String, serde_json::Value)>();
        let base = base.to_string();
        thread::Builder::new()
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
        SettingsWriter { tx: Some(tx) }
    }

    /// A writer that goes nowhere, for tests and for `--check`.
    pub fn disabled() -> SettingsWriter {
        SettingsWriter { tx: None }
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
        assert_eq!(s.video.render_scale, 1.0);
        assert_eq!(s.sensitivity, 0.05);
        assert_eq!(s.crosshair.thickness, 0.2);
    }

    #[test]
    fn an_unknown_string_falls_back_rather_than_failing() {
        let s = Settings::from_values(&json!({
            "hassault.video.quality": "ultra",
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
            KEY_QUALITY,
            KEY_VSYNC,
            KEY_CROSSHAIR_STYLE,
            KEY_CROSSHAIR_SIZE,
            KEY_CROSSHAIR_GAP,
            KEY_CROSSHAIR_THICKNESS,
            KEY_CROSSHAIR_COLOR,
            KEY_SHOW_HITBOXES,
        ] {
            bag.insert(key.into(), original.value_for(key).expect("a value"));
        }
        let read = Settings::from_values(&serde_json::Value::Object(bag));
        assert_eq!(read.crosshair.style, original.crosshair.style);
        assert_eq!(read.crosshair.color, original.crosshair.color);
        assert_eq!(read.crosshair.gap, original.crosshair.gap);
        assert_eq!(read.video.quality, original.video.quality);
        assert_eq!(read.video.render_scale, original.video.render_scale);
        assert_eq!(read.video.vsync, original.video.vsync);
        assert_eq!(read.video.fullscreen, original.video.fullscreen);
        assert_eq!(read.sensitivity, original.sensitivity);
    }

    #[test]
    fn a_key_nobody_declared_has_no_value() {
        assert!(Settings::default().value_for("hassault.nope").is_none());
    }

    #[test]
    fn video_only_asks_for_sample_counts_the_spec_guarantees() {
        // **1 and 4 only.** Anything else needs
        // `TEXTURE_ADAPTER_SPECIFIC_FORMAT_FEATURES`, which this client does not
        // request, and a pipeline built with an unsupported count is a
        // validation error on the first frame rather than a slower one. A 2×
        // "middle" level crashed the client on a machine that reports
        // `[1, 2, 4, 8]` — the support list is not the guarantee.
        //
        // Now over the *setting* rather than over the quality level, because the
        // level no longer decides it: `antialias` is its own row, and the row is
        // where a 2 would get in.
        for antialias in [true, false] {
            let video = Video {
                antialias,
                ..Video::default()
            };
            assert!(
                matches!(video.samples(), 1 | 4),
                "antialias {antialias} asked for {}",
                video.samples()
            );
        }
    }

    #[test]
    fn a_preset_writes_the_rows_under_it_rather_than_shadowing_them() {
        // The whole reason `apply` returns a list. If picking HIGH left
        // `antialias` alone, the menu would show HIGH next to `ANTI-ALIASING
        // OFF`; if it wrote it without reporting the key, the level would come
        // back next session with the old sample count under it.
        let mut video = Video::default();
        video.apply_preset(Quality::High);
        assert_eq!(video.quality, Quality::High);
        assert!(video.antialias);
        video.apply_preset(Quality::Low);
        assert!(!video.antialias);
    }

    #[test]
    fn an_explicit_antialias_survives_a_bag_that_also_names_a_quality() {
        // Read order, and it is load-bearing: the level carries a default for
        // this key, so reading them the other way round would have the preset
        // quietly overwrite the choice the player actually made.
        let s = Settings::from_values(&json!({
            "hassault.video.quality": "high",
            "hassault.video.antialias": false,
        }));
        assert_eq!(s.video.quality, Quality::High);
        assert!(!s.video.antialias);
        assert_eq!(s.video.samples(), 1);
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
