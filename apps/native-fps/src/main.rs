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
//!
//! **Standalone — no node at all.** Started with no `--server` (which is what a
//! double-click is), the client plays straight on the public game server: the
//! same HTTP reads, answered by `backend/games_server/hassault_content.py`, and
//! the same wire on that server's `/hassault-ws` as a guest. That is what makes
//! the executable something a person can download and run. The launcher always
//! passes `--server`, so nothing it starts is affected.

mod app;
mod shell;

use std::time::{Duration, Instant};

/// **Ask hybrid-graphics drivers for the discrete GPU.**
///
/// On a laptop with NVIDIA Optimus or AMD switchable graphics the *driver*
/// chooses which GPU a process runs on, before wgpu is asked anything, and it
/// gives an unfamiliar executable the integrated one. wgpu's `HighPerformance`
/// then picks the best adapter from a list the discrete GPU is not on. These two
/// exported symbols are the documented opt-in for both vendors; they must be
/// exported **from the executable**, which a Rust binary does not do on its own —
/// `build.rs` passes the linker `/EXPORT` for each.
#[cfg(windows)]
#[no_mangle]
#[used]
#[allow(non_upper_case_globals)]
pub static NvOptimusEnablement: u32 = 1;
#[cfg(windows)]
#[no_mangle]
#[used]
#[allow(non_upper_case_globals)]
pub static AmdPowerXpressRequestHighPerformance: u32 = 1;

use winit::event_loop::EventLoop;

use hassault_native::api::NodeApi;
use hassault_native::controls::Controls;
use hassault_native::geometry;
use hassault_native::net::{Incoming, MatchSocket};
use hassault_native::protocol::{Command, Event};
use hassault_native::radar;
use hassault_native::settings::{Settings, SettingsWriter};
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
    /// dummy range included (`training.rs`, the port of `training.ts`).
    Train,
    /// Open (or join) a match here and field bots in it.
    Host,
    /// Enter a match that exists — a room id, or any room on the map.
    Join,
    /// Build a map. No socket and no match, like Train — plus a draft on the
    /// node, which is served as a map named `draft:<id>`, so the boot sequence
    /// below needs no special case at all.
    Edit,
    /// A **rated** match, simulated by the game server rather than by any node.
    /// The client's only difference is a flag on the join: this node proxies, and
    /// every frame after it is the wire this client already speaks.
    Ranked,
    /// A **rated duel**: queue on the game server (through this node) and play
    /// whoever it pairs us with, on its duel map (`--map`, from `/ranked/maps`).
    Duel,
}

impl Mode {
    fn parse(v: &str) -> Option<Mode> {
        match v {
            "train" => Some(Mode::Train),
            "host" => Some(Mode::Host),
            "join" => Some(Mode::Join),
            "edit" => Some(Mode::Edit),
            "ranked" => Some(Mode::Ranked),
            "duel" => Some(Mode::Duel),
            _ => None,
        }
    }
}

/// The game server a client with no node plays on. Also where the executable
/// is downloaded from, so the address a player got it at is the one it dials.
const PUBLIC_GAME_SERVER: &str = "https://horrible-games.fly.dev";

struct Args {
    /// A node's origin, or a game server's when `standalone`.
    server: String,
    /// **No node.** `server` is a game server and everything comes from it: the
    /// map and the numbers over HTTP, the match on its `/hassault-ws` as a
    /// guest. True unless `--server` names a node — so it is what no arguments
    /// at all means, which is the only thing a double-click can say.
    standalone: bool,
    /// Whether `--name` was passed. Standalone, the name is the guest callsign
    /// people actually see, so a default nobody chose is replaced by a stored one.
    name_given: bool,
    /// Started with no arguments whatsoever: almost certainly from Explorer,
    /// where a failure's one line of explanation dies with the console window
    /// unless something holds it open.
    bare: bool,
    map: String,
    mode: Mode,
    room: String,
    host: String,
    name: String,
    /// Which **game** mode to open: `dm`, `tdm`, `ctf`, `defuse`.
    ///
    /// Distinct from `mode` above, which is how this *client* was launched —
    /// train, host, join, edit, ranked. Two different things that both want the
    /// word, so the wire one is spelled out: `--game=`.
    ///
    /// Empty asks the server for its default, which keeps a launcher that knows
    /// nothing about modes working unchanged.
    game: String,
    /// Bots to field, `--mode=host` only.
    bots: u32,
    bot_skill: String,
    /// `--mode=train` only: an aim drill to start instead of the free range.
    drill: Option<String>,
    /// Multiplies the turn per unit of raw mouse movement.
    /// `None` unless the launcher passed one, in which case it outranks the
    /// stored setting — it *is* the stored setting, as the pane last saw it.
    sensitivity: Option<f32>,
    headless: bool,
    /// `--mode=edit --new`: start from solid rock rather than from a map.
    blank: bool,
    /// Load and mesh the map, print what it found, and exit without connecting.
    check_only: bool,
    spawn: Option<usize>,
    /// `--adapter=<name>` / `--backend=<api>` / `--low-power`: outrank the saved
    /// GPU choice for this run, so a GPU that will not start can be stepped
    /// around from a terminal without a menu to reach.
    adapter: Option<String>,
    backend: Option<String>,
    low_power: bool,
    /// Open in a window this run, whatever the saved setting says — for a
    /// session somebody wants to watch beside something else, and for tests.
    windowed: bool,
    /// `--set=<key>=<value>`: settings-bag rows for this run only, laid over
    /// what the node holds and never written back. How a preset is compared
    /// against another from a terminal without touching anybody's settings.
    overrides: Vec<(String, serde_json::Value)>,
    /// `--bench=<map>`: fly that map's benchmark path in Train, print one
    /// `BENCH {json}` line on stdout and exit. See `bench.rs`.
    bench: Option<String>,
    /// Something on the command line said **where to go**: a map, a room, a
    /// mode, a check. A launch with a destination goes straight there, as it
    /// always has — the dashboard is the menu for a client it launches.
    destination: bool,
    /// `--menu` / `--no-menu`, when one was passed.
    menu_flag: Option<bool>,
    /// Open into the front end (`shell.rs`) instead of a match: standalone
    /// with nowhere to go, which is what a double-click is.
    menu: bool,
}

impl Default for Args {
    fn default() -> Args {
        Args {
            // With nothing said, the public game server and no node. It used to
            // be `http://127.0.0.1:8000`, which was never right by default
            // either: `HORRIBLE_DEV_BACKEND_PORT` moves the node (Hyper-V
            // reserves ranges on Windows that can swallow 8000), so the launcher
            // has always passed `--server` rather than anyone guessing.
            server: PUBLIC_GAME_SERVER.into(),
            standalone: true,
            name_given: false,
            bare: false,
            map: "hd_dust2".into(),
            // Joining is the least surprising default and the one the old
            // argument-free launch effectively did.
            mode: Mode::Join,
            blank: false,
            room: String::new(),
            host: String::new(),
            name: "player".into(),
            // Empty, not "dm": the server owns the default, and a client that
            // named one here would be a second place that decides it.
            game: String::new(),
            bots: 0,
            bot_skill: "normal".into(),
            drill: None,
            sensitivity: None,
            headless: false,
            check_only: false,
            spawn: None,
            adapter: None,
            backend: None,
            low_power: false,
            windowed: false,
            overrides: Vec::new(),
            bench: None,
            destination: false,
            menu_flag: None,
            menu: false,
        }
    }
}

fn parse_args() -> Args {
    parse_from(std::env::args().skip(1))
}

fn parse_from(argv: impl Iterator<Item = String>) -> Args {
    let mut args = Args {
        bare: true,
        ..Args::default()
    };
    for arg in argv {
        args.bare = false;
        if let Some(v) = arg.strip_prefix("--server=") {
            // A node. The last of `--server` / `--game-server` wins, like every
            // other repeated flag here.
            args.server = v.to_string();
            args.standalone = false;
        } else if let Some(v) = arg.strip_prefix("--game-server=") {
            // A game server other than the public one — a local one on :9200,
            // which is how standalone is tested without touching production.
            args.server = v.to_string();
            args.standalone = true;
        } else if let Some(v) = arg.strip_prefix("--map=") {
            args.destination = true;
            args.map = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--mode=") {
            args.destination = true;
            match Mode::parse(v) {
                Some(m) => args.mode = m,
                // Refused rather than defaulted: a typo'd mode silently becoming
                // "join" is how Train quietly turns back into a match.
                None => {
                    eprintln!("hassault: unknown --mode={v} (train, edit, host, join or ranked)");
                    std::process::exit(2);
                }
            }
        } else if let Some(v) = arg.strip_prefix("--bots=") {
            args.destination = true;
            if let Ok(n) = v.parse::<u32>() {
                args.bots = n;
            }
        } else if let Some(v) = arg.strip_prefix("--bot-skill=") {
            args.bot_skill = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--drill=") {
            args.destination = true;
            args.drill = Some(v.to_string());
        } else if let Some(v) = arg.strip_prefix("--game=") {
            args.destination = true;
            args.game = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--room=") {
            args.destination = true;
            args.room = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--host=") {
            args.destination = true;
            args.host = v.to_string();
        } else if let Some(v) = arg.strip_prefix("--name=") {
            args.name = v.to_string();
            args.name_given = true;
        } else if let Some(v) = arg.strip_prefix("--sensitivity=") {
            if let Ok(n) = v.parse::<f32>() {
                // A zero or negative multiplier is a view that cannot turn, which
                // reads as broken input rather than as a setting.
                if n.is_finite() && n > 0.0 {
                    args.sensitivity = Some(n);
                }
            }
        } else if arg.starts_with("--max-fps=") || arg.starts_with("--raw-input=") {
            // Accepted and ignored, for the launcher's older request shape.
            // There is no frame cap to set — the present mode decides — and raw
            // input is not an option, it is how the mouse is read.
        } else if let Some(v) = arg.strip_prefix("--bench=") {
            args.destination = true;
            args.bench = Some(v.to_string());
        } else if arg == "--new" {
            args.blank = true;
        } else if let Some(v) = arg.strip_prefix("--spawn=") {
            args.destination = true;
            if let Ok(n) = v.parse::<usize>() {
                args.spawn = Some(n);
            }
        } else if let Some(v) = arg.strip_prefix("--adapter=") {
            args.adapter = Some(v.to_string());
        } else if let Some(v) = arg.strip_prefix("--backend=") {
            args.backend = Some(v.to_string());
        } else if arg == "--low-power" {
            args.low_power = true;
        } else if arg == "--windowed" {
            args.windowed = true;
        } else if let Some(v) = arg.strip_prefix("--set=") {
            if let Some((key, value)) = v.split_once('=') {
                // JSON where it parses (numbers, booleans), a string otherwise,
                // so `--set=hassault.video.quality=ultra` needs no quoting.
                let value = serde_json::from_str(value)
                    .unwrap_or_else(|_| serde_json::Value::String(value.to_string()));
                args.overrides.push((key.to_string(), value));
            }
        } else if arg == "--list-adapters" {
            // For the node's settings UI, which has no GPU API of its own: one
            // JSON document and out, before anything touches the network.
            println!("{}", hassault_native::gpu::adapters_json());
            std::process::exit(0);
        } else if arg == "--headless" {
            args.headless = true;
            args.destination = true;
        } else if arg == "--check" {
            args.check_only = true;
            args.destination = true;
        } else if arg == "--menu" {
            args.menu_flag = Some(true);
        } else if arg == "--no-menu" {
            args.menu_flag = Some(false);
        } else if arg == "--help" || arg == "-h" {
            eprintln!(
                "hassault (native)\n\
                 \n\
                 With no --server this plays online, on the public game server,\n\
                 as a guest. Settings are then kept in a local file. With nothing\n\
                 saying where to go (no --map, --room or --mode) it opens the\n\
                 main menu.\n\
                 \n\
                   --menu              open the main menu even with a destination\n\
                   --no-menu           go straight into a match, as --map=hd_dust2\n\
                   --server=<origin>   a dashboard node's HTTP origin; the launcher passes it\n\
                   --game-server=<origin>  a game server to play on with no node\n\
                   \x20                   (default https://horrible-games.fly.dev)\n\
                   --map=<name>        map to load and join\n\
                   --mode=<mode>       train (no server), host, join (default), or\n\
                   \x20                   ranked (the game server adjudicates)\n\
                   --bots=<n>          bots to field, --mode=host only\n\
                   --bot-skill=<s>     easy, normal or hard (default normal)\n\
                   --drill=<id>        --mode=train: run this aim drill (see /api/hassault/drills)\n\
                   --game=<mode>       dm, tdm, ctf or defuse (ignored with --room)\n\
                   --room=<id>         join a specific room rather than any on the map\n\
                   --host=<node id>    that room is on a friend's node\n\
                   --name=<label>      your callsign with no node; a node uses your\n\
                   \x20                   account's username and ignores it\n\
                   --sensitivity=<n>   turn per unit of raw mouse movement (default 1)\n\
                   --new               --mode=edit: start from solid rock\n\
                   --headless          no window: connect, join, and log\n\
                   --check             load and mesh the map, print it, and exit\n\
                   --adapter=<name>    open this GPU (see --list-adapters)\n\
                   --backend=<api>     auto, dx12, vulkan, metal or gl\n\
                   --low-power         prefer the power-saving GPU\n\
                   --windowed          open in a window, whatever the saved setting\n\
                   --set=<key>=<value> a settings row for this run only, never saved\n\
                   --bench=<map>       fly the map's benchmark path offline, print\n\
                   \x20                   `BENCH {{json}}` and exit (vsync and cap off;\n\
                   \x20                   pick the tier with --set=hassault.video.quality=)\n\
                   --list-adapters     print the GPUs as JSON and exit\n"
            );
            std::process::exit(0);
        }
    }
    // A node is somebody's dashboard, and the dashboard is its menu: only a
    // client with no node opens its own. `--menu` and `--no-menu` say so
    // outright; otherwise a destination on the command line means go there.
    args.menu = args.standalone && args.menu_flag.unwrap_or(!args.destination);
    args
}

fn main() {
    let mut args = parse_args();
    if let Some(map) = args.bench.clone() {
        // Checked before anything loads: a typo should fail in a millisecond,
        // not after the map has been fetched and meshed.
        if hassault_native::bench::path_for(&map).is_none() {
            eprintln!(
                "hassault: no bench path for {map} (have: {})",
                hassault_native::bench::maps().join(", ")
            );
            std::process::exit(2);
        }
        // Offline, on that map: a bench measures this machine drawing the
        // map, and a match would add a server and other players to it.
        args.map = map;
        args.mode = Mode::Train;
    }
    if let Err(e) = run(&args) {
        // One line, naming the thing that failed. The overwhelmingly common
        // failure is "the node is not running", and it deserves to say so rather
        // than surfacing as a window that never appears.
        eprintln!("hassault: {e}");
        if args.bare {
            hold_console_open();
        }
        std::process::exit(1);
    }
}

/// Keep the console up until somebody has read it.
///
/// Double-clicked, the executable gets a console of its own that closes the
/// instant the process exits — so "could not reach the server" would flash for a
/// frame and be gone, and the game would simply seem not to start. Only when
/// started with no arguments: anything launched with flags has a terminal or a
/// launcher reading its stderr, and must exit rather than wait on a key.
fn hold_console_open() {
    eprintln!("\nPress Enter to close.");
    let mut line = String::new();
    let _ = std::io::stdin().read_line(&mut line);
}

/// A callsign for a guest who has not chosen one: `Operative-417`, the browser
/// client's own default (`useGuestSession.ts`), so the two read alike in a room.
/// The clock is the only entropy here and all this needs — it is a label, and
/// two players drawing the same three digits is not a collision anything minds.
fn fresh_callsign() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    format!("Operative-{}", 100 + nanos % 900)
}

/// The name a guest plays under: `--name` if given, else the one stored from a
/// previous launch, else a fresh one. `true` when it is new and wants saving.
///
/// Cut to 16 characters because the game server cuts it there too
/// (`hassault_ws`), and a name that is one thing here and another in the
/// scoreboard is a small lie on every line of the kill feed.
fn guest_callsign(given: Option<&str>, stored: &serde_json::Value) -> (String, bool) {
    let clean = |s: &str| s.trim().chars().take(16).collect::<String>();
    if let Some(name) = given.map(clean).filter(|n| !n.is_empty()) {
        return (name, false);
    }
    let kept = stored
        .get(hassault_native::settings::KEY_CALLSIGN)
        .and_then(|v| v.as_str())
        .map(clean)
        .filter(|n| !n.is_empty());
    match kept {
        Some(name) => (name, false),
        None => (fresh_callsign(), true),
    }
}

/// Lay this run's command-line choices over the stored settings.
///
/// Nothing here is written back, so a terminal experiment does not become the
/// menu's value. Shared by a direct launch and the front end (`shell.rs`).
fn apply_run_flags(settings: &mut Settings, args: &Args) {
    // An explicit `--sensitivity` outranks the stored one: it is the launcher
    // passing what the pane's own slider says, and the two are the same setting.
    if let Some(sensitivity) = args.sensitivity {
        settings.sensitivity = sensitivity;
    }
    // The GPU flags outrank the saved choice for this run only.
    if let Some(name) = args.adapter.as_deref() {
        settings.gpu.adapter = hassault_native::gpu::adapters()
            .iter()
            .position(|a| a.name.eq_ignore_ascii_case(name))
            .map(|i| i + 1)
            .unwrap_or_else(|| {
                eprintln!("hassault: no GPU named {name:?}; see --list-adapters");
                0
            });
    }
    if let Some(api) = args.backend.as_deref() {
        settings.gpu.backend = hassault_native::gpu::BackendPref::parse(api);
    }
    if args.low_power {
        settings.gpu.low_power = true;
    }
    if args.windowed {
        settings.video.fullscreen = false;
    }
}

fn run(args: &Args) -> Result<(), Box<dyn std::error::Error>> {
    if args.menu {
        // Nowhere to go: open the window first and let the player say.
        return shell::run(args);
    }
    let node = NodeApi::new(&args.server);
    if args.standalone {
        eprintln!(
            "hassault: playing on {} with no node (pass --server=<origin> to use one)",
            args.server
        );
    }
    // With no node there is no settings bag to read, so the same document is
    // kept in a file. See `settings.rs`.
    let local_settings = if args.standalone {
        let path = hassault_native::settings::local_settings_path();
        if path.is_none() {
            eprintln!("hassault: warning — no config directory; settings will not be saved");
        }
        path
    } else {
        None
    };

    // Edit mode opens a draft first, and then loads it **as a map**. That is the
    // whole of the special-casing: a draft is served at `draft:<id>` through the
    // same two routes a match uses, so everything below this point — the parse,
    // the mesh, the radar plan, the check — is the ordinary boot sequence and
    // knows nothing about the designer.
    let mut draft: Option<hassault_native::api::DraftInfo> = None;
    let map_name = if args.mode == Mode::Edit {
        let opened = node
            .create_draft(if args.blank { "" } else { &args.map })
            .map_err(|e| -> Box<dyn std::error::Error> {
                if e.is_missing() {
                    // A draft is a document on a server that keeps them: a
                    // dashboard node always does, a game server only when
                    // asked to. The bare 404 reads as the server being broken.
                    "this server keeps no map drafts: run a game server with                      HASSAULT_ENABLE_DRAFTS=1 (--game-server=<origin>), or name                      a dashboard node (--server=<origin>)"
                        .into()
                } else {
                    e.into()
                }
            })?;
        eprintln!(
            "hassault: editing {} as {}",
            if args.blank { "a new map" } else { &args.map },
            opened.map_name
        );
        let name = opened.map_name.clone();
        draft = Some(opened);
        name
    } else {
        args.map.clone()
    };

    eprintln!("hassault: loading {} from {}", map_name, args.server);
    let info = node.map_info(&map_name)?;
    let expected = info.cubic_size * info.plane_order.len();
    let cubes = node.map_cubes(&map_name, expected)?;
    let ssize = info.ssize;
    let spawn_count = info
        .entities
        .iter()
        .filter(|e| e.name == "playerstart")
        .count();
    // Captured before `info` is moved into the world: edit mode sizes its
    // ownership fetch from it.
    let info_cubic = info.cubic_size;
    let world = World::new(info, &cubes)?;
    let mesh = geometry::build_world_mesh(&world);
    // The radar's floor plan, merged once here beside the mesh rather than per
    // frame — it is a property of the map, exactly as the mesh is. Built before
    // `--check` returns so the check exercises it: a map that produces a plan
    // nobody can draw should fail on a machine with no GPU, not on a player's.
    let plan = radar::floor_plan(&world);

    eprintln!(
        "hassault: {}×{} grid, {} spawns, {} triangles, {} radar runs",
        ssize,
        ssize,
        spawn_count,
        mesh.triangles,
        plan.len()
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
    // What the pause menu edits, read from the node so the pane and the game
    // hold one set of preferences rather than two. A node that only served the
    // map still gets a playable client — the defaults are the game's, not an
    // error state — so this warns and carries on.
    let mut stored_loadout: Option<serde_json::Value> = None;
    let mut stored_agents = serde_json::Value::Null;
    let stored = if args.standalone {
        Ok(local_settings
            .as_deref()
            .map(hassault_native::settings::read_local)
            .unwrap_or_else(|| serde_json::json!({})))
    } else {
        node.settings()
    };
    // The guest callsign rides the same document, read before the overrides
    // are laid over it below.
    let stored_callsign = match &stored {
        Ok(values) if args.standalone => Some(guest_callsign(
            args.name_given.then_some(args.name.as_str()),
            values,
        )),
        _ => None,
    };
    let (mut settings, controls) = match stored.map(|mut values| {
        if let Some(bag) = values.as_object_mut() {
            for (key, value) in &args.overrides {
                bag.insert(key.clone(), value.clone());
            }
        }
        values
    }) {
        Ok(values) => (
            {
                // A document the server validates, not a setting this client
                // interprets, so it is read raw.
                stored_loadout = values
                    .get("hassault.loadout")
                    .and_then(|v| v.as_str())
                    .and_then(|raw| serde_json::from_str(raw).ok());
                stored_agents = values
                    .get("hassault.agents")
                    .and_then(|v| v.as_str())
                    .and_then(|raw| serde_json::from_str(raw).ok())
                    .unwrap_or(serde_json::Value::Null);
                Settings::from_values(&values)
            },
            // The pane's Controls screen writes this; the native client used to
            // ignore it and hard-code every key.
            Controls::from_values(&values),
        ),
        Err(e) => {
            eprintln!("hassault: warning — no saved settings ({e}); using defaults");
            (Settings::default(), Controls::default())
        }
    };
    apply_run_flags(&mut settings, args);
    if args.bench.is_some() {
        // The tier's whole preset, not just its label: `quality` is a name,
        // and the rows it stands for (MSAA, shadows, lights) are stored one by
        // one, so `--set=hassault.video.quality=high` alone would bench
        // whatever this machine's rows happen to say under a "high" heading.
        // Uncapped, at full render scale, windowed at a fixed size (`app.rs`
        // `BENCH_SIZE`), so a row in the table carries its own conditions.
        // None of it is written back.
        //
        // Rebuilt from the tier and the `--set` rows **alone**, in
        // `from_values`' order — preset first, then rows — so an explicit row
        // still wins (`--set=hassault.video.msaa=1` is how one row's cost is
        // isolated), and nothing this machine happens to have stored leaks in.
        let mut bag = serde_json::Map::new();
        bag.insert(
            hassault_native::settings::KEY_QUALITY.to_string(),
            serde_json::Value::String(settings.video.quality.key().to_string()),
        );
        for (key, value) in &args.overrides {
            bag.insert(key.clone(), value.clone());
        }
        settings.video = Settings::from_values(&serde_json::Value::Object(bag)).video;
        // Uncapped, unsynced, native resolution, windowed — unless the run
        // asks otherwise: a scaled run is how a fill-bound GPU is told from a
        // vertex-bound one, and a capped or synced run is how the frame
        // limiter and the present queue are measured.
        use hassault_native::settings as keys;
        let asked = |key: &str| args.overrides.iter().any(|(k, _)| k == key);
        if !asked(keys::KEY_VSYNC) {
            settings.video.vsync = false;
        }
        if !asked(keys::KEY_FPS_LIMIT) {
            settings.video.fps_limit = 0;
        }
        if !asked(keys::KEY_RENDER_SCALE) {
            settings.video.render_scale = 1.0;
        }
        settings.video.fullscreen = false;
    }
    let writer = match (&local_settings, args.standalone) {
        (Some(path), _) => SettingsWriter::to_file(path.clone()),
        (None, true) => SettingsWriter::disabled(),
        (None, false) => SettingsWriter::new(&args.server),
    };

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

    // The pouch. Non-fatal like the loadout and for the same reason: a node that
    // cannot answer leaves the tray empty and the throw key doing nothing, which
    // is a game missing a mechanic — not a client that refuses to start. The
    // alternative, a table of four grenades in Rust, is worse than empty: the
    // wire carries a **slot index**, so a local list that drifted by one from
    // `grenades.GRENADES` would throw a smoke where the player asked for a flash
    // and report nothing at all.
    let tacticals = match node.tacticals() {
        Ok(specs) if !specs.is_empty() => {
            eprintln!("hassault: {} grenades", specs.len());
            specs
        }
        Ok(_) => {
            eprintln!("hassault: warning — the node served no grenades; nothing to throw");
            Vec::new()
        }
        Err(e) => {
            eprintln!("hassault: warning — no grenades ({e}); nothing to throw");
            Vec::new()
        }
    };

    // The constants a throw is integrated with, for the trajectory preview.
    // Non-fatal, and the fallback is **no preview** rather than a guessed one: a
    // trajectory drawn with numbers this client invented would be an aiming aid
    // confidently pointing somewhere the grenade will not go, which is worse
    // than not drawing one.
    let throw_physics = match node.throw_physics() {
        Ok(physics) => Some(physics),
        Err(e) => {
            eprintln!("hassault: warning — no throw constants ({e}); no trajectory preview");
            None
        }
    };

    // What each item kind gives, and how close you have to get. Non-fatal like
    // the loadout: a node too old to serve it leaves the range's items drawn but
    // inert — a missing convenience, not a client that refuses to start. In a
    // match nothing reads it at all, because the server resolves every pickup.
    let item_table = match node.items() {
        Ok(table) => {
            eprintln!("hassault: {} item kinds", table.kinds.len());
            table
        }
        Err(e) => {
            eprintln!("hassault: warning — no item table ({e}); range items will give nothing");
            Default::default()
        }
    };

    // The body every hit is resolved against. Fetched rather than held, for the
    // reason `HitboxSpec` gives: the local copy this replaced drew a crouched
    // player four percent shorter than the server could hit them, all of it at
    // the top, which is where the head band is.
    //
    // A node that cannot answer falls back to the shipped body and says so. That
    // is wrong only if somebody has tuned the hitbox; drawing against nothing is
    // wrong always.
    let hitbox = match node.hitbox() {
        Ok(spec) => {
            eprintln!("hassault: hitbox {} ({})", spec.spec_id, spec.shape);
            if !spec.drawable() {
                eprintln!(
                    "hassault: warning — this build cannot draw a '{}' body; nobody will be drawn",
                    spec.shape
                );
            }
            spec
        }
        Err(e) => {
            eprintln!("hassault: warning — no hitbox served ({e}); using the shipped body");
            Default::default()
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

    // The developer console's registry. Non-fatal in the strongest sense: an
    // older node has no such route, and the console still opens, still sends
    // lines and still applies client CVars — it loses completion and the
    // type/range validation that comes with a definition. Declaring a fallback
    // table here instead would be a third copy of `console.py`'s list, and the
    // way a third copy fails is a console offering commands the node has never
    // heard of.
    let definitions = match node.console_definitions() {
        Ok(defs) => {
            eprintln!(
                "hassault: console registry — {} cvars, {} commands, {} macros",
                defs.cvars.len(),
                defs.commands.len(),
                defs.macros.len()
            );
            defs
        }
        Err(e) => {
            eprintln!("hassault: no console registry ({e}); completion is off");
            Default::default()
        }
    };

    if args.check_only {
        return Ok(());
    }

    if args.mode == Mode::Edit {
        // No socket, exactly like Train, and for a stronger version of the same
        // reason: there is no match to be in, so there is no server to lie to
        // about a camera that ignores gravity and walls.
        let Some(opened) = draft else {
            return Err("edit mode opened no draft".into());
        };
        if args.headless {
            return Err("--headless watches a match; --mode=edit has none".into());
        }
        // Which brush painted each cell. Fetched rather than derived: brushes
        // compose by overwrite, so ownership is only knowable while they are
        // being applied, and it is what turns a crosshair on a wall into a brush
        // you can drag. Non-fatal — without it the camera still flies and the
        // console still edits; only picking goes quiet.
        let owners = match node.draft_owners(&opened.id, info_cubic) {
            Ok(bytes) => {
                hassault_native::editor::decode_owners(&bytes, info_cubic).unwrap_or_default()
            }
            Err(e) => {
                eprintln!("hassault: warning — no brush ownership ({e}); picking is off");
                Vec::new()
            }
        };
        eprintln!(
            "hassault: edit mode — fly with WASD, space/shift for height, scroll for speed, click to select, drag to reshape, [ and ] for floor height, E to place, Z/Y undo, R save. Everything else is `edit.*` in the console."
        );
        let event_loop = EventLoop::new()?;
        let mut app = App::new(
            world,
            mesh,
            None,
            settings,
            writer,
            weapons,
            tacticals,
            throw_physics.clone(),
            skins,
            hitbox,
            item_table.clone(),
            definitions,
            plan,
        );
        app.controls = controls.clone();
        app.enter_edit_mode(&args.server, opened, owners);
        event_loop.run_app(&mut app)?;
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
        let settings_tier = settings.video.quality.label();
        let event_loop = EventLoop::new()?;
        let mut app = App::new(
            world,
            mesh,
            None,
            settings,
            writer,
            weapons,
            tacticals,
            throw_physics.clone(),
            skins,
            hitbox,
            item_table.clone(),
            definitions,
            plan,
        );
        app.controls = controls.clone();
        if let Some(sp) = args.spawn {
            app.spawn_at_index(sp);
        }
        if let Some(map) = args.bench.as_deref() {
            let path = hassault_native::bench::path_for(map).unwrap_or_default();
            let tier = settings_tier.to_lowercase();
            eprintln!("hassault: benchmarking {map} at {tier}");
            app.start_bench(hassault_native::bench::BenchRun::new(
                map,
                path,
                &tier,
                hassault_native::bench::warmup_seconds(),
            ));
        }
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

    let ws_url = match &stored_callsign {
        Some((callsign, fresh)) => {
            if *fresh {
                // Kept, so the next launch is the same player rather than a
                // stranger with a new number.
                writer.save(
                    hassault_native::settings::KEY_CALLSIGN,
                    serde_json::Value::String(callsign.clone()),
                );
            }
            eprintln!("hassault: guest callsign {callsign} (change it with --name=<label>)");
            node.guest_ws_url(callsign)
        }
        None => node.ws_url(),
    };
    let mut socket = MatchSocket::connect(&ws_url)?;
    eprintln!("hassault: joining…");
    // Hosting asks for no particular room, exactly as the browser's Host does:
    // the server opens one on this map (or seats us in one already running there).
    if args.mode == Mode::Duel {
        // No join: the server seats us when it pairs, and its welcome arrives
        // like any other. The map was loaded from `--map` above.
        eprintln!("hassault: queueing for a rated duel…");
        socket.queue_duel()?;
    } else {
        socket.join(
            &args.map,
            &args.room,
            &args.host,
            &args.name,
            matches!(args.mode, Mode::Ranked),
            &args.game,
        )?;
    }

    if args.headless {
        return run_headless(&mut socket);
    }

    let event_loop = EventLoop::new()?;
    let mut app = App::new(
        world,
        mesh,
        Some(socket),
        settings,
        writer,
        weapons,
        tacticals,
        throw_physics.clone(),
        skins,
        hitbox,
        item_table,
        definitions,
        plan,
    );
    app.controls = controls;
    app.set_own_room(args.mode == Mode::Host && args.host.is_empty());
    if args.mode == Mode::Host {
        // Queued, not sent: `add_bot` needs the room the welcome names, and it is
        // host-only on the channel — which is why the launcher only ever sends a
        // count with `--mode=host`.
        app.queue_bots(args.bots, args.bot_skill.clone());
    }
    // The loadout to carry, as the pane stored it.
    if let Some(choice) = stored_loadout {
        app.set_loadout(choice);
    }
    // The LOADOUT page. A node too old to serve agents offers weapons only.
    let agents = node.agents().unwrap_or_else(|e| {
        eprintln!("hassault: no agents served ({e}); the loadout page offers weapons only");
        Vec::new()
    });
    app.set_loadout_menu(agents, stored_agents);
    // The STORE page asks the node, which proxies the game server's wallet.
    app.set_store_node(&args.server);
    if let (Mode::Train, Some(drill)) = (args.mode, args.drill.as_deref()) {
        // Fetched here rather than inside the app, like the loadout: the
        // app's constructor is where Train places the player, and the drill
        // starts from wherever that put them.
        match node.drill(drill, &args.map, app.offline_position()) {
            Ok(spec) => app.set_drill(spec),
            Err(e) => eprintln!("hassault: no drill {drill} ({e}); free range instead"),
        }
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
                // Headless never pings, so this only arrives if something else
                // on the socket did. Nothing to report.
                Incoming::Event(Event::Pong(_)) => {}
                // Reported once per name by `protocol::classify`, which is
                // where every consumer of this socket now learns what it is
                // dropping — this loop used to be the only one that said so,
                // and it is the one nobody plays in.
                Incoming::Event(Event::Other(_)) => {}
                Incoming::Event(Event::ConsoleRes(_)) => {}
                // The headless watcher reports the room's membership, which is
                // most of what it is for.
                Incoming::Event(Event::Joined(j)) => {
                    eprintln!("hassault: {} joined", j.player.name)
                }
                Incoming::Event(Event::Left(l)) => {
                    eprintln!("hassault: {} left", l.player_id)
                }
                Incoming::Event(Event::Roster(_)) => {}
                Incoming::Event(Event::Cosmetics(_)) => {}
                Incoming::Event(Event::DuelQueue(_)) => {}
                Incoming::Event(Event::RatingUpdate(r)) => {
                    eprintln!("hassault: rating {:.0} -> {:.0}", r.before, r.after)
                }
                Incoming::Event(Event::Invite(i)) => {
                    eprintln!("hassault: invite to room {} from {}", i.room, i.host_name)
                }
                Incoming::Event(Event::Invites(_)) => {}
                Incoming::Event(Event::Chat(c)) => {
                    eprintln!("hassault: chat [{}]: {}", c.sender_name, c.text);
                }
                Incoming::Event(Event::ChatRefused(reason)) => {
                    eprintln!("hassault: chat not sent: {reason}");
                }
                Incoming::Event(Event::Voice(v)) => {
                    eprintln!(
                        "hassault: voice [{}]: transmitting={}",
                        v.player_name, v.transmitting
                    );
                }
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

    fn parse(argv: &[&str]) -> Args {
        parse_from(argv.iter().map(|a| a.to_string()))
    }

    #[test]
    fn no_arguments_is_the_public_game_server_and_no_node() {
        // What a double-click says, which is nothing. It has to mean "play".
        let args = parse(&[]);
        assert!(args.standalone);
        assert!(args.bare);
        assert_eq!(args.server, PUBLIC_GAME_SERVER);
        assert_eq!(args.mode, Mode::Join);
    }

    #[test]
    fn a_double_click_opens_the_menu_and_a_destination_skips_it() {
        let parse = |argv: &[&str]| parse_from(argv.iter().map(|s| s.to_string()));
        // No arguments: the public server, no node, and nowhere to go.
        assert!(parse(&[]).menu);
        // Flags that are about the session, not the destination, keep it.
        assert!(
            parse(&[
                "--windowed",
                "--name=Sable",
                "--game-server=http://127.0.0.1:9200"
            ])
            .menu
        );
        // Anything that names a destination goes there, as it always did.
        for flag in [
            "--map=hd_aim",
            "--mode=train",
            "--room=abc",
            "--headless",
            "--check",
            "--bench=hd_aim",
        ] {
            assert!(!parse(&[flag]).menu, "{flag} should skip the menu");
        }
        // A node is never given a menu: the dashboard is one.
        assert!(!parse(&["--server=http://127.0.0.1:8000"]).menu);
        assert!(!parse(&["--server=http://127.0.0.1:8000", "--menu"]).menu);
        // And both can be said outright.
        assert!(parse(&["--map=hd_aim", "--menu"]).menu);
        assert!(!parse(&["--no-menu"]).menu);
    }

    #[test]
    fn the_launcher_s_server_flag_still_means_a_node() {
        // The launcher always passes `--server`. If that ever stopped meaning a
        // node, every pane launch would dial `/hassault-ws` on a dashboard.
        let args = parse(&["--server=http://127.0.0.1:8100", "--map=hd_pit"]);
        assert!(!args.standalone);
        assert!(!args.bare);
        assert_eq!(args.server, "http://127.0.0.1:8100");
    }

    #[test]
    fn a_game_server_can_be_named_and_other_flags_keep_it_standalone() {
        let args = parse(&["--game-server=http://127.0.0.1:9200", "--windowed"]);
        assert!(args.standalone);
        assert_eq!(args.server, "http://127.0.0.1:9200");
        // A flag that is not about the server does not move it off the default.
        let args = parse(&["--map=hd_mirage"]);
        assert!(args.standalone && !args.bare);
        assert_eq!(args.server, PUBLIC_GAME_SERVER);
    }

    #[test]
    fn a_guest_keeps_one_callsign_until_told_otherwise() {
        use serde_json::json;
        let key = hassault_native::settings::KEY_CALLSIGN;

        // Nothing stored: a fresh one, flagged so it gets saved.
        let (name, fresh) = guest_callsign(None, &json!({}));
        assert!(fresh);
        assert!(name.starts_with("Operative-") && name.len() == "Operative-000".len());

        // Stored: the same player as last time, and nothing to write.
        let stored = json!({ key: "  Ghost  " });
        assert_eq!(guest_callsign(None, &stored), ("Ghost".to_string(), false));

        // `--name` outranks it for this run, cut where the server cuts it.
        assert_eq!(
            guest_callsign(Some("a-very-long-callsign-indeed"), &stored),
            ("a-very-long-call".to_string(), false)
        );
        // A blank `--name` is not a name; neither is a stored non-string.
        assert_eq!(guest_callsign(Some("   "), &stored).0, "Ghost");
        assert!(guest_callsign(None, &json!({ key: 7 })).1);
    }
}
