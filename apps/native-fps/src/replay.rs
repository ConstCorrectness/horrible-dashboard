//! Watching a recording: the controls, the camera and the timeline bar.
//!
//! The `App` plays a replay on the range's machinery — the map loaded with no
//! server — and asks this for three things each frame: the bodies to draw
//! (`rows`), where the eye is (`eye`), and the bar along the bottom (`paint`).
//! Everything here is free of the window and the GPU, so it is tested headless.
//!
//! The camera is behind someone's eyes — the recorder's at first, the killer's
//! after a jump to a frag — or, with the freecam (F6), wherever it is flown.
//! Their body is left out of `rows` while the camera is inside it, exactly as
//! a match leaves out your own.

use winit::keyboard::KeyCode;

use crate::api::WeaponSpec;
use crate::demo::{row_from_snapshot, DemoData, DemoPlayer, DemoPlayerSnapshot};
use crate::hud::{OverlayVertex, Painter};
use crate::physics::{eye_height, PlayerState};
use crate::protocol::PlayerRow;
use crate::text_kit::TextKit;
use crate::ui_theme as theme;

/// The speeds the arrow keys step through.
pub const SPEEDS: [f32; 6] = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0];
/// How far the left and right arrows seek.
pub const SEEK_SECONDS: f64 = 5.0;

/// A recording being watched.
#[derive(Debug, Clone)]
pub struct Replay {
    pub player: DemoPlayer,
    /// No HUD and no bar: the picture alone, for capturing. `H` toggles it,
    /// and an export runs in it.
    pub clean: bool,
}

/// What a key did, for the `App` to act on what it owns.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Handled {
    /// Not a replay key: the game's own handling has it.
    No,
    Yes,
}

impl Replay {
    /// Start watching `demo`, already playing.
    pub fn new(demo: DemoData) -> Replay {
        let mut player = DemoPlayer::new(demo);
        player.play();
        Replay {
            player,
            clean: false,
        }
    }

    /// The body the camera follows this frame: the spectated one, or the
    /// first in the frame when they are not in it (they left, or a bookmark
    /// named someone who never appeared).
    pub fn followed(&self) -> Option<DemoPlayerSnapshot> {
        let (players, _) = self.player.sample();
        let target = self.player.spectator_target_id.as_deref();
        players
            .iter()
            .find(|p| Some(p.id.as_str()) == target)
            .or(players.first())
            .cloned()
    }

    /// Where the followed body's eyes are and which way they look: position
    /// in cube space, yaw and pitch in **degrees**, as `Camera` keeps them.
    pub fn eye(&self) -> Option<([f32; 3], f32, f32)> {
        let p = self.followed()?;
        let body = PlayerState {
            x: p.x,
            y: p.y,
            z: p.z,
            crouch: p.crouch.unwrap_or(0.0),
            ..PlayerState::default()
        };
        Some((
            [p.x, p.y, eye_height(&body)],
            p.yaw.to_degrees(),
            p.pitch.to_degrees(),
        ))
    }

    /// The bodies to draw. With `inside`, the camera is in the followed one's
    /// head and that body is left out.
    pub fn rows(&self, weapons: &[WeaponSpec], inside: bool) -> Vec<PlayerRow> {
        let followed = inside.then(|| self.followed().map(|p| p.id)).flatten();
        let (players, _) = self.player.sample();
        players
            .iter()
            .filter(|p| Some(&p.id) != followed.as_ref())
            .map(|p| row_from_snapshot(p, weapons))
            .collect()
    }

    /// Follow the next body in the frame, in the frame's order.
    pub fn follow_next(&mut self) {
        let (players, _) = self.player.sample();
        if players.is_empty() {
            return;
        }
        let at = self
            .followed()
            .and_then(|f| players.iter().position(|p| p.id == f.id))
            .unwrap_or(0);
        self.player.spectator_target_id = Some(players[(at + 1) % players.len()].id.clone());
    }

    /// One step faster or slower through `SPEEDS`.
    pub fn step_speed(&mut self, step: i32) {
        let now = self.player.playback_speed;
        let at = SPEEDS
            .iter()
            .position(|s| (s - now).abs() < 1e-3)
            .unwrap_or(3) as i32;
        let next = (at + step).clamp(0, SPEEDS.len() as i32 - 1) as usize;
        self.player.set_speed(SPEEDS[next]);
    }

    /// The replay's keys. `freecam` is whether the drone has the movement
    /// keys, in which case Space is its climb rather than play.
    pub fn key(&mut self, code: KeyCode, freecam: bool) -> Handled {
        match code {
            KeyCode::KeyP => self.player.toggle_play(),
            KeyCode::Space if !freecam => self.player.toggle_play(),
            KeyCode::ArrowLeft => self.player.seek_relative(-SEEK_SECONDS),
            KeyCode::ArrowRight => self.player.seek_relative(SEEK_SECONDS),
            KeyCode::ArrowUp => self.step_speed(1),
            KeyCode::ArrowDown => self.step_speed(-1),
            KeyCode::Home => self.player.seek(0.0),
            KeyCode::KeyB => {
                if let Some(i) = self.player.next_bookmark() {
                    self.player.seek_to_bookmark(i);
                }
            }
            KeyCode::KeyN => self.follow_next(),
            KeyCode::KeyH => self.clean = !self.clean,
            _ => return Handled::No,
        }
        Handled::Yes
    }

    /// The timeline along the bottom: progress, the frags as ticks, the time,
    /// the speed, who is being followed and the keys.
    pub fn paint(
        &self,
        out: &mut Vec<OverlayVertex>,
        width: f32,
        height: f32,
        kit: Option<&TextKit>,
        freecam: bool,
    ) {
        let mut p = Painter::with_text(out, width, height, kit);
        let k = (height / 720.0).max(0.6);
        let (x, w) = (40.0 * k, width - 80.0 * k);
        let bar_y = height - 64.0 * k;
        let panel_y = bar_y - 34.0 * k;
        p.gradient_v(
            0.0,
            panel_y - 20.0 * k,
            width,
            height - panel_y + 20.0 * k,
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.65],
        );
        let duration = self.player.duration_ms().max(1.0);
        let t = (self.player.current_time_ms / duration).clamp(0.0, 1.0) as f32;
        p.rect(x, bar_y, w, 4.0 * k, [1.0, 1.0, 1.0, 0.16]);
        p.rect(x, bar_y, w * t, 4.0 * k, theme::ACCENT);
        p.rect(
            x + w * t - 1.5 * k,
            bar_y - 5.0 * k,
            3.0 * k,
            14.0 * k,
            theme::TEXT,
        );
        for mark in &self.player.demo.bookmarks {
            let at = (mark.timestamp / duration).clamp(0.0, 1.0) as f32;
            let hue = if mark.kind == "headshot" || mark.kind == "streak" {
                theme::AMBER
            } else {
                theme::SIDE_T
            };
            p.rect(x + w * at - 1.0 * k, bar_y - 8.0 * k, 2.0 * k, 8.0 * k, hue);
        }

        let clock = |ms: f64| crate::front::length_label(ms);
        let state = if self.player.is_playing {
            "PLAYING"
        } else if self.player.ended() {
            "ENDED"
        } else {
            "PAUSED"
        };
        p.num(
            x,
            panel_y,
            1.3 * k,
            theme::TEXT,
            &format!(
                "{}  /  {}",
                clock(self.player.current_time_ms),
                clock(duration)
            ),
        );
        p.num(
            x + 170.0 * k,
            panel_y + 2.0 * k,
            1.05 * k,
            theme::AMBER,
            &format!("{state}  {}X", trim(self.player.playback_speed)),
        );
        let who = if freecam {
            "FREECAM".to_string()
        } else {
            self.followed()
                .map(|f| format!("FOLLOWING {}", f.name.to_uppercase()))
                .unwrap_or_default()
        };
        p.num_right(x + w, panel_y + 2.0 * k, 1.05 * k, theme::TEXT_DIM, &who);
        p.num(
            x,
            bar_y + 14.0 * k,
            0.95 * k,
            theme::FAINT,
            "P PLAY  /  ARROWS SEEK AND SPEED  /  B NEXT FRAG  /  N NEXT PLAYER  /  F6 FREECAM  /  H HIDE  /  ESC MENU",
        );
    }
}

/// `0.25`, `1`, `4`: a speed without trailing zeros.
fn trim(speed: f32) -> String {
    let s = format!("{speed:.2}");
    s.trim_end_matches('0').trim_end_matches('.').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::demo::DemoRecorder;
    use std::collections::BTreeMap;

    fn body(id: &str, x: f32, crouch: f32) -> DemoPlayerSnapshot {
        DemoPlayerSnapshot {
            id: id.into(),
            name: id.into(),
            x,
            z: 1.0,
            yaw: std::f32::consts::FRAC_PI_2,
            hp: 100.0,
            crouch: Some(crouch),
            alive: Some(true),
            ..Default::default()
        }
    }

    fn replay() -> Replay {
        let mut r = DemoRecorder::default();
        r.start("hd_dust2", BTreeMap::new(), Some("b".into()), 0.0, 0.0);
        r.record_frame(0.0, vec![body("a", 0.0, 0.0), body("b", 10.0, 0.0)]);
        r.record_frame(20_000.0, vec![body("a", 20.0, 0.0), body("b", 10.0, 1.0)]);
        let mut demo = r.stop(20_000.0).unwrap();
        demo.header.pov = Some("b".into());
        Replay::new(demo)
    }

    #[test]
    fn the_camera_starts_in_the_recorders_eyes_and_leaves_their_body_out() {
        let r = replay();
        let ([x, _, z], yaw, _) = r.eye().unwrap();
        assert_eq!(x, 10.0);
        assert!(z > 1.0, "eyes above the feet");
        assert!((yaw - 90.0).abs() < 1e-4, "degrees, for the camera");
        let inside: Vec<_> = r.rows(&[], true).into_iter().map(|r| r.id).collect();
        assert_eq!(inside, ["a"]);
        assert_eq!(r.rows(&[], false).len(), 2, "the freecam sees everyone");
    }

    #[test]
    fn a_crouched_body_has_lower_eyes() {
        let mut r = replay();
        let standing = r.eye().unwrap().0[2];
        r.player.seek(20_000.0);
        assert!(r.eye().unwrap().0[2] < standing);
    }

    #[test]
    fn the_keys_seek_step_the_speed_and_change_who_is_followed() {
        let mut r = replay();
        assert_eq!(r.key(KeyCode::ArrowRight, false), Handled::Yes);
        assert_eq!(r.player.current_time_ms, 5000.0);
        r.key(KeyCode::ArrowLeft, false);
        r.key(KeyCode::ArrowLeft, false);
        assert_eq!(r.player.current_time_ms, 0.0, "clamped at the start");
        r.key(KeyCode::ArrowUp, false);
        assert_eq!(r.player.playback_speed, 2.0);
        for _ in 0..10 {
            r.key(KeyCode::ArrowDown, false);
        }
        assert_eq!(r.player.playback_speed, 0.1);
        r.key(KeyCode::KeyN, false);
        assert_eq!(r.followed().unwrap().id, "a", "after b comes a, wrapping");
        assert!(r.player.is_playing);
        r.key(KeyCode::Space, true);
        assert!(r.player.is_playing, "Space is the freecam's climb");
        r.key(KeyCode::Space, false);
        assert!(!r.player.is_playing);
        assert_eq!(r.key(KeyCode::KeyW, false), Handled::No);
    }

    #[test]
    fn the_bar_draws_inside_the_window() {
        let r = replay();
        let mut out = Vec::new();
        r.paint(&mut out, 1280.0, 720.0, None, false);
        assert!(!out.is_empty());
        assert!(out
            .iter()
            .all(|v| v.position[0].abs() <= 1.0001 && v.position[1].abs() <= 1.0001));
    }

    #[test]
    fn speeds_read_without_trailing_zeros() {
        assert_eq!(trim(0.25), "0.25");
        assert_eq!(trim(1.0), "1");
        assert_eq!(trim(0.1), "0.1");
    }
}
