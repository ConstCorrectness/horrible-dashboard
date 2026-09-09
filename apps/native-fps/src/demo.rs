//! HorribleAssault Match Recording & Cinematic Replay Suite (.hademo)
//!
//! Provides:
//! 1. HADEMO demo recording format & serialization (tick-based player states, shots, kills, streaks).
//! 2. Playback controller with variable speed (0.1x slow-mo to 4.0x fast-forward), seeking, and frag bookmarks.
//! 3. 6-DOF Cinematic Drone Freecam with camera roll (Q/E), FOV zoom lens (15° to 120°), and inertia smoothing.
//! 4. Catmull-Rom spline dolly keyframe interpolation for montage cinematic camera sweeps.
//! 5. Clean view toggle (Ctrl+H) for zero-HUD video capture.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DemoHeader {
    pub magic: String,
    pub version: u32,
    pub map: String,
    pub recorded_at: u64,
    pub tick_rate: u32,
    pub player_names: HashMap<u32, String>,
    pub duration_ms: f32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DemoPlayerSnapshot {
    pub id: u32,
    pub name: String,
    pub team: i32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub yaw: f32,
    pub pitch: f32,
    pub roll: f32,
    pub weapon: i32,
    pub hp: i32,
    pub crouching: bool,
    pub sprinting: bool,
    pub sliding: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DemoEvent {
    pub tick: u32,
    pub timestamp: f32,
    pub event_type: String,
    pub killer_id: Option<u32>,
    pub victim_id: Option<u32>,
    pub weapon: Option<String>,
    pub head: bool,
    pub nutshot: bool,
    pub streak: u32,
    pub origin: Option<[f32; 3]>,
    pub target: Option<[f32; 3]>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DemoBookmark {
    pub tick: u32,
    pub timestamp: f32,
    pub label: String,
    pub bookmark_type: String,
    pub target_player_id: Option<u32>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DemoFrame {
    pub tick: u32,
    pub timestamp: f32,
    pub players: Vec<DemoPlayerSnapshot>,
    pub events: Vec<DemoEvent>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DemoData {
    pub header: DemoHeader,
    pub frames: Vec<DemoFrame>,
    pub bookmarks: Vec<DemoBookmark>,
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
        self.keyframes
            .sort_by(|a, b| a.time.partial_cmp(&b.time).unwrap());
    }

    pub fn evaluate(&self, time: f32) -> Option<DollyKeyframe> {
        if self.keyframes.is_empty() {
            return None;
        }
        if self.keyframes.len() == 1 {
            return Some(self.keyframes[0].clone());
        }

        let first = &self.keyframes[0];
        let last = &self.keyframes[self.keyframes.len() - 1];
        if time <= first.time {
            return Some(first.clone());
        }
        if time >= last.time {
            return Some(last.clone());
        }

        let mut idx = 0;
        for i in 0..self.keyframes.len() - 1 {
            if time >= self.keyframes[i].time && time <= self.keyframes[i + 1].time {
                idx = i;
                break;
            }
        }

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

/// Match Demo Recorder
#[derive(Clone, Debug, Default)]
pub struct DemoRecorder {
    active: bool,
    header: Option<DemoHeader>,
    frames: Vec<DemoFrame>,
    bookmarks: Vec<DemoBookmark>,
    current_tick: u32,
    start_time: f32,
    pending_events: Vec<DemoEvent>,
}

impl DemoRecorder {
    pub fn start(&mut self, map_name: &str, player_names: HashMap<u32, String>) {
        self.active = true;
        self.current_tick = 0;
        self.start_time = 0.0;
        self.frames.clear();
        self.bookmarks.clear();
        self.pending_events.clear();
        self.header = Some(DemoHeader {
            magic: "HADEMO".to_string(),
            version: 1,
            map: map_name.to_string(),
            recorded_at: 0,
            tick_rate: 60,
            player_names,
            duration_ms: 0.0,
        });
    }

    pub fn is_recording(&self) -> bool {
        self.active
    }

    pub fn record_event(&mut self, mut ev: DemoEvent) {
        if !self.active {
            return;
        }
        ev.tick = self.current_tick;
        if ev.event_type == "kill" {
            let label = if ev.streak > 1 {
                format!("STREAK x{}", ev.streak)
            } else if ev.nutshot {
                "NUTSHOT".to_string()
            } else if ev.head {
                "HEADSHOT".to_string()
            } else {
                "KILL".to_string()
            };

            let bookmark_type = if ev.streak > 1 {
                "streak".to_string()
            } else if ev.nutshot {
                "nutshot".to_string()
            } else if ev.head {
                "headshot".to_string()
            } else {
                "kill".to_string()
            };

            self.bookmarks.push(DemoBookmark {
                tick: ev.tick,
                timestamp: ev.timestamp,
                label,
                bookmark_type,
                target_player_id: ev.killer_id,
            });
        }
        self.pending_events.push(ev);
    }

    pub fn record_frame(&mut self, timestamp: f32, players: Vec<DemoPlayerSnapshot>) {
        if !self.active {
            return;
        }
        let events = std::mem::take(&mut self.pending_events);
        self.frames.push(DemoFrame {
            tick: self.current_tick,
            timestamp,
            players,
            events,
        });
        self.current_tick += 1;
    }

    pub fn stop(&mut self, duration_ms: f32) -> Option<DemoData> {
        if !self.active {
            return None;
        }
        self.active = false;
        let mut header = self.header.take()?;
        header.duration_ms = duration_ms;
        Some(DemoData {
            header,
            frames: std::mem::take(&mut self.frames),
            bookmarks: std::mem::take(&mut self.bookmarks),
        })
    }

    pub fn export_json(&mut self, duration_ms: f32) -> Result<String, serde_json::Error> {
        let demo = self.stop(duration_ms);
        serde_json::to_string(&demo)
    }
}

/// Replay Playback Controller
#[derive(Clone, Debug, Default)]
pub struct DemoPlayer {
    pub demo: Option<DemoData>,
    pub is_playing: bool,
    pub playback_speed: f32,
    pub current_time_ms: f32,
    pub clean_view: bool,
    pub freecam: DroneFreecam,
    pub dolly: DollyPath,
    pub spectator_target_id: Option<u32>,
}

impl DemoPlayer {
    pub fn load(&mut self, demo: DemoData) {
        self.current_time_ms = 0.0;
        self.is_playing = false;
        self.playback_speed = 1.0;
        if let Some(f0) = demo.frames.first() {
            if let Some(p0) = f0.players.first() {
                self.spectator_target_id = Some(p0.id);
            }
        }
        self.demo = Some(demo);
    }

    pub fn play(&mut self) {
        self.is_playing = true;
    }

    pub fn pause(&mut self) {
        self.is_playing = false;
    }

    pub fn toggle_play(&mut self) {
        self.is_playing = !self.is_playing;
    }

    pub fn set_speed(&mut self, speed: f32) {
        self.playback_speed = speed.clamp(0.1, 4.0);
    }

    pub fn seek(&mut self, time_ms: f32) {
        if let Some(demo) = &self.demo {
            self.current_time_ms = time_ms.clamp(0.0, demo.header.duration_ms);
        }
    }

    pub fn seek_to_bookmark(&mut self, index: usize) {
        let bookmark = self
            .demo
            .as_ref()
            .and_then(|demo| demo.bookmarks.get(index).cloned());
        if let Some(bm) = bookmark {
            // Seek 2.0s before kill for montage clip pacing
            self.seek((bm.timestamp - 2000.0).max(0.0));
            if let Some(target) = bm.target_player_id {
                self.spectator_target_id = Some(target);
            }
        }
    }

    pub fn toggle_clean_view(&mut self) -> bool {
        self.clean_view = !self.clean_view;
        self.clean_view
    }

    pub fn update(&mut self, dt: f32) {
        if !self.is_playing {
            return;
        }
        let Some(demo) = &self.demo else { return };
        self.current_time_ms += dt * 1000.0 * self.playback_speed;
        if self.current_time_ms >= demo.header.duration_ms {
            self.current_time_ms = demo.header.duration_ms;
            self.is_playing = false;
        }
    }

    pub fn sample_current_frame(&self) -> (Vec<DemoPlayerSnapshot>, Vec<DemoEvent>) {
        let Some(demo) = &self.demo else {
            return (Vec::new(), Vec::new());
        };
        if demo.frames.is_empty() {
            return (Vec::new(), Vec::new());
        }

        let t = self.current_time_ms;
        let frames = &demo.frames;

        if t <= frames[0].timestamp {
            return (frames[0].players.clone(), frames[0].events.clone());
        }
        let last = &frames[frames.len() - 1];
        if t >= last.timestamp {
            return (last.players.clone(), last.events.clone());
        }

        // Binary search for frame interval
        let mut low = 0;
        let mut high = frames.len() - 1;
        while low <= high {
            let mid = (low + high) / 2;
            if frames[mid].timestamp <= t {
                low = mid + 1;
            } else {
                high = mid - 1;
            }
        }

        let idx0 = high.min(frames.len() - 2);
        let idx1 = idx0 + 1;
        let f0 = &frames[idx0];
        let f1 = &frames[idx1];

        let span = f1.timestamp - f0.timestamp;
        let alpha = if span > 0.0 {
            (t - f0.timestamp) / span
        } else {
            0.0
        };

        let mut players = Vec::with_capacity(f0.players.len());
        for p0 in &f0.players {
            if let Some(p1) = f1.players.iter().find(|p| p.id == p0.id) {
                players.push(DemoPlayerSnapshot {
                    id: p0.id,
                    name: p0.name.clone(),
                    team: p0.team,
                    x: p0.x + (p1.x - p0.x) * alpha,
                    y: p0.y + (p1.y - p0.y) * alpha,
                    z: p0.z + (p1.z - p0.z) * alpha,
                    yaw: interpolate_angle(p0.yaw, p1.yaw, alpha),
                    pitch: interpolate_angle(p0.pitch, p1.pitch, alpha),
                    roll: interpolate_angle(p0.roll, p1.roll, alpha),
                    weapon: if alpha < 0.5 { p0.weapon } else { p1.weapon },
                    hp: if alpha < 0.5 { p0.hp } else { p1.hp },
                    crouching: if alpha < 0.5 { p0.crouching } else { p1.crouching },
                    sprinting: if alpha < 0.5 { p0.sprinting } else { p1.sprinting },
                    sliding: if alpha < 0.5 { p0.sliding } else { p1.sliding },
                });
            } else {
                players.push(p0.clone());
            }
        }

        (players, f0.events.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_catmull_rom_and_interpolate_angle() {
        let p = catmull_rom(0.0, 10.0, 20.0, 30.0, 0.5);
        assert!((p - 15.0).abs() < 1e-4);

        let angle = interpolate_angle(350.0, 10.0, 0.5);
        assert!(((angle + 360.0) % 360.0).abs() < 1e-4);
    }

    #[test]
    fn test_drone_freecam() {
        let mut drone = DroneFreecam::default();
        drone.active = true;
        drone.set_position(0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 90.0);

        drone.update(0.016, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, -10.0, false);
        assert!(drone.y > 0.0, "should move forward along +y");
        assert!(drone.v_roll > 0.0, "should apply roll acceleration");
        assert!((drone.fov - 80.0).abs() < 1e-3, "should zoom FOV");
    }

    #[test]
    fn test_dolly_path() {
        let mut path = DollyPath::default();
        path.add_keyframe(DollyKeyframe {
            time: 0.0,
            x: 0.0,
            y: 0.0,
            z: 2.0,
            yaw: 0.0,
            pitch: 0.0,
            roll: 0.0,
            fov: 90.0,
        });
        path.add_keyframe(DollyKeyframe {
            time: 2.0,
            x: 10.0,
            y: 20.0,
            z: 4.0,
            yaw: 45.0,
            pitch: 10.0,
            roll: 5.0,
            fov: 75.0,
        });

        let eval = path.evaluate(1.0).expect("evaluated keyframe");
        assert!(eval.x > 0.0 && eval.x < 10.0);
        assert!(eval.fov < 90.0);
    }

    #[test]
    fn test_recorder_and_player() {
        let mut recorder = DemoRecorder::default();
        let mut names = HashMap::new();
        names.insert(1, "Player1".to_string());
        recorder.start("hd_junkflea", names);

        recorder.record_frame(
            0.0,
            vec![DemoPlayerSnapshot {
                id: 1,
                name: "Player1".to_string(),
                team: 0,
                x: 0.0,
                y: 0.0,
                z: 0.0,
                yaw: 0.0,
                pitch: 0.0,
                roll: 0.0,
                weapon: 3,
                hp: 100,
                crouching: false,
                sprinting: true,
                sliding: false,
            }],
        );

        recorder.record_event(DemoEvent {
            tick: 0,
            timestamp: 50.0,
            event_type: "kill".to_string(),
            killer_id: Some(1),
            victim_id: Some(2),
            weapon: Some("sniper".to_string()),
            head: false,
            nutshot: true,
            streak: 2,
            origin: None,
            target: None,
        });

        recorder.record_frame(
            100.0,
            vec![DemoPlayerSnapshot {
                id: 1,
                name: "Player1".to_string(),
                team: 0,
                x: 5.0,
                y: 5.0,
                z: 0.0,
                yaw: 0.0,
                pitch: 0.0,
                roll: 0.0,
                weapon: 3,
                hp: 100,
                crouching: false,
                sprinting: false,
                sliding: true,
            }],
        );

        let demo = recorder.stop(100.0).expect("demo generated");
        assert_eq!(demo.header.map, "hd_junkflea");
        assert_eq!(demo.frames.len(), 2);
        assert_eq!(demo.bookmarks.len(), 1);

        let mut player = DemoPlayer::default();
        player.load(demo);
        player.play();
        assert!(player.is_playing);

        player.update(0.05); // 50ms at 1.0x
        let (players, _) = player.sample_current_frame();
        assert_eq!(players.len(), 1);
        assert!((players[0].x - 2.5).abs() < 0.5);

        player.toggle_clean_view();
        assert!(player.clean_view);
    }
}
