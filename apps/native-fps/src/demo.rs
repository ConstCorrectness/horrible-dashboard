//! HorribleAssault Match Recording & Cinematic Replay Suite (.hademo)
//!
//! Provides:
//! 1. HADEMO demo recording format & serialization (tick-based player states, shots, kills, streaks).
//! 2. Playback controller with variable speed (0.1x slow-mo to 4.0x fast-forward), seeking, and frag bookmarks.
//! 3. 6-DOF Cinematic Drone Freecam with camera roll (Q/E), FOV zoom lens (15° to 120°), and inertia smoothing.
//! 4. Catmull-Rom spline dolly keyframe interpolation for montage cinematic camera sweeps.
//!
//! **One file format, the browser's** (`demo.ts`). This module used to have
//! its own: snake_case keys, numeric player ids and a 60 Hz tick, so a demo
//! recorded by one client was a parse error in the other — and nothing here
//! was wired to anything, so the difference never showed. The structs below
//! are `demo.ts`'s interfaces field for field, and `demo-vectors.json` (written
//! by `demo.test.ts`) pins both the parse and the sampling between them.
//!
//! Times are milliseconds since the recording started, as `f64` because that
//! is what a JSON number is on the other side. Nothing here reads a clock: the
//! recorder and the player are told the time, so a test can step them exactly
//! and an export can step them at a fixed rate.
//!
//! Angles in a frame are the wire's: radians, yaw 0 = +x (`PlayerRow`).

use serde::{Deserialize, Deserializer, Serialize};
use std::collections::BTreeMap;

use crate::api::WeaponSpec;
use crate::protocol::PlayerRow;

/// The magic every `.hademo` starts its header with.
pub const MAGIC: &str = "HADEMO";
/// Frames a second the recorder keeps. Thirty, not the display's rate:
/// playback interpolates between frames, and a minute at sixty with ten bodies
/// is a file nobody wants to share. `demo.ts`'s `tickRate`.
pub const TICK_RATE: u32 = 30;
/// How far before a frag a bookmark seeks, for montage pacing.
pub const BOOKMARK_LEAD_MS: f64 = 2000.0;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DemoHeader {
    pub magic: String,
    pub version: u32,
    pub map: String,
    /// Wall-clock milliseconds since the epoch, when recording began.
    pub recorded_at: f64,
    pub tick_rate: u32,
    #[serde(default)]
    pub player_names: BTreeMap<String, String>,
    pub duration_ms: f64,
    /// The recorder's own id in `frames`: whose eyes playback starts behind.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "opt_id"
    )]
    pub pov: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DemoPlayerSnapshot {
    /// The wire's player id. A number in a hand-written file is read as its
    /// decimal string, as `parseDemo` does.
    #[serde(deserialize_with = "id")]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub team: i32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
    pub pitch: f32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub roll: Option<f32>,
    /// Index into the served weapon table at recording time.
    #[serde(default)]
    pub weapon: i32,
    /// The same weapon by id, preferred on playback: an index is only good for
    /// the table it was taken from.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub weapon_id: Option<String>,
    /// Zoom step, 0 unscoped. The recorder's own body only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scoped: Option<i32>,
    /// The magnification that step meant when it was recorded; 1 unscoped.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub zoom: Option<f32>,
    #[serde(default)]
    pub hp: f32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub crouching: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sprinting: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sliding: Option<bool>,
    /// Crouch, 0 standing to 1 crouched (`PlayerRow::crouch`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub crouch: Option<f32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub alive: Option<bool>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DemoEvent {
    pub tick: u32,
    pub timestamp: f64,
    /// `kill`, `shot`, `hit`, `grenade` or `streak`.
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "opt_id"
    )]
    pub killer_id: Option<String>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "opt_id"
    )]
    pub victim_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub killer: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub victim: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub weapon: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub head: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nutshot: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub streak: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub origin: Option<[f32; 3]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target: Option<[f32; 3]>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DemoBookmark {
    pub tick: u32,
    pub timestamp: f64,
    pub label: String,
    /// `kill`, `streak`, `nutshot` or `headshot`.
    #[serde(rename = "type")]
    pub kind: String,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "opt_id"
    )]
    pub target_player_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DemoFrame {
    pub tick: u32,
    pub timestamp: f64,
    #[serde(default)]
    pub players: Vec<DemoPlayerSnapshot>,
    #[serde(default)]
    pub events: Vec<DemoEvent>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DemoData {
    pub header: DemoHeader,
    pub frames: Vec<DemoFrame>,
    #[serde(default)]
    pub bookmarks: Vec<DemoBookmark>,
}

/// A player id as either a string or a number.
#[derive(Deserialize)]
#[serde(untagged)]
enum Id {
    Text(String),
    Number(serde_json::Number),
}

impl Id {
    fn text(self) -> String {
        match self {
            Id::Text(s) => s,
            Id::Number(n) => n.to_string(),
        }
    }
}

fn id<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    Id::deserialize(d).map(Id::text)
}

fn opt_id<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    Option::<Id>::deserialize(d).map(|id| id.map(Id::text))
}

/// Why a file is not a demo this build can play, in words for the player.
#[derive(Debug, Clone, PartialEq)]
pub struct DemoError(pub String);

impl std::fmt::Display for DemoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// A `.hademo` file's text, checked and normalised: refused with a reason
/// rather than loaded into a player that will draw nothing. `parseDemo`, with
/// the same three refusals in the same words.
pub fn parse_demo(text: &str) -> Result<DemoData, DemoError> {
    let raw: serde_json::Value = serde_json::from_str(text)
        .map_err(|_| DemoError("This file is not a demo: it is not JSON.".into()))?;
    if raw.pointer("/header/magic").and_then(|m| m.as_str()) != Some(MAGIC) {
        return Err(DemoError("This file is not a .hademo demo.".into()));
    }
    if raw
        .get("frames")
        .and_then(|f| f.as_array())
        .is_none_or(|f| f.is_empty())
    {
        return Err(DemoError(
            "This demo has no frames, so there is nothing to play. Demos saved before \
             recording was fixed are all empty; record a new one."
                .into(),
        ));
    }
    let mut demo: DemoData = serde_json::from_value(raw)
        .map_err(|e| DemoError(format!("This demo could not be read: {e}.")))?;
    let last = demo.frames.last().map_or(0.0, |f| f.timestamp);
    demo.header.duration_ms = demo.header.duration_ms.max(last);
    Ok(demo)
}

/// What the replay list shows for a file, without keeping its frames.
#[derive(Clone, Debug, PartialEq)]
pub struct DemoSummary {
    pub map: String,
    pub recorded_at: f64,
    pub duration_ms: f64,
    pub kills: usize,
    /// The recorder's name, when the header says who that was.
    pub pov_name: Option<String>,
}

impl DemoSummary {
    pub fn of(demo: &DemoData) -> DemoSummary {
        let h = &demo.header;
        DemoSummary {
            map: h.map.clone(),
            recorded_at: h.recorded_at,
            duration_ms: h.duration_ms,
            kills: demo.bookmarks.len(),
            pov_name: h
                .pov
                .as_ref()
                .and_then(|id| h.player_names.get(id).cloned()),
        }
    }
}

/// Where recordings are kept: a `demos` folder beside the local settings file
/// (`hAssault/demos` in the platform's per-user config directory). `None` on a
/// platform that names no such directory, where nothing can be saved.
pub fn demos_dir() -> Option<std::path::PathBuf> {
    Some(
        crate::settings::local_settings_path()?
            .parent()?
            .join("demos"),
    )
}

/// `dust2-20261114-123015.hademo`: the map, then when recording began, in UTC.
/// Sorts by time within a map, and a name that says where and when is all a
/// file manager has to go on.
pub fn file_name(map: &str, recorded_at_ms: f64) -> String {
    let map = map.strip_prefix("hd_").unwrap_or(map);
    let safe: String = map
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    let (date, time) = utc(recorded_at_ms);
    format!(
        "{}-{date}-{time}.hademo",
        if safe.is_empty() { "demo" } else { &safe }
    )
}

/// Epoch milliseconds as `YYYYMMDD` and `HHMMSS`, UTC. The civil-from-days
/// conversion (Howard Hinnant's), rather than a date crate for one filename.
fn utc(ms: f64) -> (String, String) {
    let secs = (ms / 1000.0).floor() as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    (
        format!("{year:04}{month:02}{day:02}"),
        format!("{:02}{:02}{:02}", rem / 3600, rem % 3600 / 60, rem % 60),
    )
}

/// Epoch milliseconds as `2026-11-14  12:30 UTC`, for the replay list.
pub fn utc_label(ms: f64) -> String {
    let (date, time) = utc(ms);
    format!(
        "{}-{}-{}  {}:{} UTC",
        &date[..4],
        &date[4..6],
        &date[6..],
        &time[..2],
        &time[2..4]
    )
}

/// Write a demo into `dir`, compact (a minute pretty-printed is several times
/// the size), and say where it went.
pub fn save(dir: &std::path::Path, demo: &DemoData) -> std::io::Result<std::path::PathBuf> {
    std::fs::create_dir_all(dir)?;
    let path = dir.join(file_name(&demo.header.map, demo.header.recorded_at));
    let json = serde_json::to_vec(demo).map_err(std::io::Error::other)?;
    // Through a temporary name, so a crash mid-write leaves no half a demo
    // that the list would then refuse.
    let partial = path.with_extension("hademo.part");
    std::fs::write(&partial, json)?;
    std::fs::rename(&partial, &path)?;
    Ok(path)
}

/// Every demo in `dir` that parses, newest first, with what the list shows.
/// One that does not parse is left out rather than listed as broken: it is
/// either half-written or not ours.
pub fn list(dir: &std::path::Path) -> Vec<(std::path::PathBuf, DemoSummary)> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<_> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "hademo"))
        .filter_map(|p| {
            let text = std::fs::read_to_string(&p).ok()?;
            let demo = parse_demo(&text).ok()?;
            Some((p, DemoSummary::of(&demo)))
        })
        .collect();
    out.sort_by(|a, b| b.1.recorded_at.total_cmp(&a.1.recorded_at));
    out
}

/// Interpolate angle without 360-degree wrap-around artifacts
pub fn interpolate_angle(a0: f32, a1: f32, t: f32) -> f32 {
    let mut diff = (a1 - a0) % 360.0;
    if diff > 180.0 {
        diff -= 360.0;
    }
    if diff < -180.0 {
        diff += 360.0;
    }
    a0 + diff * t
}

/// As `interpolate_angle`, in radians: the shortest way round.
pub fn interpolate_radians(a0: f32, a1: f32, t: f32) -> f32 {
    let turn = std::f32::consts::TAU;
    let mut diff = (a1 - a0) % turn;
    if diff > std::f32::consts::PI {
        diff -= turn;
    }
    if diff < -std::f32::consts::PI {
        diff += turn;
    }
    a0 + diff * t
}

/// A body the renderer drew, as a frame keeps it.
pub fn snapshot_from_row(row: &PlayerRow, weapons: &[WeaponSpec]) -> DemoPlayerSnapshot {
    DemoPlayerSnapshot {
        id: row.id.clone(),
        name: row.name.clone(),
        team: row.team,
        x: row.x,
        y: row.y,
        z: row.z,
        yaw: row.yaw,
        pitch: row.pitch,
        weapon: row.weapon,
        weapon_id: usize::try_from(row.weapon)
            .ok()
            .and_then(|i| weapons.get(i))
            .map(|w| w.id.clone()),
        hp: row.hp,
        crouch: Some(row.crouch),
        alive: Some(row.alive),
        ..Default::default()
    }
}

/// A frame's body, back in the shape the renderer draws. The weapon by id
/// where the table still has it — a roster that grew since recording would
/// otherwise hand the body a different gun — and by index where it does not.
pub fn row_from_snapshot(s: &DemoPlayerSnapshot, weapons: &[WeaponSpec]) -> PlayerRow {
    let by_id = s
        .weapon_id
        .as_deref()
        .and_then(|id| weapons.iter().position(|w| w.id == id));
    PlayerRow {
        id: s.id.clone(),
        name: s.name.clone(),
        team: s.team,
        x: s.x,
        y: s.y,
        z: s.z,
        yaw: s.yaw,
        pitch: s.pitch,
        hp: s.hp,
        alive: s.alive.unwrap_or(s.hp > 0.0),
        ground: true,
        crouch: s
            .crouch
            .unwrap_or(if s.crouching == Some(true) { 1.0 } else { 0.0 }),
        weapon: by_id.map_or(s.weapon, |i| i as i32),
        ..Default::default()
    }
}

/// 6-DOF Drone Freecam Controller
#[derive(Clone, Debug)]
pub struct DroneFreecam {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
    pub pitch: f32,
    pub roll: f32,
    pub fov: f32,

    pub vx: f32,
    pub vy: f32,
    pub vz: f32,
    pub v_roll: f32,

    pub speed: f32,
    pub friction: f32,
    pub angular_friction: f32,
    pub active: bool,
}

impl Default for DroneFreecam {
    fn default() -> Self {
        Self {
            x: 0.0,
            y: 0.0,
            z: 5.0,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            fov: 90.0,
            vx: 0.0,
            vy: 0.0,
            vz: 0.0,
            v_roll: 0.0,
            speed: 12.0,
            friction: 8.0,
            angular_friction: 12.0,
            active: false,
        }
    }
}

impl DroneFreecam {
    #[allow(clippy::too_many_arguments)]
    pub fn set_position(
        &mut self,
        x: f32,
        y: f32,
        z: f32,
        yaw: f32,
        pitch: f32,
        roll: f32,
        fov: f32,
    ) {
        self.x = x;
        self.y = y;
        self.z = z;
        self.yaw = yaw;
        self.pitch = pitch;
        self.roll = roll;
        self.fov = fov;
        self.vx = 0.0;
        self.vy = 0.0;
        self.vz = 0.0;
        self.v_roll = 0.0;
    }

    #[allow(clippy::too_many_arguments)]
    pub fn update(
        &mut self,
        dt: f32,
        forward: f32,
        strafe: f32,
        vertical: f32,
        yaw_delta: f32,
        pitch_delta: f32,
        roll_input: f32,
        fov_delta: f32,
        boost: bool,
    ) {
        if !self.active {
            return;
        }
        let dt = dt.min(0.05);
        let move_speed = self.speed * if boost { 2.5 } else { 1.0 };

        let rad_yaw = self.yaw.to_radians();
        let forward_x = -rad_yaw.sin();
        let forward_y = rad_yaw.cos();
        let right_x = rad_yaw.cos();
        let right_y = rad_yaw.sin();

        let ax = (forward_x * forward + right_x * strafe) * move_speed;
        let ay = (forward_y * forward + right_y * strafe) * move_speed;
        let az = vertical * move_speed;

        self.vx += ax * dt * 10.0;
        self.vy += ay * dt * 10.0;
        self.vz += az * dt * 10.0;

        let damp = (1.0 - self.friction * dt).max(0.0);
        self.vx *= damp;
        self.vy *= damp;
        self.vz *= damp;

        self.x += self.vx * dt;
        self.y += self.vy * dt;
        self.z += self.vz * dt;

        self.yaw += yaw_delta;
        self.pitch = (self.pitch + pitch_delta).clamp(-89.9, 89.9);

        self.v_roll += roll_input * 180.0 * dt;
        let roll_damp = (1.0 - self.angular_friction * dt).max(0.0);
        self.v_roll *= roll_damp;
        self.roll += self.v_roll * dt;

        if fov_delta.abs() > 0.001 {
            self.fov = (self.fov + fov_delta).clamp(15.0, 120.0);
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DollyKeyframe {
    pub time: f32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
    pub pitch: f32,
    pub roll: f32,
    pub fov: f32,
}

/// Catmull-Rom cubic interpolation between 4 points
pub fn catmull_rom(p0: f32, p1: f32, p2: f32, p3: f32, t: f32) -> f32 {
    let t2 = t * t;
    let t3 = t2 * t;
    0.5 * (2.0 * p1
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
}

/// Catmull-Rom Spline Dolly Keyframe Path
#[derive(Clone, Debug, Default)]
pub struct DollyPath {
    pub keyframes: Vec<DollyKeyframe>,
}

impl DollyPath {
    pub fn clear(&mut self) {
        self.keyframes.clear();
    }

    pub fn add_keyframe(&mut self, kf: DollyKeyframe) {
        self.keyframes.push(kf);
        self.keyframes.sort_by(|a, b| a.time.total_cmp(&b.time));
    }

    pub fn evaluate(&self, time: f32) -> Option<DollyKeyframe> {
        let (first, last) = (self.keyframes.first()?, self.keyframes.last()?);
        if self.keyframes.len() == 1 || time <= first.time {
            return Some(first.clone());
        }
        if time >= last.time {
            return Some(last.clone());
        }
        let idx = self
            .keyframes
            .windows(2)
            .position(|w| time >= w[0].time && time <= w[1].time)
            .unwrap_or(0);
        let k0 = &self.keyframes[idx.saturating_sub(1)];
        let k1 = &self.keyframes[idx];
        let k2 = &self.keyframes[idx + 1];
        let k3 = &self.keyframes[(idx + 2).min(self.keyframes.len() - 1)];
        let span = k2.time - k1.time;
        let t = if span > 0.0 {
            (time - k1.time) / span
        } else {
            0.0
        };
        Some(DollyKeyframe {
            time,
            x: catmull_rom(k0.x, k1.x, k2.x, k3.x, t),
            y: catmull_rom(k0.y, k1.y, k2.y, k3.y, t),
            z: catmull_rom(k0.z, k1.z, k2.z, k3.z, t),
            yaw: interpolate_angle(k1.yaw, k2.yaw, t),
            pitch: interpolate_angle(k1.pitch, k2.pitch, t),
            roll: interpolate_angle(k1.roll, k2.roll, t),
            fov: catmull_rom(k0.fov, k1.fov, k2.fov, k3.fov, t),
        })
    }
}

/// Match Demo Recorder. `DemoRecorder` in `demo.ts`, told the time rather
/// than reading `performance.now()`.
#[derive(Clone, Debug, Default)]
pub struct DemoRecorder {
    header: Option<DemoHeader>,
    frames: Vec<DemoFrame>,
    bookmarks: Vec<DemoBookmark>,
    tick: u32,
    started: f64,
    pending: Vec<DemoEvent>,
    last_frame: Option<f64>,
}

impl DemoRecorder {
    /// Begin a recording at `now` (any monotonic clock, in ms) of `map`, seen
    /// through `pov`'s eyes. `recorded_at` is the wall clock, for the header.
    pub fn start(
        &mut self,
        map: &str,
        player_names: BTreeMap<String, String>,
        pov: Option<String>,
        now: f64,
        recorded_at: f64,
    ) {
        *self = DemoRecorder {
            header: Some(DemoHeader {
                magic: MAGIC.to_string(),
                version: 1,
                map: map.to_string(),
                recorded_at,
                tick_rate: TICK_RATE,
                player_names,
                duration_ms: 0.0,
                pov,
            }),
            started: now,
            ..DemoRecorder::default()
        };
    }

    pub fn is_recording(&self) -> bool {
        self.header.is_some()
    }

    /// Milliseconds recorded so far.
    pub fn elapsed(&self, now: f64) -> f64 {
        if self.is_recording() {
            now - self.started
        } else {
            0.0
        }
    }

    /// Whether a frame is owed: one per `1 / TICK_RATE`, whatever the frame rate.
    pub fn due(&self, now: f64) -> bool {
        self.is_recording()
            && self
                .last_frame
                .is_none_or(|at| now - at >= 1000.0 / TICK_RATE as f64)
    }

    /// File an event against the next frame. A kill also becomes a bookmark,
    /// labelled as `demo.ts` labels it.
    pub fn record_event(&mut self, now: f64, mut event: DemoEvent) {
        if !self.is_recording() {
            return;
        }
        event.tick = self.tick;
        event.timestamp = now - self.started;
        if event.kind == "kill" {
            let (mut label, mut kind) = if event.head == Some(true) {
                ("HEADSHOT".to_string(), "headshot")
            } else if event.nutshot == Some(true) {
                ("NUTSHOT".to_string(), "nutshot")
            } else {
                ("KILL".to_string(), "kill")
            };
            if let Some(streak) = event.streak.filter(|&s| s > 1) {
                label = format!("STREAK x{streak}");
                kind = "streak";
            }
            self.bookmarks.push(DemoBookmark {
                tick: event.tick,
                timestamp: event.timestamp,
                label,
                kind: kind.to_string(),
                target_player_id: event.killer_id.clone(),
            });
        }
        self.pending.push(event);
    }

    pub fn record_frame(&mut self, now: f64, players: Vec<DemoPlayerSnapshot>) {
        if !self.is_recording() {
            return;
        }
        // On a grid of the tick, not from whenever this frame happened to be
        // drawn: `demo.ts` restarts the interval at every frame, so at 250 fps
        // each one lands 36 ms after the last and a "30 Hz" demo keeps 28. A
        // recorder that fell more than a tick behind (a hitch) starts the grid
        // again rather than bursting to catch up.
        let interval = 1000.0 / TICK_RATE as f64;
        self.last_frame = Some(match self.last_frame {
            Some(at) if now - at < 2.0 * interval => at + interval,
            _ => now,
        });
        self.frames.push(DemoFrame {
            tick: self.tick,
            timestamp: now - self.started,
            players,
            events: std::mem::take(&mut self.pending),
        });
        self.tick += 1;
    }

    /// Stop, and hand back the demo. `None` when nothing was recording.
    pub fn stop(&mut self, now: f64) -> Option<DemoData> {
        let mut header = self.header.take()?;
        header.duration_ms = now - self.started;
        Some(DemoData {
            header,
            frames: std::mem::take(&mut self.frames),
            bookmarks: std::mem::take(&mut self.bookmarks),
        })
    }
}

/// Replay Playback Controller. `DemoPlayer` in `demo.ts`.
#[derive(Clone, Debug)]
pub struct DemoPlayer {
    pub demo: DemoData,
    pub is_playing: bool,
    pub playback_speed: f32,
    pub current_time_ms: f64,
    pub clean_view: bool,
    /// Whose eyes the camera is behind.
    pub spectator_target_id: Option<String>,
}

impl DemoPlayer {
    pub fn new(demo: DemoData) -> DemoPlayer {
        let spectator_target_id = demo
            .header
            .pov
            .clone()
            .or_else(|| Some(demo.frames.first()?.players.first()?.id.clone()));
        DemoPlayer {
            demo,
            is_playing: false,
            playback_speed: 1.0,
            current_time_ms: 0.0,
            clean_view: false,
            spectator_target_id,
        }
    }

    pub fn duration_ms(&self) -> f64 {
        self.demo.header.duration_ms
    }

    /// At the end and stopped: Play then starts again from the top.
    pub fn ended(&self) -> bool {
        self.current_time_ms >= self.duration_ms()
    }

    pub fn play(&mut self) {
        self.is_playing = true;
    }

    pub fn pause(&mut self) {
        self.is_playing = false;
    }

    pub fn toggle_play(&mut self) {
        if !self.is_playing && self.ended() {
            self.current_time_ms = 0.0;
        }
        self.is_playing = !self.is_playing;
    }

    pub fn set_speed(&mut self, speed: f32) {
        self.playback_speed = speed.clamp(0.1, 4.0);
    }

    pub fn seek(&mut self, time_ms: f64) {
        self.current_time_ms = time_ms.clamp(0.0, self.duration_ms());
    }

    pub fn seek_relative(&mut self, seconds: f64) {
        self.seek(self.current_time_ms + seconds * 1000.0);
    }

    /// Two seconds before the frag, following whoever made it.
    pub fn seek_to_bookmark(&mut self, index: usize) {
        let Some(bm) = self.demo.bookmarks.get(index).cloned() else {
            return;
        };
        self.seek((bm.timestamp - BOOKMARK_LEAD_MS).max(0.0));
        if bm.target_player_id.is_some() {
            self.spectator_target_id = bm.target_player_id;
        }
    }

    /// The first bookmark whose lead-in starts after now, wrapping to the
    /// first — so pressing the key repeatedly walks the frags in order.
    pub fn next_bookmark(&self) -> Option<usize> {
        let marks = &self.demo.bookmarks;
        if marks.is_empty() {
            return None;
        }
        let now = self.current_time_ms;
        marks
            .iter()
            .position(|b| (b.timestamp - BOOKMARK_LEAD_MS).max(0.0) > now + 1.0)
            .or(Some(0))
    }

    pub fn toggle_clean_view(&mut self) -> bool {
        self.clean_view = !self.clean_view;
        self.clean_view
    }

    pub fn update(&mut self, dt: f32) {
        if !self.is_playing {
            return;
        }
        self.current_time_ms += dt as f64 * 1000.0 * self.playback_speed as f64;
        if self.current_time_ms >= self.duration_ms() {
            self.current_time_ms = self.duration_ms();
            self.is_playing = false;
        }
    }

    /// The bodies at the current time, interpolated between the two frames
    /// either side, and the events of the earlier one.
    pub fn sample(&self) -> (Vec<DemoPlayerSnapshot>, &[DemoEvent]) {
        let frames = &self.demo.frames;
        let (Some(first), Some(last)) = (frames.first(), frames.last()) else {
            return (Vec::new(), &[]);
        };
        let t = self.current_time_ms;
        if t <= first.timestamp {
            return (first.players.clone(), &first.events);
        }
        if t >= last.timestamp {
            return (last.players.clone(), &last.events);
        }
        // The last frame at or before `t`; `t` is strictly inside, so there is
        // always one after it.
        let i0 = frames
            .partition_point(|f| f.timestamp <= t)
            .saturating_sub(1);
        let (f0, f1) = (&frames[i0], &frames[(i0 + 1).min(frames.len() - 1)]);
        let span = f1.timestamp - f0.timestamp;
        let a = if span > 0.0 {
            ((t - f0.timestamp) / span) as f32
        } else {
            0.0
        };
        let players = f0
            .players
            .iter()
            .map(|p0| match f1.players.iter().find(|p| p.id == p0.id) {
                None => p0.clone(),
                Some(p1) => {
                    // A step, not a blend, for anything that has no halfway:
                    // half-way into a scope is not a view anybody had.
                    let step = if a < 0.5 { p0 } else { p1 };
                    DemoPlayerSnapshot {
                        id: p0.id.clone(),
                        name: p0.name.clone(),
                        team: p0.team,
                        x: p0.x + (p1.x - p0.x) * a,
                        y: p0.y + (p1.y - p0.y) * a,
                        z: p0.z + (p1.z - p0.z) * a,
                        yaw: interpolate_radians(p0.yaw, p1.yaw, a),
                        pitch: interpolate_radians(p0.pitch, p1.pitch, a),
                        roll: Some(interpolate_radians(
                            p0.roll.unwrap_or(0.0),
                            p1.roll.unwrap_or(0.0),
                            a,
                        )),
                        weapon: step.weapon,
                        weapon_id: step.weapon_id.clone(),
                        scoped: step.scoped,
                        zoom: step.zoom,
                        hp: step.hp,
                        crouching: step.crouching,
                        sprinting: step.sprinting,
                        sliding: step.sliding,
                        crouch: Some(
                            p0.crouch.unwrap_or(0.0)
                                + (p1.crouch.unwrap_or(0.0) - p0.crouch.unwrap_or(0.0)) * a,
                        ),
                        alive: step.alive,
                    }
                }
            })
            .collect();
        (players, &f0.events)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Written by `demo-vectors.test.ts`. Included rather than read at run
    /// time, so it is the same file rather than a copy.
    const VECTORS: &str =
        include_str!("../../../packages/core/src/modules/hassault/__tests__/demo-vectors.json");

    /// The same JSON, numbers to within single precision: this side keeps
    /// positions and angles as `f32`, the browser as doubles.
    fn same(got: &serde_json::Value, want: &serde_json::Value, at: &str) {
        use serde_json::Value;
        match (got, want) {
            (Value::Number(g), Value::Number(w)) => {
                let (g, w) = (g.as_f64().unwrap(), w.as_f64().unwrap());
                assert!((g - w).abs() <= 1e-5 * w.abs().max(1.0), "{at}: {g} vs {w}");
            }
            (Value::Array(g), Value::Array(w)) => {
                assert_eq!(g.len(), w.len(), "{at}: length");
                for (i, (g, w)) in g.iter().zip(w).enumerate() {
                    same(g, w, &format!("{at}[{i}]"));
                }
            }
            (Value::Object(g), Value::Object(w)) => {
                let keys = |o: &serde_json::Map<String, Value>| {
                    o.keys().cloned().collect::<std::collections::BTreeSet<_>>()
                };
                assert_eq!(keys(g), keys(w), "{at}: keys");
                for (k, w) in w {
                    same(&g[k], w, &format!("{at}.{k}"));
                }
            }
            _ => assert_eq!(got, want, "{at}"),
        }
    }

    #[test]
    fn the_browsers_file_parses_and_plays_back_the_same() {
        let v: serde_json::Value = serde_json::from_str(VECTORS).unwrap();
        let demo = parse_demo(&v["file"].to_string()).expect("the fixture parses");
        same(
            &serde_json::to_value(&demo).unwrap(),
            &v["parsed"],
            "parsed",
        );
        let mut player = DemoPlayer::new(demo);
        assert_eq!(
            player.spectator_target_id.as_deref(),
            v["spectating"].as_str()
        );
        for sample in v["samples"].as_array().unwrap() {
            let t = sample["t"].as_f64().unwrap();
            player.seek(t);
            let (players, events) = player.sample();
            let at = format!("at {t} ms");
            same(
                &serde_json::to_value(&players).unwrap(),
                &sample["players"],
                &at,
            );
            same(
                &serde_json::to_value(events).unwrap(),
                &sample["events"],
                &at,
            );
        }
    }

    #[test]
    fn test_catmull_rom_and_interpolate_angle() {
        let p = catmull_rom(0.0, 10.0, 20.0, 30.0, 0.5);
        assert!((p - 15.0).abs() < 1e-4);

        let angle = interpolate_angle(350.0, 10.0, 0.5);
        assert!(((angle + 360.0) % 360.0).abs() < 1e-4);
    }

    #[test]
    fn yaw_is_blended_the_short_way_round_in_radians() {
        // 0.1 rad either side of the seam: the midpoint is the seam, not π.
        let a = interpolate_radians(std::f32::consts::TAU - 0.1, 0.1, 0.5);
        assert!((a - std::f32::consts::TAU).abs() < 1e-5, "{a}");
    }

    #[test]
    fn test_drone_freecam() {
        let mut drone = DroneFreecam {
            active: true,
            ..Default::default()
        };
        drone.set_position(0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 90.0);

        drone.update(0.016, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, -10.0, false);
        assert!(drone.y > 0.0, "should move forward along +y");
        assert!(drone.v_roll > 0.0, "should apply roll acceleration");
        assert!((drone.fov - 80.0).abs() < 1e-3, "should zoom FOV");
    }

    #[test]
    fn test_dolly_path() {
        let key = |time: f32, x: f32, fov: f32| DollyKeyframe {
            time,
            x,
            y: x * 2.0,
            z: 2.0,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            fov,
        };
        let mut path = DollyPath::default();
        path.add_keyframe(key(2.0, 10.0, 75.0));
        path.add_keyframe(key(0.0, 0.0, 90.0));
        let eval = path.evaluate(1.0).expect("evaluated keyframe");
        assert!(eval.x > 0.0 && eval.x < 10.0);
        assert!(eval.fov < 90.0);
    }

    fn body(id: &str, x: f32) -> DemoPlayerSnapshot {
        DemoPlayerSnapshot {
            id: id.into(),
            name: id.to_uppercase(),
            x,
            hp: 100.0,
            alive: Some(true),
            ..Default::default()
        }
    }

    #[test]
    fn the_recorder_keeps_thirty_frames_a_second_whatever_the_frame_rate() {
        let mut r = DemoRecorder::default();
        assert!(!r.due(0.0), "not recording");
        r.start(
            "hd_dust2",
            BTreeMap::new(),
            Some("me".into()),
            1000.0,
            1.7e12,
        );
        // A frame every 4 ms (250 fps) for a second.
        let mut ms = 1000.0;
        while ms < 2000.0 {
            if r.due(ms) {
                r.record_frame(ms, vec![body("me", 0.0)]);
            }
            ms += 4.0;
        }
        let demo = r.stop(2000.0).expect("a demo");
        assert!(
            (29..=31).contains(&demo.frames.len()),
            "{}",
            demo.frames.len()
        );
        assert_eq!(demo.header.duration_ms, 1000.0);
        assert_eq!(demo.frames[0].timestamp, 0.0, "times are from the start");
        assert_eq!(demo.header.pov.as_deref(), Some("me"));
        assert!(r.stop(3000.0).is_none(), "stopping twice gives nothing");
    }

    #[test]
    fn a_kill_is_filed_on_the_next_frame_and_bookmarked() {
        let mut r = DemoRecorder::default();
        r.start("hd_dust2", BTreeMap::new(), None, 0.0, 0.0);
        r.record_frame(0.0, vec![body("a", 0.0)]);
        r.record_event(
            50.0,
            DemoEvent {
                kind: "kill".into(),
                killer_id: Some("a".into()),
                head: Some(true),
                ..Default::default()
            },
        );
        r.record_frame(100.0, vec![body("a", 5.0)]);
        let demo = r.stop(100.0).unwrap();
        assert!(demo.frames[0].events.is_empty());
        assert_eq!(demo.frames[1].events[0].timestamp, 50.0);
        assert_eq!(demo.bookmarks[0].label, "HEADSHOT");
        assert_eq!(demo.bookmarks[0].kind, "headshot");
        assert_eq!(demo.bookmarks[0].target_player_id.as_deref(), Some("a"));
    }

    #[test]
    fn playback_interpolates_and_stops_at_the_end() {
        let mut r = DemoRecorder::default();
        r.start("hd_dust2", BTreeMap::new(), None, 0.0, 0.0);
        r.record_frame(0.0, vec![body("a", 0.0)]);
        r.record_frame(100.0, vec![body("a", 10.0)]);
        let mut p = DemoPlayer::new(r.stop(100.0).unwrap());
        assert_eq!(p.spectator_target_id.as_deref(), Some("a"));
        p.play();
        p.update(0.025);
        assert!((p.sample().0[0].x - 2.5).abs() < 1e-4);
        p.set_speed(10.0);
        assert_eq!(p.playback_speed, 4.0, "clamped");
        p.update(1.0);
        assert!(p.ended() && !p.is_playing);
        p.toggle_play();
        assert_eq!(p.current_time_ms, 0.0, "play at the end starts over");
    }

    #[test]
    fn the_three_refusals_are_the_browsers() {
        assert!(parse_demo("nope").unwrap_err().0.contains("not JSON"));
        assert!(parse_demo("{}").unwrap_err().0.contains("not a .hademo"));
        let empty = r#"{"header":{"magic":"HADEMO","version":1,"map":"m","recordedAt":0,"tickRate":30,"playerNames":{},"durationMs":0},"frames":[],"bookmarks":[]}"#;
        assert!(parse_demo(empty).unwrap_err().0.contains("no frames"));
    }

    #[test]
    fn bookmarks_are_walked_in_order_and_wrap() {
        let mut r = DemoRecorder::default();
        r.start("m", BTreeMap::new(), None, 0.0, 0.0);
        for (at, killer) in [(3000.0, "a"), (9000.0, "b")] {
            r.record_frame(at, vec![body("a", 0.0), body("b", 0.0)]);
            r.record_event(
                at,
                DemoEvent {
                    kind: "kill".into(),
                    killer_id: Some(killer.into()),
                    ..Default::default()
                },
            );
        }
        r.record_frame(12000.0, vec![body("a", 0.0)]);
        let mut p = DemoPlayer::new(r.stop(12000.0).unwrap());
        let next = p.next_bookmark().unwrap();
        p.seek_to_bookmark(next);
        assert_eq!(p.current_time_ms, 1000.0);
        assert_eq!(p.spectator_target_id.as_deref(), Some("a"));
        p.seek_to_bookmark(p.next_bookmark().unwrap());
        assert_eq!(p.current_time_ms, 7000.0);
        assert_eq!(p.spectator_target_id.as_deref(), Some("b"));
        assert_eq!(
            p.next_bookmark(),
            Some(0),
            "past the last, back to the first"
        );
    }

    #[test]
    fn a_file_is_named_for_its_map_and_when_in_utc() {
        assert_eq!(
            file_name("hd_dust2", 1_793_227_697_703.0),
            "dust2-20261028-224817.hademo"
        );
        // A leap day, and a name with nothing filename-safe in it.
        assert_eq!(
            file_name("a b/c", 951_782_400_000.0),
            "a_b_c-20000229-000000.hademo"
        );
    }

    #[test]
    fn a_saved_demo_is_listed_newest_first_and_a_broken_one_is_not() {
        let dir = std::env::temp_dir().join(format!("hademo-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let record = |map: &str, at: f64| {
            let mut r = DemoRecorder::default();
            r.start(map, BTreeMap::new(), None, 0.0, at);
            r.record_frame(0.0, vec![body("a", 0.0)]);
            r.stop(500.0).unwrap()
        };
        let older = save(&dir, &record("hd_aim", 1.0e12)).unwrap();
        save(&dir, &record("hd_dust2", 2.0e12)).unwrap();
        std::fs::write(dir.join("junk.hademo"), "{").unwrap();
        let found = list(&dir);
        assert_eq!(found.len(), 2);
        assert_eq!(found[0].1.map, "hd_dust2");
        assert_eq!(found[1].0, older);
        assert_eq!(found[1].1.duration_ms, 500.0);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_row_survives_the_trip_through_a_frame() {
        let weapons = vec![
            WeaponSpec {
                id: "knife".into(),
                ..Default::default()
            },
            WeaponSpec {
                id: "assault".into(),
                ..Default::default()
            },
        ];
        let row = PlayerRow {
            id: "p1".into(),
            name: "Sable".into(),
            team: 1,
            x: 1.0,
            y: 2.0,
            z: 3.0,
            yaw: 0.5,
            pitch: -0.2,
            hp: 80.0,
            alive: true,
            crouch: 0.4,
            weapon: 1,
            ..Default::default()
        };
        let snap = snapshot_from_row(&row, &weapons);
        assert_eq!(snap.weapon_id.as_deref(), Some("assault"));
        // A roster that gained a weapon in front: the id still finds it.
        let grown = [
            WeaponSpec {
                id: "pistol".into(),
                ..Default::default()
            },
            weapons[0].clone(),
            weapons[1].clone(),
        ];
        let back = row_from_snapshot(&snap, &grown);
        assert_eq!(back.weapon, 2);
        assert_eq!((back.x, back.crouch, back.alive), (1.0, 0.4, true));
    }
}
