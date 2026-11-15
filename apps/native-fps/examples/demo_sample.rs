//! Write a `.hademo` without playing a match: four bodies walking Dust II's
//! benchmark framings, with a few frags bookmarked.
//!
//! For trying the replay viewer and the export on a machine with no server and
//! no time to play:
//!
//! ```text
//! cargo run --example demo_sample -- /tmp/sample.hademo [seconds]
//! cargo run --release -- --replay=/tmp/sample.hademo
//! cargo run --release -- --replay=/tmp/sample.hademo --export
//! ```
//!
//! Pass no path to write it into the client's own demos folder, where the
//! REPLAYS tab finds it. The bodies walk straight between framings, through
//! whatever is in the way: it is a recording to test playback with, not a match.

use std::collections::BTreeMap;

use hassault_native::demo::{self, DemoEvent, DemoPlayerSnapshot, DemoRecorder, TICK_RATE};
use hassault_native::physics::{eye_offset, PlayerState};

/// Cubes a second: a walk, so the legs have something to do.
const SPEED: f32 = 9.0;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let seconds: f64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(30.0);
    let path = hassault_native::bench::path_for("hd_dust2").expect("a Dust II bench path");
    let stand = eye_offset(&PlayerState::default());
    let spots: Vec<[f32; 3]> = path
        .iter()
        .map(|f| [f.eye[0], f.eye[1], f.eye[2] - stand])
        .collect();
    assert!(spots.len() >= 2, "a path to walk");

    // (id, name, team, weapon id, weapon index, first spot)
    let bodies = [
        ("p0", "Sable", 0, "assault", 2, 0usize),
        ("p1", "Meridian", 1, "sniper", 4, 1),
        ("p2", "Kestrel", 0, "pistol", 1, 2),
        ("p3", "Vantage", 1, "assault", 2, 3),
    ];

    let wall = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64() * 1000.0);
    let mut rec = DemoRecorder::default();
    rec.start("hd_dust2", BTreeMap::new(), Some("p0".into()), 0.0, wall);
    let frags = [
        (8.0, "p0", "p1", true),
        (17.0, "p1", "p2", false),
        (26.0, "p0", "p3", false),
    ];
    let step = 1000.0 / TICK_RATE as f64;
    let mut t = 0.0;
    while t <= seconds * 1000.0 {
        let players = bodies
            .iter()
            .map(|&(id, name, team, weapon_id, weapon, first)| {
                let (x, y, z, yaw) = walk(&spots, first, (t / 1000.0) as f32);
                DemoPlayerSnapshot {
                    id: id.into(),
                    name: name.into(),
                    team,
                    x,
                    y,
                    z,
                    yaw,
                    pitch: -0.05,
                    weapon,
                    weapon_id: Some(weapon_id.into()),
                    hp: 100.0,
                    crouch: Some(0.0),
                    alive: Some(true),
                    ..Default::default()
                }
            })
            .collect();
        for &(at, killer, victim, head) in &frags {
            if (t / 1000.0 - at).abs() < step / 2000.0 {
                rec.record_event(
                    t,
                    DemoEvent {
                        kind: "kill".into(),
                        killer_id: Some(killer.into()),
                        victim_id: Some(victim.into()),
                        head: Some(head),
                        weapon: Some("assault".into()),
                        ..Default::default()
                    },
                );
            }
        }
        rec.record_frame(t, players);
        t += step;
    }
    let mut data = rec.stop(seconds * 1000.0).expect("a recording");
    for (id, name, ..) in bodies {
        data.header.player_names.insert(id.into(), name.into());
    }
    let written = match args.first() {
        Some(out) => {
            let json = serde_json::to_vec(&data).expect("serialises");
            std::fs::write(out, json).expect("written");
            out.into()
        }
        None => demo::save(&demo::demos_dir().expect("a demos folder"), &data).expect("saved"),
    };
    println!(
        "wrote {} ({} frames, {} frags)",
        std::path::Path::new(&written).display(),
        data.frames.len(),
        data.bookmarks.len()
    );
}

/// Where a body starting at `first` is after `t` seconds of walking the loop
/// of spots at `SPEED`, and which way it faces (radians, 0 = +x).
fn walk(spots: &[[f32; 3]], first: usize, t: f32) -> (f32, f32, f32, f32) {
    let n = spots.len();
    let mut left = t * SPEED;
    let mut i = first % n;
    loop {
        let (a, b) = (spots[i], spots[(i + 1) % n]);
        let (dx, dy, dz) = (b[0] - a[0], b[1] - a[1], b[2] - a[2]);
        let len = (dx * dx + dy * dy).sqrt().max(1e-3);
        if left <= len {
            let f = left / len;
            return (a[0] + dx * f, a[1] + dy * f, a[2] + dz * f, dy.atan2(dx));
        }
        left -= len;
        i = (i + 1) % n;
    }
}
