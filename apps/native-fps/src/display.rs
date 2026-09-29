//! Which monitor, and which of its modes, fullscreen uses.
//!
//! **Borderless is the default** — see `App::set_fullscreen` for why. Exclusive
//! is offered because some drivers only give a compositor-free present path and
//! a refresh rate the desktop is not running at to a window that owns the
//! display, and a player with a 240 Hz monitor on a 60 Hz desktop should be able
//! to ask for it.
//!
//! The menu steps through monitors and modes that only winit can list, and only
//! while a window exists. So the lists are captured here whenever the window
//! opens or moves, and the menu reads them — the same arrangement `gpu.rs` uses
//! for adapters, and for the same reason: `Settings` stays `Copy` and the menu
//! stays testable without a display.

use std::sync::Mutex;

use winit::monitor::{MonitorHandle, VideoModeHandle};
use winit::window::Fullscreen;

use crate::settings::Video;

/// One exclusive mode: width, height, refresh in millihertz.
pub type Mode = (u32, u32, u32);

#[derive(Debug, Clone, Default)]
struct Snapshot {
    monitors: Vec<String>,
    /// Modes of the monitor fullscreen would use, best first.
    modes: Vec<Mode>,
}

static SNAPSHOT: Mutex<Snapshot> = Mutex::new(Snapshot {
    monitors: Vec::new(),
    modes: Vec::new(),
});

/// The monitor fullscreen lands on: 0 is whichever the window is on now, `n` is
/// the n-th in the system's list. An index past the end — a monitor since
/// unplugged — is the current one, which is where the player can see the menu.
fn pick_monitor(
    video: &Video,
    monitors: &[MonitorHandle],
    current: Option<MonitorHandle>,
) -> Option<MonitorHandle> {
    match video.monitor {
        0 => current.or_else(|| monitors.first().cloned()),
        n => monitors.get(n as usize - 1).cloned().or(current),
    }
}

/// Every mode of a monitor, as `Mode`s, best first (largest, then fastest), with
/// the bit-depth duplicates Windows reports folded into one.
fn modes_of(monitor: &MonitorHandle) -> Vec<Mode> {
    let mut modes: Vec<Mode> = monitor
        .video_modes()
        .map(|m| (m.size().width, m.size().height, m.refresh_rate_millihertz()))
        .collect();
    modes.sort_by(|a, b| (b.0 * b.1, b.2).cmp(&(a.0 * a.1, a.2)));
    modes.dedup();
    modes
}

/// Record what this machine has, for the menu.
pub fn capture(video: &Video, monitors: Vec<MonitorHandle>, current: Option<MonitorHandle>) {
    let chosen = pick_monitor(video, &monitors, current);
    let snapshot = Snapshot {
        monitors: monitors
            .iter()
            .enumerate()
            .map(|(i, m)| {
                let name = m.name().unwrap_or_else(|| format!("DISPLAY {}", i + 1));
                let size = m.size();
                format!("{} {}X{}", name, size.width, size.height)
            })
            .collect(),
        modes: chosen.as_ref().map(modes_of).unwrap_or_default(),
    };
    if let Ok(mut s) = SNAPSHOT.lock() {
        *s = snapshot;
    }
}

/// How many monitors the menu can offer, not counting "current".
pub fn monitor_count() -> usize {
    SNAPSHOT.lock().map(|s| s.monitors.len()).unwrap_or(0)
}

pub fn monitor_label(index: u32) -> String {
    if index == 0 {
        return "CURRENT".into();
    }
    SNAPSHOT
        .lock()
        .ok()
        .and_then(|s| s.monitors.get(index as usize - 1).cloned())
        .map(|name| crate::gpu::menu_label(&name))
        .unwrap_or_else(|| format!("DISPLAY {index}"))
}

/// The exclusive modes on offer, with the desktop's own mode first as zeros.
pub fn modes() -> Vec<Mode> {
    let mut out = vec![(0, 0, 0)];
    if let Ok(s) = SNAPSHOT.lock() {
        out.extend(s.modes.iter().copied());
    }
    out
}

pub fn mode_label(mode: Mode) -> String {
    if mode.0 == 0 {
        "DESKTOP".into()
    } else {
        format!(
            "{}X{} @{}",
            mode.0,
            mode.1,
            (mode.2 as f32 / 1000.0).round() as u32
        )
    }
}

/// What to hand winit for this video setting: `None` for a window.
pub fn fullscreen(
    video: &Video,
    monitors: Vec<MonitorHandle>,
    current: Option<MonitorHandle>,
) -> Option<Fullscreen> {
    if !video.fullscreen {
        return None;
    }
    let monitor = pick_monitor(video, &monitors, current);
    if video.exclusive {
        if let Some(mode) = monitor
            .as_ref()
            .and_then(|m| exclusive_mode(m, video.exclusive_mode))
        {
            return Some(Fullscreen::Exclusive(mode));
        }
        // No mode matched — a monitor swapped for one with other modes. The
        // borderless window below is on the right monitor at its desktop mode,
        // which is the recoverable version of that.
        eprintln!("hassault: no exclusive mode matches; using borderless fullscreen");
    }
    Some(Fullscreen::Borderless(monitor))
}

/// The monitor's mode matching `want`, or — for the desktop setting, zeros —
/// the largest at the fastest refresh.
fn exclusive_mode(monitor: &MonitorHandle, want: Mode) -> Option<VideoModeHandle> {
    let mut modes: Vec<VideoModeHandle> = monitor.video_modes().collect();
    modes.sort_by_key(|m| {
        (
            std::cmp::Reverse(m.size().width * m.size().height),
            std::cmp::Reverse(m.refresh_rate_millihertz()),
            std::cmp::Reverse(m.bit_depth()),
        )
    });
    if want.0 == 0 {
        let desktop = monitor.size();
        return modes
            .iter()
            .find(|m| m.size() == desktop)
            .or(modes.first())
            .cloned();
    }
    modes.into_iter().find(|m| {
        m.size().width == want.0
            && m.size().height == want.1
            && m.refresh_rate_millihertz() == want.2
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_mode_label_is_drawable() {
        for label in [mode_label((0, 0, 0)), mode_label((2560, 1440, 143_998))] {
            assert!(label.chars().all(crate::hud::has_glyph), "{label}");
        }
        assert_eq!(mode_label((2560, 1440, 143_998)), "2560X1440 @144");
    }

    #[test]
    fn the_desktop_mode_is_always_offered_first() {
        assert_eq!(modes()[0], (0, 0, 0));
    }
}
