//! HorribleAssault, native client — **stages B1 and B2**.
//!
//! What this replaced is worth remembering, because it made claims: a `minifb`
//! software framebuffer walking a hardcoded 16×16 grid, with no map loading and
//! no networking at all (`tungstenite` was a declared dependency nothing
//! imported), launched by a route that passed it `--connect`, `--room` and
//! `--raw-input`, none of which it parsed — and advertised in the game's menu as
//! a Vulkan client with sub-tick UDP networking.
//!
//! **B1 — the wire and the world.** Real maps fetched from the node and meshed by
//! a port of the browser client's `world.ts`/`geometry.ts` (`world.rs`,
//! `geometry.rs`), and the real `hassault` protocol on the node's shared `/ws`
//! (`protocol.rs`, `net.rs`).
//!
//! **B2 — the renderer.** A `wgpu` device (`renderer.rs`), a first-person camera
//! (`camera.rs`), bodies (`bodies.rs`) and the window and input loop (`app.rs`).
//! `wgpu` selects DX12/Vulkan on Windows, Vulkan on Linux and Metal on macOS from
//! one backend, which is the whole cross-platform claim.
//!
//! **B3 — prediction.** A Rust port of the movement rules (`physics.rs`) plus
//! rewind-and-replay reconciliation (`prediction.rs`), so input moves you on the
//! frame you pressed it. That port is the **third** implementation of one set of
//! rules, and it takes its seat at the shared-fixture table
//! (`tests/conformance.rs`) alongside the server's and the browser client's.
//!
//! Identity needs nothing here: the node takes the player's name from its own
//! signed-in account and ignores anything a client sends.

mod app;

use std::time::{Duration, Instant};

use winit::event_loop::EventLoop;

use hassault_native::api::NodeApi;
use hassault_native::geometry;
use hassault_native::net::{Incoming, MatchSocket};
use hassault_native::protocol::{Command, Event};
use hassault_native::viewmodel;
use hassault_native::world::World;

use crate::app::App;

/// What the launcher was asked for, which is not always what a join is.
///
/// The route used to send none of this, so every launch was the same launch — "a
/// match on this map, or open one". That made **Train a lie**: `match_server.join`
/// with no room id is join-*or*-create, so pressing Train while anyone was playing
/// that map put you in their firefight. It also meant the bot count the menu had
/// just collected had nowhere to go.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Mode {
    /// One player, one map, **no socket at all** — the browser client's Train,
    /// minus its dummy range, which lives in `training.ts` and has no port yet.
    Train,
    /// Open (or join) a match here and field bots in it.
    Host,
    /// Enter a match that exists — a room id, or any room on the map.
    Join,
}

impl Mode {
    fn parse(v: &str) -> Option<Mode> {
        match v {
            "train" => Some(Mode::Train),
            "host" => Some(Mode::Host),
            "join" => Some(Mode::Join),
            _ => None,
        }
    }
}

struct Args {
    server: String,
    map: String,
    mode: Mode,
    room: String,
    host: String,
    name: String,
    /// Bots to field, `--mode=host` only.
    bots: u32,
    bot_skill: String,
    /// Multiplies the turn per unit of raw mouse movement.
    sensitivity: f32,
    headless: bool,
    /// Load and mesh the map, print what it found, and exit without connecting.
    check_only: bool,
}

impl Default for Args {
    fn default() -> Args {
        Args {
            // The node on this machine. `HORRIBLE_DEV_BACKEND_PORT` moves it —
            // Hyper-V reserves ranges on Windows that can swallow 8000 — so the
            // launcher passes `--server` rather than anyone guessing.
            server: "http://127.0.0.1:8000".into(),
            map: "hd_crossing".into(),
            // Joining is the least surprising default and the one the old
            // argument-free launch effectively did.
            mode: Mode::Join,
            room: String::new(),
            host: String::new(),
            name: "player".into(),
            bots: 0,
            bot_skill: "normal".into(),
            sensitivity: 1.0,
            headless: false,
            check_only: false,
        }
    }
}

fn parse_args() -> Args {
    let mut args = Args::default();
    for arg in std::env::args().skip(1) {
        if let Some(v) = arg.strip_prefix("--server=") {
            args.server = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--map=") {
            args.map = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--mode=") {
            match Mode::parse(v) {
                Some(m) => args.mode = m,
                // Refused rather than defaulted: a typo'd mode silently becoming
                // "join" is how Train quietly turns back into a match.
                None => {
                    eprintln!("hassault: unknown --mode={v} (train, host or join)");
                    std::process::exit(2);
                }
            }
        } else if let Some(v) = arg.strip_prefix("--bots=") {
            if let Ok(n) = v.parse::<u32>() {
                args.bots = n;
            }
        } else if let Some(v) = arg.strip_prefix("--bot-skill=") {
            args.bot_skill = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--room=") {
            args.room = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--host=") {
            args.host = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--name=") {
            args.name = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--sensitivity=") {
            if let Ok(n) = v.parse::<f32>() {
                // A zero or negative multiplier is a view that cannot turn, which
                // reads as broken input rather than as a setting.
                if n.is_finite() && n > 0.0 {
                    args.sensitivity = n;
                }
            }
        } else if arg.starts_with("--max-fps=") || arg.starts_with("--raw-input=") {
            // Accepted and ignored, for the launcher's older request shape.
            // There is no frame cap to set — the present mode decides — and raw
            // input is not an option, it is how the mouse is read.
        } else if arg == "--headless" {
            args.headless = true;
        } else if arg == "--check" {
            args.check_only = true;
        } else if arg == "--help" || arg == "-h" {
            eprintln!(
                "hassault (native)\n\
                 \n\
                   --server=<origin>   the node's HTTP origin (default http://127.0.0.1:8000)\n\
                   --map=<name>        map to load and join\n\
                   --mode=<mode>       train (no server), host, or join (default)\n\
                   --bots=<n>          bots to field, --mode=host only\n\
                   --bot-skill=<s>     easy, normal or hard (default normal)\n\
                   --room=<id>         join a specific room rather than any on the map\n\
                   --host=<node id>    that room is on a friend's node\n\
                   --name=<label>      wire label only; the node uses your account's username\n\
                   --sensitivity=<n>   turn per unit of raw mouse movement (default 1)\n\
                   --headless          no window: connect, join, and log\n\
                   --check             load and mesh the map, print it, and exit\n"
            );
            std::process::exit(0);
        }
    }
    args
}

fn main() {
    let args = parse_args();
    if let Err(e) = run(&args) {
        // One line, naming the thing that failed. The overwhelmingly common
        // failure is "the node is not running", and it deserves to say so rather
        // than surfacing as a window that never appears.
        eprintln!("hassault: {e}");
        std::process::exit(1);
    }
}

fn run(args: &Args) -> Result<(), Box<dyn std::error::Error>> {
    let node = NodeApi::new(&args.server);

    eprintln!("hassault: loading {} from {}", args.map, args.server);
    let info = node.map_info(&args.map)?;
    let expected = info.cubic_size * info.plane_order.len();
    let cubes = node.map_cubes(&args.map, expected)?;
    let ssize = info.ssize;
    let spawn_count = info
        .entities
        .iter()
        .filter(|e| e.name == "playerstart")
        .count();
    let world = World::new(info, &cubes)?;
    let mesh = geometry::build_world_mesh(&world);

    eprintln!(
        "hassault: {}×{} grid, {} spawns, {} triangles",
        ssize, ssize, spawn_count, mesh.triangles
    );
    if world.info.truncated {
        // The reader fills a short cube stream with defaults rather than
        // rejecting the map, which is right — but silently playing a map that is
        // partly invented is not.
        eprintln!("hassault: warning — this map's cube stream was truncated and padded");
    }

    // The weapon numbers are *served*, never hardcoded here: the client predicts
    // recoil from `kickback` and divides its sensitivity by the scope
    // magnification, and a stale local copy is an aim that is wrong only while
    // scoped. Fetched now so a missing loadout is a startup error rather than a
    // trigger that silently does nothing — the exact failure the browser client
    // had until it started surfacing it.
    let weapons = match node.weapons() {
        Ok(weapons) if !weapons.is_empty() => {
            eprintln!("hassault: {} weapons", weapons.len());
            weapons
        }
        Ok(_) => {
            eprintln!("hassault: warning — the node served an empty loadout; nothing will fire");
            Vec::new()
        }
        Err(e) => {
            eprintln!("hassault: warning — no loadout ({e}); nothing will fire");
            Vec::new()
        }
    };

    // The armoury, which is cosmetic by definition: a node that cannot answer
    // gets a weapon in its default colours, never a refusal to play.
    let skins = match node.skins() {
        Ok(items) => {
            let equipped = viewmodel::equipped_skins(&items);
            if !equipped.is_empty() {
                eprintln!("hassault: {} weapon skins equipped", equipped.len());
            }
            equipped
        }
        Err(e) => {
            eprintln!("hassault: no skins ({e}); weapons in default colours");
            Default::default()
        }
    };

    if args.check_only {
        return Ok(());
    }

    if args.mode == Mode::Train {
        // No socket, and that is the whole of Train. Joining a room of one would
        // not be solitude — the server's roomless join is join-*or*-create, so it
        // lands in whatever match is already on this map — and it would put a
        // learner practising chained jumps in somebody's crosshair.
        if args.headless {
            return Err("--headless watches a match; --mode=train has none".into());
        }
        eprintln!(
            "hassault: training on {} — no server, no other players",
            args.map
        );
        let event_loop = EventLoop::new()?;
        let mut app = App::new(world, mesh, None, args.sensitivity, weapons, skins);
        event_loop.run_app(&mut app)?;
        return Ok(());
    }

    // A host and no room is the one combination the channel refuses outright ("a
    // remote match needs a room id"), and the refusal would land after a window
    // had already opened. The launcher checks it too; a client that only works
    // when launched from the menu is a client that cannot be debugged.
    if !args.host.is_empty() && args.room.is_empty() {
        return Err("a match on a friend's node needs --room".into());
    }

    let mut socket = MatchSocket::connect(&node.ws_url())?;
    eprintln!("hassault: joining…");
    // Hosting asks for no particular room, exactly as the browser's Host does:
    // the server opens one on this map (or seats us in one already running there).
    socket.join(&args.map, &args.room, &args.host, &args.name)?;

    if args.headless {
        return run_headless(&mut socket);
    }

    let event_loop = EventLoop::new()?;
    let mut app = App::new(world, mesh, Some(socket), args.sensitivity, weapons, skins);
    if args.mode == Mode::Host {
        // Queued, not sent: `add_bot` needs the room the welcome names, and it is
        // host-only on the channel — which is why the launcher only ever sends a
        // count with `--mode=host`.
        app.queue_bots(args.bots, args.bot_skill.clone());
    }
    event_loop.run_app(&mut app)?;
    Ok(())
}

/// No window: join, watch, and report. This is the mode that proves the wire
/// works without a renderer being involved at all.
fn run_headless(socket: &mut MatchSocket) -> Result<(), Box<dyn std::error::Error>> {
    let mut joined = false;
    let started = Instant::now();
    let mut last = Instant::now();
    loop {
        for item in socket.drain() {
            match item {
                Incoming::Event(Event::Welcome(w)) => {
                    eprintln!(
                        "hassault: joined room {} as {} ({} already in)",
                        w.room,
                        w.player_id,
                        w.players.len()
                    );
                    joined = true;
                }
                Incoming::Event(Event::Snapshot(s)) => eprintln!(
                    "hassault: tick {} ack {} — {} players",
                    s.tick,
                    s.ack,
                    s.players.len()
                ),
                Incoming::Event(Event::Error(e)) => {
                    // `not_signed_in` is the one worth explaining: the node
                    // refuses a join from an account with no username, and the
                    // fix is not in this client at all.
                    if e.code == "not_signed_in" {
                        eprintln!(
                            "hassault: {} — sign in and choose a username in the dashboard first",
                            e.message
                        );
                    } else {
                        eprintln!("hassault: server refused: {}", e.message);
                    }
                    return Ok(());
                }
                Incoming::Event(Event::Other(name)) => eprintln!("hassault: (ignored {name})"),
                Incoming::Closed(why) => {
                    eprintln!("hassault: connection closed: {why}");
                    return Ok(());
                }
            }
        }
        if joined {
            // Standing still, but sending: the point is that the server accepts
            // the frames and acknowledges them, which is what `ack` reports back.
            let dt = last.elapsed().as_secs_f32();
            last = Instant::now();
            let mut cmd = Command::new(0);
            cmd.dt = dt.min(0.05);
            socket.push_command(cmd);
            socket.flush(None)?;
        }
        if started.elapsed() > Duration::from_secs(30) {
            eprintln!("hassault: 30s elapsed, leaving");
            let _ = socket.leave();
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_mode_is_one_of_three_words() {
        assert_eq!(Mode::parse("train"), Some(Mode::Train));
        assert_eq!(Mode::parse("host"), Some(Mode::Host));
        assert_eq!(Mode::parse("join"), Some(Mode::Join));
    }

    #[test]
    fn an_unknown_mode_is_not_a_join() {
        // The whole reason `parse` returns an Option and the caller exits: a
        // typo silently becoming the default is how Train turns back into a
        // match nobody asked to be in.
        assert_eq!(Mode::parse("Train"), None);
        assert_eq!(Mode::parse("practice"), None);
        assert_eq!(Mode::parse(""), None);
    }
}
