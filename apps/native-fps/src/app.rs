//! The window, the input, and the frame loop.
//!
//! **This file is where the native client earns its existence**, and it is worth
//! being precise about why, because "native is faster" is not an argument.
//!
//! - **Raw mouse input.** `DeviceEvent::MouseMotion` is the mouse's own delta, in
//!   device units, before the OS has applied pointer acceleration, before it has
//!   been quantised to a pixel grid, and before it has been clamped by a screen
//!   edge. A browser gives you `movementX`, which is a *cursor* delta: it is
//!   already accelerated, already integral, and on some platforms already
//!   smoothed. That difference is the whole of "the aim feels different", and it
//!   cannot be recovered from the browser side at any frame rate.
//! - **An uncapped present mode.** See `renderer::pick_present_mode`. A browser
//!   composites through the page, which adds at least a frame.
//! - **Frames driven by the loop, not by a callback.** `ControlFlow::Poll` runs
//!   the loop as fast as the GPU will present.
//!
//! Everything else — the map, the protocol, identity — is stage B1's and unchanged.
//!
//! **Prediction runs here** (`prediction.rs`), which is what makes the input feel
//! attached to the screen rather than to the network. Note the ordering in
//! `about_to_wait`: the snapshot is applied *first*, then the frame's own command
//! is predicted. The other order predicts against a state the server has already
//! superseded, and spends every frame correcting a guess it made from stale
//! ground.

use std::collections::HashMap;
use std::sync::mpsc::Receiver;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use winit::application::ApplicationHandler;
use winit::event::{
    DeviceEvent, DeviceId, ElementState, MouseButton, MouseScrollDelta, WindowEvent,
};
use winit::event_loop::{ActiveEventLoop, ControlFlow};
use winit::keyboard::{KeyCode, PhysicalKey};
use winit::window::{CursorGrabMode, Window, WindowId};

use hassault_native::animator::Squad;
use hassault_native::api::LintFinding;
use hassault_native::api::{
    HitboxSpec, ItemsResponse, NodeApi, TacticalSpec, ThrowPhysics, WeaponSpec,
};
use hassault_native::arc::{self, ThrowArc};
use hassault_native::audio::GameAudio;
use hassault_native::bodies;
use hassault_native::camera::{blast_trauma, damage_trauma, fire_trauma, Camera, Shake};
use hassault_native::console::{self, ClientCvars, Console, Definitions, Dispatch};
use hassault_native::damage::{DamageNumbers, Placed};
use hassault_native::decals::DecalPool;
use hassault_native::editor::{self, EditRequest, Editor, Selection};
use hassault_native::effects::EffectsPool;
use hassault_native::geometry::{build_world_mesh, MeshData};
use hassault_native::held;
use hassault_native::hud::{
    self, ConsoleView, Hud, HudView, OverlayVertex, RadarView, ScoreRow, UtilitySlot, UtilityView,
};
use hassault_native::interp::{PingTracker, SnapshotBuffer};
use hassault_native::items::ItemField;
use hassault_native::menu::{self, Action, Menu, Page};
use hassault_native::nades::{self, NadePool};
use hassault_native::net::{Incoming, MatchSocket};
use hassault_native::physics::{self, eye_height, MoveInput, JUMP_SPEED, MOVE_SPEED};
use hassault_native::prediction::Prediction;
use hassault_native::prop;
use hassault_native::protocol::{Command, Event, Fx, HitMarker, PlayerRow, SelfState};
use hassault_native::radar::{self, Blip, Run};
use hassault_native::renderer::{Renderer, Vertex, VolumeVertex, MODE_FLAT};
use hassault_native::reveal::Reveal;
use hassault_native::settings::{Crosshair, CrosshairStyle, Settings, SettingsWriter, FOV_RANGE};
use hassault_native::summary::{MatchTally, Summary, SummaryScreen};
use hassault_native::trace::kick_vector;
use hassault_native::training::TrainingRange;
use hassault_native::utility::GrenadeController;
use hassault_native::viewmodel::{self, Skin, WeaponViewModel};
use hassault_native::world::World;

/// Degrees of view rotation per unit of raw mouse movement, before the user's own
/// multiplier. Chosen to land near the browser client's feel at sensitivity 1.
const LOOK_SCALE: f32 = 0.08;

/// The largest `dt` ever sent, in seconds.
///
/// Clamped **before it goes on the wire**, not only on arrival. The server spends
/// client-supplied time from a replenishing budget, so one enormous frame — an
/// alt-tab, a GPU hitch, a breakpoint — would burn the reservoir and throttle the
/// next second of genuine movement. The browser client clamps for the same reason
/// and at the same value.
const MAX_DT: f32 = 0.05;

/// Input commands produced per second, independent of the frame rate.
///
/// **This client deliberately runs uncapped** (`Immediate` present mode, vsync
/// off by default), so `about_to_wait` iterates thousands of times a second on
/// any machine that can draw this scene quickly. One command per iteration was
/// wrong twice over, and both failures were silent:
///
/// 1. `dt` used to be clamped up to a 1 ms floor, so every iteration faster than
///    that claimed more simulated time than had actually passed. The server
///    spends client-supplied `dt` from a budget that replenishes at 1.1x real
///    time (`match.py`), so a client claiming 4x real time is throttled to a
///    quarter of what it predicted — its prediction runs permanently ahead and
///    every snapshot yanks it back. That reads as "I press forward and get
///    dragged back into the wall behind me", which is what it was reported as.
/// 2. `MatchSocket::flush` sends at most 64 commands per 33 ms window (the
///    server's `MAX_COMMANDS_PER_MESSAGE`), so above ~1940 iterations a second
///    the surplus was dropped *after* being predicted locally. The client had
///    already moved on commands the server would never see.
///
/// So input is produced on its own clock and the real elapsed time is
/// accumulated between commands, which makes the claimed time exactly the time
/// that passed no matter how fast the loop spins. 250 Hz is above any display
/// refresh rate — the local body is not what limits smoothness here, the mouse
/// is, and `look` still runs per event — and `INPUT_HZ * SEND_INTERVAL` is 8.25
/// commands per flush, comfortably inside the 64 a message may carry.
const INPUT_HZ: f32 = 250.0;

/// Seconds between input commands. See `INPUT_HZ`.
const INPUT_INTERVAL: f32 = 1.0 / INPUT_HZ;

/// Cubes of travel between footsteps at a run, from `noise.py`'s
/// `STRIDE_DISTANCE`. Your own steps are made locally — the server does not send
/// them back, because a footstep that arrives 50 ms late is not a footstep.
const STRIDE_DISTANCE: f32 = 4.2;

pub struct App {
    window: Option<Arc<Window>>,
    renderer: Option<Renderer>,
    /// The skinned operator and every drawn player's animation state.
    ///
    /// `None` when the asset failed to parse, which falls back to the old box
    /// bodies rather than to a match of invisible players.
    squad: Option<Squad>,
    /// Weapon props being parsed off the frame thread — see `prop::preload`.
    /// Drained in `sync_prop`, which is where the renderer is in hand.
    prop_loads: Receiver<(String, Result<prop::Prop, String>)>,
    /// The weapon `sync_prop` last fitted a prop to, and whether it succeeded.
    /// Two fields rather than an `Option<String>` because "fitted nothing for
    /// this weapon" and "have not tried this weapon yet" are different: the
    /// first stops retrying, the second must keep trying until the preloader
    /// catches up.
    prop_weapon: String,
    prop_fitted: bool,
    mesh: MeshData,
    world: World,
    /// The match, when there is one. **Train has none** — it is one player on a
    /// map, off the wire entirely, which is what the browser client's Train is
    /// too. A solo mode implemented as a room of one would not be solo: the
    /// server's join with no room id is join-*or*-create, so it would drop a
    /// learner into whatever match was already running on that map.
    socket: Option<MatchSocket>,
    /// Bots to field once the welcome names a room, `--mode=host` only.
    pending_bots: Option<(u32, String)>,
    /// Where we believe we are, ahead of the server. The camera reads this.
    prediction: Prediction,
    camera: Camera,
    /// The camera's decaying trauma. **Applied to a copy at draw time and
    /// nowhere else** — see `Camera::shaken`. Every angle this client puts on
    /// the wire, and every ray it builds a shot from, comes off `camera` above,
    /// so a shake written into that field would stop being a visual effect and
    /// start being recoil the server honours.
    shake: Shake,
    keys: Keys,
    sensitivity: f32,
    /// Whether the pointer is captured. Look is only applied while it is, so a
    /// click on the title bar does not spin the view.
    focused: bool,
    /// The newest snapshot's rows, verbatim. The **authoritative** roster:
    /// reconciliation and the scoreboard read it, and nothing draws it.
    players: Vec<PlayerRow>,
    /// Snapshot history, and the render clock derived from it.
    ///
    /// Bodies used to be drawn straight from `players`, which meant every other
    /// player in the match moved in 50 ms steps with the jitter landing on top —
    /// the rubber-banding this client was reported for. They are drawn from here
    /// instead, a tenth of a second in the past, between two snapshots that have
    /// both already arrived. See `interp.rs`.
    snapshots: SnapshotBuffer,
    /// This frame's interpolated bodies. Rebuilt each frame rather than each
    /// snapshot: the whole point is that it changes between snapshots.
    drawn: Vec<PlayerRow>,
    ping: PingTracker,
    self_id: String,
    joined: bool,
    /// Whether the last snapshot had us dead, so a respawn can be told from an
    /// ordinary correction.
    was_dead: bool,
    last_frame: Instant,
    /// Real seconds elapsed since the last input command was produced.
    ///
    /// The whole point of the accumulator: an iteration too short to be worth a
    /// command is not discarded and not rounded up, it is *banked*, so the sum
    /// of the `dt`s this client puts on the wire is the wall-clock time that
    /// actually passed. See `INPUT_HZ`.
    input_accum: f32,
    /// Frame timing, reported in the title bar — the number this client exists to
    /// move, so it should not need a profiler to read.
    frames: u32,
    fps_since: Instant,
    fps: f32,
    /// The `net.graph` step this client falls back to when nothing has set the
    /// CVar. **Not the live value** — that is `self.net_graph()`, which consults
    /// the console first. Keeping the private field as the *default* rather than
    /// as the state is what stops F3 and `net.graph 2` being two different
    /// numbers for the same thing, which is precisely the shape of divergence
    /// this client already had against the browser pane.
    net_graph_default: u32,
    /// The **throwing** half, which this client had none of: `nades` below drew
    /// everybody else's grenades while nothing here could produce one. What is
    /// readied, what is left, and the edge that puts `throw` on a command — see
    /// `utility.rs`.
    utility: GrenadeController,
    /// Grenades in the air and the clouds they leave. Drawn only — every
    /// position, radius and fuse in it is the server's. See `nades.rs`.
    /// The map building itself, over the first couple of seconds. See
    /// `reveal.rs` — a clock, not a load progress bar, exactly as the browser's
    /// call site runs it.
    reveal: Reveal,
    /// Seconds since this match started drawing, for effects that are a function
    /// of time rather than of state — the water's ripple. Its own field rather
    /// than borrowing the reveal's clock, which stops advancing once the map has
    /// finished arriving.
    elapsed: f32,
    nades: NadePool,
    /// Items on the map: placements from `MapInfo`, availability from the
    /// snapshot. A renderer only — the server decides every pickup.
    items: ItemField,
    /// What each item kind gives and how close you have to get, served rather
    /// than declared. Only Train reads it: in a match the server resolves every
    /// pickup and this client is told the result.
    item_table: ItemsResponse,
    /// Tracers, impacts and detonations. Like the nades, purely a renderer for
    /// what the server resolved — see `effects.rs`.
    effects: EffectsPool,
    /// Floating damage numbers, and the buffer they are projected into.
    ///
    /// The pool holds **world** anchors and is projected fresh every frame, so a
    /// number stays on the body it belongs to as both of you move. The buffer is
    /// reused rather than allocated per frame, like `volume_verts`.
    damage: DamageNumbers,
    damage_placed: Vec<Placed>,
    /// The whole-match totals the wire does not carry, and the card that shows
    /// them. See `summary.rs` — in particular why damage dealt is not among them.
    tally: MatchTally,
    summary: SummaryScreen,
    /// Kept buffers, like `overlay`: the render path does not allocate.
    volume_verts: Vec<VolumeVertex>,
    /// The served throw constants, or `None`. See `App::new`.
    throw_physics: Option<ThrowPhysics>,
    /// The arc drawn this frame, rebuilt while a grenade is up and empty
    /// otherwise. A field rather than a local so the render pass can read it
    /// without the simulation handing it across.
    throw_arc: Option<ThrowArc>,
    /// Whether a grenade was in hand on the previous frame. See the stow above.
    nade_held: bool,
    /// The spray index the last snapshot reported, so the camera can be kicked
    /// by the step between two of them. See `apply_match_recoil`.
    match_spray_index: i32,
    /// Simulated-clock instant of the last command that carried `fire`, so the
    /// trigger is rate-limited to the weapon's own interval.
    last_fire_sent_ms: f64,
    /// The world's memory of being shot at. Rides the translucent pass with the
    /// clouds and the water — see `decals.rs` for why that is the right one.
    decals: DecalPool,
    /// The map's floor plan, merged into runs **once** when the world loads.
    /// See `radar::floor_plan`: it is a property of the map, and rebuilding it
    /// per frame is how a minimap costs more than the map.
    radar_plan: Vec<Run>,
    /// The map designer, when this window is one. `None` is every other mode —
    /// edit mode is a thing a window becomes, not a flag every window carries.
    editor: Option<Editor>,
    /// Edits in flight. The node API is blocking HTTP, and an edit is a PATCH, a
    /// compile and two fetches; doing that on the frame thread is a visible
    /// hitch every time you let go of the mouse.
    edit_jobs: Option<EditJobs>,
    /// This frame's radar contacts. A kept buffer rather than a fresh `Vec`, for
    /// the same reason `overlay` is: the render path does not allocate.
    blips: Vec<Blip>,
    /// The developer console. See `console.rs` — the registry it completes and
    /// validates against is served by the node, never declared here.
    console: Console,
    /// Live values for `client`-flagged CVars: **overrides**, empty until
    /// something sets one, so a node that cannot serve the registry changes no
    /// behaviour at all. Every reader passes its own default as the fallback.
    cvars: ClientCvars,
    /// Unacknowledged commands, sampled when the title updates.
    pending: usize,
    map_name: String,
    /// The room this client is in, from the welcome. Empty in Train and while
    /// connecting — the console header draws nothing rather than an empty label,
    /// because `ROOM` with a blank after it reads as a room whose id is missing.
    room: String,
    /// Which modifier keys are held. See `WindowEvent::ModifiersChanged`.
    modifiers: winit::keyboard::ModifiersState,
    /// Sequence numbers for the offline simulation, which has no socket to stamp
    /// them. Only the ordering matters here — nothing acknowledges them.
    local_seq: u64,
    /// The served body every hit is resolved against, fetched once at startup
    /// beside the loadout and for the same reason. See `api::HitboxSpec` — the
    /// local copy this replaced had the crouched height wrong.
    hitbox: HitboxSpec,
    /// The served loadout. **Never hardcoded**: the crosshair opens by the
    /// weapon's own cone and the view model is built from the weapon's own id,
    /// and a local copy of either is wrong only for the weapon it is wrong for.
    weapons: Vec<WeaponSpec>,
    /// The equipped skin for each weapon, by weapon id. Fetched once at startup
    /// like the loadout: this process is launched per match, so there is no
    /// moment during one when the armoury could change under it.
    skins: HashMap<String, Skin>,
    /// Team scores from the last snapshot. Two numbers, and they are the only
    /// statement of who is winning anywhere in this client.
    scores: Vec<i32>,
    /// The private half of the last snapshot — health, ammo, the reload clock.
    /// `None` in Train, which has no server to have said any of it.
    you: Option<SelfState>,
    hud: Hud,
    viewmodel: WeaponViewModel,
    /// `None` when the machine has no output device, or refused one. The game
    /// runs silently rather than not at all.
    audio: Option<GameAudio>,
    /// Distance walked since the last footstep, in cubes.
    stride: f32,
    /// Whether we were on the ground last frame, so a takeoff and a landing can
    /// be told apart from being in either state.
    was_grounded: bool,
    /// Whether the body was in water last frame, so *entering* can be told from
    /// *being in*: only the crossing makes a sound.
    was_wet: bool,
    /// Zoom step: 0 unscoped, otherwise 1-based into the weapon's `zoom_levels`.
    ///
    /// **Client-owned**, like the view angles and for the same reason: it changes
    /// what you can see and how far the mouse moves you, both of which are
    /// already yours. The server reads it only to pick the shot's cone, and
    /// clamps it against the weapon actually held.
    scoped: i32,
    /// The unscoped field of view, so stepping the scope divides *this* rather
    /// than compounding on whatever the last magnification left behind.
    base_fov: f32,
    /// The pointer, in window pixels, whenever it is free.
    ///
    /// Meaningless while the pointer is grabbed — the look comes from a raw
    /// `DeviceEvent` delta then, and there is no cursor to have a position. Kept
    /// here rather than in the console because the menu already keeps its own in
    /// `Menu::hover`, and a third copy is a third thing to update.
    pointer: (f32, f32),
    /// Everything the pause menu edits, live. The authority for this session:
    /// the node is where it is *stored*, not where it is read from per frame.
    settings: Settings,
    /// Persists changes off the frame thread. See `settings::SettingsWriter`.
    writer: SettingsWriter,
    menu: Menu,
    /// Whether the window is currently fullscreen, so the menu's toggle can tell
    /// what it is toggling without asking the compositor.
    fullscreen: bool,
    /// The training range: the server's part, played locally, when there is no
    /// server. Empty and untouched in a match.
    range: TrainingRange,
    /// Seconds since this client started, for the range's fire rate. A local
    /// clock rather than `Instant::now()` at the call site, so the rate limit is
    /// testable without sleeping.
    local_clock: f32,
    /// When the range last fired, on that clock.
    last_fire: f32,
    /// Semi-automatic weapons need the trigger released between shots.
    trigger_used: bool,
    /// A weapon switch to ask for on the next command, or `-1`. In a match the
    /// server owns the slot, so this is a request and not a change.
    want_weapon: i32,
    /// A reload to ask for on the next command. Cleared as it is sent.
    want_reload: bool,
    /// Rebuilt each frame into buffers that are kept, so the render path does
    /// not allocate. The same reasoning as the renderer's own body buffer.
    overlay: Vec<OverlayVertex>,
    weapon_verts: Vec<Vertex>,
}

#[derive(Default)]
struct Keys {
    forward: bool,
    back: bool,
    left: bool,
    right: bool,
    jump: bool,
    crouch: bool,
    fire: bool,
    /// Whether the scoreboard is being held open. A held key rather than a
    /// toggle, matching the browser client and every shooter: you look at the
    /// scores *during* a lull, and a toggle is a scoreboard you leave up by
    /// accident and then die behind.
    scores: bool,
}

impl App {
    // Eight arguments, and every one of them is a *fetched* thing this client is
    // forbidden to hold a copy of — the world, its mesh, the socket, the
    // settings bag, the loadout, the skins, the hitbox. Bundling them into a
    // struct would only move the list somewhere the compiler checks less.
    #[allow(clippy::too_many_arguments)]
    /// `socket: None` is Train: a world, a body, and nobody else in it.
    pub fn new(
        world: World,
        mesh: MeshData,
        socket: Option<MatchSocket>,
        settings: Settings,
        writer: SettingsWriter,
        weapons: Vec<WeaponSpec>,
        // The grenades, in slot order. Empty is a node that served none, and is
        // a throw key that does nothing rather than a client-side guess at what
        // the four are.
        tacticals: Vec<TacticalSpec>,
        // The constants a throw is integrated with, for the trajectory preview.
        // `None` is a node too old to serve them, and draws **no preview** — a
        // trajectory integrated with numbers this client invented would be an
        // aiming aid confidently pointing somewhere the grenade will not go.
        throw_physics: Option<ThrowPhysics>,
        skins: HashMap<String, Skin>,
        hitbox: HitboxSpec,
        // What each item kind gives, and the reach. Another fetched thing this
        // client is forbidden to hold a copy of: Train resolves its own
        // ammunition pickups, and a local table would be a range where items
        // behave differently from a match.
        item_table: ItemsResponse,
        definitions: Definitions,
        radar_plan: Vec<Run>,
    ) -> App {
        let map_name = world.info.name.clone();
        // Read off the bag before it is moved into the struct. The camera is
        // constructed before the settings are, so a saved FOV taken from
        // `Camera::default()` would sit unapplied until the player opened the
        // menu and nudged the row.
        let base_fov = settings.video.fov;
        // Read before `world` is moved into the struct below.
        let world_items = world.info.items.clone();
        let mut app = App {
            // Started here, before a window exists: the parse needs no GPU, and
            // the sooner it begins the likelier every prop is resident before
            // the first weapon switch.
            prop_loads: prop::preload(),
            prop_weapon: String::new(),
            prop_fitted: false,
            window: None,
            renderer: None,
            mesh,
            world,
            socket,
            pending_bots: None,
            prediction: Prediction::default(),
            camera: Camera::default(),
            shake: Shake::default(),
            keys: Keys::default(),
            sensitivity: settings.sensitivity,
            focused: false,
            players: Vec::new(),
            snapshots: SnapshotBuffer::new(),
            drawn: Vec::new(),
            ping: PingTracker::default(),
            self_id: String::new(),
            joined: false,
            was_dead: false,
            // Parsed up front rather than on the first body: decoding fourteen
            // textures mid-match is a visible hitch, and the first body tends to
            // appear at the least convenient moment for one.
            squad: match Squad::load() {
                Ok(squad) => Some(squad),
                Err(error) => {
                    eprintln!("hassault: the operator model could not be loaded: {error}");
                    None
                }
            },
            last_frame: Instant::now(),
            input_accum: 0.0,
            frames: 0,
            fps_since: Instant::now(),
            fps: 0.0,
            net_graph_default: 1,
            reveal: Reveal::default(),
            utility: GrenadeController::new(tacticals),
            elapsed: 0.0,
            nades: NadePool::default(),
            // Placed from the map rather than from the welcome: the placements
            // are a property of the map, so one source serves a live room and a
            // solo range alike. Availability is the per-match half, and rides in
            // the snapshot.
            items: ItemField::place(&world_items),
            item_table,

            effects: EffectsPool::default(),
            damage: DamageNumbers::default(),
            damage_placed: Vec::new(),
            tally: MatchTally::default(),
            summary: SummaryScreen::default(),
            volume_verts: Vec::new(),
            throw_physics,
            throw_arc: None,
            nade_held: false,
            match_spray_index: 0,
            last_fire_sent_ms: f64::NEG_INFINITY,
            decals: DecalPool::default(),
            radar_plan,
            editor: None,
            edit_jobs: None,
            blips: Vec::new(),
            console: Console::default(),
            cvars: ClientCvars::default(),
            pending: 0,
            map_name,
            room: String::new(),
            modifiers: winit::keyboard::ModifiersState::empty(),
            local_seq: 0,
            weapons,
            skins,
            hitbox,
            scores: Vec::new(),
            you: None,
            hud: Hud::default(),
            viewmodel: WeaponViewModel::default(),
            settings,
            writer,
            menu: Menu::default(),
            fullscreen: false,
            range: TrainingRange::default(),
            local_clock: 0.0,
            last_fire: -999.0,
            trigger_used: false,
            want_weapon: -1,
            want_reload: false,
            overlay: Vec::new(),
            weapon_verts: Vec::new(),
            audio: GameAudio::open(),
            stride: 0.0,
            was_grounded: true,
            // Corrected on the first frame before anything can hear it:
            // `footsteps` writes this outside its `audible` guard, so a body that
            // starts in water has already been recorded as wet by the time the
            // match is joined.
            was_wet: false,
            scoped: 0,
            // From the settings, never from `Camera::default()`: the camera is
            // built before the bag is read, so taking it from there would leave
            // a saved FOV applied to nothing until the player opened the menu
            // and nudged it.
            base_fov,
            pointer: (0.0, 0.0),
        };
        if app.audio.is_none() {
            // Said out loud: a game that is silently silent reads as a game whose
            // sound is broken, and the noise mechanic is a mechanic.
            eprintln!("hassault: no audio output device; playing silently");
        }
        // Aimed at the map it will build. `extent * 1.05` is the browser's:
        // a radius slightly larger than the world, so the far corner is not
        // still mid-rise when the clock runs out.
        let extent = app.world.ssize as f32 * 0.5;
        app.reveal
            .fit([extent, extent], extent * 1.05, (extent * 0.6).max(1.0));
        app.console.set_definitions(definitions);
        if app.socket.is_none() {
            app.place_offline();
        }
        app
    }

    /// The weapon currently in hand, if the loadout names one.
    ///
    /// In Train there is no `you` and so no weapon slot: the server is what
    /// decides what you are holding. Rather than an empty hand — which reads as
    /// the view model having failed — training holds the rifle, the same weapon
    /// the browser's Train hands you.
    fn held(&self) -> Option<&WeaponSpec> {
        match &self.you {
            Some(you) => self.weapons.get(you.weapon.max(0) as usize),
            None => self
                .weapons
                .iter()
                .find(|w| w.id == "assault")
                .or_else(|| self.weapons.first()),
        }
    }

    /// Step the scope: none → each magnification in turn → none.
    ///
    /// A cycle rather than a hold, because the zoom levels are discrete: holding
    /// a button for 2× and a *different* gesture for 4× is two controls for one
    /// axis. A weapon with no scope ignores the click entirely rather than
    /// consuming it, so the button stays free to mean something else later.
    fn cycle_scope(&mut self) {
        let levels = self.held().map(|w| w.zoom_levels.len()).unwrap_or(0) as i32;
        if levels == 0 {
            return;
        }
        self.scoped = if self.scoped >= levels {
            0
        } else {
            self.scoped + 1
        };
        self.apply_zoom();
    }

    /// Apply one raw mouse delta.
    ///
    /// A method rather than three lines inside the event handler, so the
    /// sensitivity rule can be tested without a winit event loop — the division
    /// by the magnification is the half of the zoom that is invisible when it is
    /// missing, and a test that poked the camera directly would not exercise it.
    fn look(&mut self, dx: f32, dy: f32) {
        // Divided by the same magnification the field of view is. A zoom that
        // narrowed the view without slowing the mouse would make a given hand
        // movement sweep four times as much of the world at 4× — an aim that is
        // wrong only while scoped.
        let sensitivity = LOOK_SCALE * self.sensitivity() / self.magnification();
        self.camera.apply_look(dx, dy, sensitivity);
    }

    /// Current magnification: 1 when unscoped, so callers can divide by it blind.
    fn magnification(&self) -> f32 {
        if self.scoped <= 0 {
            return 1.0;
        }
        self.held()
            .and_then(|w| w.zoom_levels.get(self.scoped as usize - 1).copied())
            .unwrap_or(1.0)
    }

    /// Drop the scope — a death, a weapon that does not have one, a released
    /// pointer. Anything that would leave you at 4× with no memory of having
    /// scoped, which reads as the mouse having broken.
    fn unscope(&mut self) {
        if self.scoped != 0 {
            self.scoped = 0;
            self.apply_zoom();
        }
    }

    /// **The zoom is a narrower field of view, not an enlargement**, which is why
    /// it divides the FOV rather than scaling anything.
    ///
    /// The mouse is divided by the same number in `device_event`. Zooming without
    /// it means the same hand movement sweeps four times as much of the world at
    /// 4×, which is precisely the aim being wrong only while scoped.
    fn apply_zoom(&mut self) {
        self.camera.fov = self.base_fov() / self.magnification();
    }

    /// The unscoped FOV, with any console override folded in.
    ///
    /// The same precedence the crosshair and `draw.hitboxes` use, and for the
    /// same reason: a CVar assignment is a statement about *now*, the setting is
    /// a preference, and reading them through one accessor is what stops the
    /// console and the video menu becoming two FOVs that disagree. Clamped to
    /// the setting's range rather than trusted — `draw.fov` is declared over
    /// exactly this range in `console.py`, but a client that believed whatever
    /// arrived would render a fish-eye on a typo.
    fn base_fov(&self) -> f32 {
        self.cvars
            .number("draw.fov")
            .map(|v| v as f32)
            .unwrap_or(self.base_fov)
            .clamp(FOV_RANGE.0, FOV_RANGE.1)
    }

    /// The roster, ranked.
    ///
    /// Most kills first, then fewest deaths — the ordering the browser client
    /// uses, because two clients ranking the same match differently is a thing
    /// people notice and nobody can explain.
    ///
    /// Read from `players` (the authoritative roster) and never from `drawn`:
    /// the interpolated copy is a hundred milliseconds old and, more to the
    /// point, deliberately excludes us.
    fn score_rows(&self) -> Vec<ScoreRow> {
        let mut rows: Vec<ScoreRow> = self
            .players
            .iter()
            .map(|p| ScoreRow {
                name: if p.name.is_empty() {
                    p.id.clone()
                } else {
                    p.name.clone()
                },
                kills: p.kills,
                deaths: p.deaths,
                team: p.team,
                bot: p.bot,
                you: p.id == self.self_id,
            })
            .collect();
        rows.sort_by(|a, b| b.kills.cmp(&a.kills).then(a.deaths.cmp(&b.deaths)));
        rows
    }

    /// This process's clock in milliseconds.
    ///
    /// The *base* is arbitrary — `SnapshotBuffer` only ever differences it
    /// against the server's stamp — but it must not step, which is exactly what
    /// a wall clock does when NTP corrects it. A jump there would move every
    /// interpolated body on screen at once, and would put a `viewT` on the wire
    /// pointing at a moment the server's position history never covered.
    fn clock_ms() -> f64 {
        static START: OnceLock<Instant> = OnceLock::new();
        START.get_or_init(Instant::now).elapsed().as_secs_f64() * 1000.0
    }

    /// Horizontal speed, in cubes per second. Vertical is deliberately excluded:
    /// the number is about the movement model's ground speed cap, and falling
    /// would otherwise read as a chained jump.
    fn ground_speed(&self) -> f32 {
        let p = &self.prediction.state;
        (p.vel_x * p.vel_x + p.vel_y * p.vel_y).sqrt()
    }

    /// Sleep out whatever is left of this frame's slice under an FPS cap.
    ///
    /// **Before the frame clock is read, deliberately**, so the sleep is part of
    /// `dt` rather than something that happened between two frames. Sleeping
    /// after the clock would leave every step integrating a `dt` that excluded
    /// the wait, which is the same class of bug as ticking a player by wall clock
    /// — the simulation would run at the uncapped rate and the screen at the
    /// capped one.
    ///
    /// This is **not** vsync and does not pretend to be: it adds no queued frame
    /// and it does not align to the display, so tearing is exactly as visible as
    /// it was. What it buys is a machine that is not pinned at 100% to draw
    /// frames nobody's monitor will show — see `Video::fps_limit`.
    ///
    /// `spin_sleep` is not used and the accuracy is not chased. The OS timer's
    /// granularity means a 240 cap lands somewhere near 240, and a cap that is
    /// approximate is doing its whole job; a busy-wait that hit it exactly would
    /// burn the core the cap exists to leave alone.
    fn wait_for_frame(&mut self) {
        let limit = self.settings.video.fps_limit;
        if limit == 0 {
            return;
        }
        let budget = Duration::from_secs_f32(1.0 / limit as f32);
        let spent = self.last_frame.elapsed();
        if let Some(left) = budget.checked_sub(spent) {
            std::thread::sleep(left);
        }
    }

    /// Advance the HUD and the weapon in the hands by one frame.
    fn animate(&mut self, dt: f32) {
        self.hud.update(dt, self.keys.scores);
        // Drawn-position easing only. Nothing here simulates a grenade — the
        // arc, the bounce and the fuse are all the server's, and a second
        // implementation of the bounce would exist only to disagree with the
        // first.
        self.reveal.advance(dt);
        self.elapsed += dt;
        self.nades.update(dt);
        self.items.update(dt);
        self.effects.update(dt);
        self.decals.update(dt);
        self.damage.update(dt);
        self.shake.update(dt);
        // Advanced here rather than at draw time so every player's clip runs on
        // the same clock the weapon and the HUD do. The poses are uploaded later
        // in the frame — see `poses()`.
        {
            let dummies;
            let rows: &[PlayerRow] = if self.socket.is_none() {
                dummies = self.range.rows();
                &dummies
            } else {
                &self.drawn
            };
            if let Some(squad) = self.squad.as_mut() {
                squad.update(dt, rows, &self.self_id);
            }
        }
        let weapon = self.held().map(|w| w.id.clone()).unwrap_or_default();
        if weapon != self.viewmodel.weapon() {
            // A weapon swap drops the scope: the step is an index into *this*
            // weapon's levels, and carrying it across would put you at a
            // magnification the gun in your hands does not have.
            self.unscope();
        }
        // Also clamped every frame, because the loadout is fetched once and the
        // slot comes from the server: a swap we did not initiate is still a swap.
        let levels = self.held().map(|w| w.zoom_levels.len()).unwrap_or(0) as i32;
        if self.scoped > levels {
            self.unscope();
        }
        if self.you.as_ref().is_some_and(|y| !y.alive) {
            self.unscope();
        }
        self.viewmodel.set_weapon(&weapon, self.skins.get(&weapon));
        self.sync_prop(&weapon);
        // **What is in your hand this frame.** The weapon is stowed while a
        // grenade is up and comes back the instant it leaves — a throw is one
        // action, not a mode you have to leave.
        //
        // An edge, not a level: `holster` starts an animation, and calling it
        // every frame would restart the stow sixty times a second and leave the
        // weapon permanently down.
        let holding_nade = self.utility.equipped();
        // **The predicted arc.** Drawn from the *locally predicted* velocity,
        // not from the last snapshot's: the whole reason it exists is to make
        // `THROW_INHERIT` visible — running and jumping feed the throw — and a
        // velocity half a round trip old would lag exactly the movement it is
        // meant to be showing.
        self.throw_arc = match (holding_nade, self.throw_physics.as_ref()) {
            (true, Some(physics)) => {
                let state = &self.prediction.state;
                let (yaw, pitch) = self.view_angles();
                let eye_z = state.z + physics::eye_offset(state);
                Some(arc::simulate_throw(
                    &self.world,
                    arc::throw_origin(state.x, state.y, eye_z, yaw, pitch, physics),
                    // The full throw, not the lob: nothing is held down while
                    // aiming, so the arc shows the overhand and the underhand is
                    // read off it as "much shorter than that".
                    arc::throw_velocity(
                        yaw,
                        pitch,
                        false,
                        [state.vel_x, state.vel_y, state.vel_z],
                        physics,
                    ),
                    physics,
                    arc::ARC_PREVIEW_SECONDS,
                ))
            }
            _ => None,
        };
        if holding_nade != self.nade_held {
            self.nade_held = holding_nade;
            if holding_nade {
                self.viewmodel.holster();
            }
        }
        // Alive unless the server says otherwise. **Not** gated on the pointer
        // being captured: releasing it here is not a menu, it is a mouse you can
        // move — the world is still drawn behind it, so a world with no weapon
        // and no HUD in it reads as the client having half-loaded. The browser
        // client draws its weapon on `alive` alone for the same reason.
        let alive = self.you.as_ref().map(|y| y.alive).unwrap_or(true);
        let frame = viewmodel::Frame {
            speed: self.ground_speed(),
            on_ground: self.prediction.state.on_ground,
            reloading: self.you.as_ref().is_some_and(|y| y.reloading),
            yaw: self.camera.yaw.to_radians(),
            pitch: self.camera.pitch.to_radians(),
            visible: self.joined && alive,
            move_speed: MOVE_SPEED,
            // `camera.fov`, not `base_fov`: line 610 divides it by the scope's
            // magnification every frame, and the flash cap has to see the FOV
            // the player is actually looking through — otherwise it is right
            // hipfiring and four times too generous at 4x.
            fov: self.camera.fov.to_radians(),
            // For the landing dip. A *duration*, which is also what the server
            // sends (`you.move.since_landed`) and for the same reason: the two
            // simulated clocks are unrelated, so a timestamp from one measured
            // against the other means nothing.
            since_landed: (self.prediction.state.t - self.prediction.state.landed_at).max(0.0),
            // The reload's *progress*, from the two served numbers: how long it
            // takes (`reloadTime`, on the weapon) and how much is left
            // (`reloadIn`, on the snapshot). The range fills both as well, so a
            // reload dips identically in Train and in a match with no second
            // code path to keep in step.
            //
            // `None` when `reloadTime` is zero — an older server, or a spec that
            // has not arrived — because there is then nothing to measure
            // against, and a progress of zero would hold the weapon down for the
            // whole reload. See `Frame::reload_progress`.
            reload_progress: self.you.as_ref().filter(|y| y.reloading).and_then(|y| {
                let total = self.held().map(|w| w.reload_time).unwrap_or(0.0);
                (total > 0.0).then(|| (1.0 - y.reload_in / total).clamp(0.0, 1.0))
            }),
        };
        self.viewmodel.update(dt, &frame);
        // **After** `update`, not inside `sync_prop`. `prop_model` reads the
        // pose and the visibility that `update` has just written, so pushing it
        // from `sync_prop` (which runs before, because it has to fit the prop
        // first) handed the renderer last frame's transform — and, now that the
        // pose is gated on `visible`, last frame's answer to "is there a weapon
        // at all".
        if let Some(renderer) = self.renderer.as_mut() {
            renderer.set_prop_model(self.viewmodel.prop_model());
        }
        self.footsteps(dt);
    }

    /// Our own movement noises, made locally.
    ///
    /// The server deliberately does not send these back — they need no round
    /// trip, and a footstep that arrives 50 ms after the step does not sound like
    /// one. The rules are the server's own (`noise.py`): a stride only
    /// accumulates on the ground and **never while crouched**, which is what
    /// makes AC's crouch speed penalty a trade rather than a tax.
    fn footsteps(&mut self, dt: f32) {
        let state = &self.prediction.state;
        let grounded = state.on_ground;
        let crouched = state.crouch > 0.5;
        let speed = self.ground_speed();
        let fall_speed = state.fall_speed;
        let rising = state.vel_z > 0.0;
        let wet = physics::in_water(&self.world, state);
        let audible = self.joined;

        if grounded && !crouched {
            self.stride += speed * dt;
            if self.stride >= STRIDE_DISTANCE {
                self.stride = 0.0;
                if audible {
                    self.play_own("step", 0.45, false);
                }
            }
        } else {
            // Reset rather than bank it, or a player could crouch-walk a long way
            // and pay for all of it with one loud step on standing up.
            self.stride = 0.0;
        }

        if audible {
            if self.was_grounded && !grounded && rising {
                self.play_own("jump", 0.5, false);
            }
            if fall_speed > 0.0 {
                // Louder the harder the landing, which is the audible half of the
                // fall-damage rule: you hear that a drop was expensive.
                let volume = (0.35 + fall_speed / (JUMP_SPEED * 2.0)).min(1.0);
                self.play_own("land", volume, false);
            }
            // Breaking the surface, in either direction — getting out is as loud
            // as getting in. Synthesised locally like every other sound we make:
            // the server does not send our own noises back, because one arriving
            // half a round trip late does not sound like the thing that made it.
            if wet != self.was_wet {
                self.play_own("splash", 0.8, false);
            }
        }
        // Outside the `audible` guard: the flag tracks where the body *is*, and
        // letting it drift while unjoined would fire a splash on the first frame
        // of a match for a player who was already standing in water.
        self.was_wet = wet;
        self.was_grounded = grounded;
    }

    /// Say goodbye and stop.
    ///
    /// The `leave` is not optional politeness: without it the room holds a
    /// player who is not there until the socket times out, and everyone still
    /// in it is shooting at a body that no longer has anybody behind it.
    fn leave_and_exit(&mut self, event_loop: &ActiveEventLoop) {
        if let Some(socket) = &self.socket {
            let _ = socket.leave();
        }
        event_loop.exit();
    }

    /// The card's contents, from what this client already knows.
    ///
    /// Assembled here rather than held as state so it cannot go stale: every
    /// figure is read at the moment the card is drawn. `won` and `mvp` use
    /// `MatchRoom.result_for`'s own definitions — nobody outscored you, and
    /// nobody equalled you — because a second definition of winning is how two
    /// cards for one match come to disagree.
    fn build_summary(&self) -> Summary {
        let you = self.you.as_ref();
        let mine = you.map(|y| y.kills).unwrap_or(0);
        // Bots included. Losing to one is losing, and a card that quietly
        // excluded them would be flattering rather than true.
        let best = self
            .players
            .iter()
            .filter(|p| p.id != self.self_id)
            .map(|p| p.kills)
            .max()
            .unwrap_or(-1);
        let mut summary = Summary {
            map: self.world.info.name.clone(),
            name: self
                .players
                .iter()
                .find(|p| p.id == self.self_id)
                .map(|p| p.name.clone())
                .unwrap_or_default(),
            kills: mine,
            deaths: you.map(|y| y.deaths).unwrap_or(0),
            tally: self.tally,
            opponents: self.players.iter().filter(|p| p.id != self.self_id).count(),
            won: false,
            mvp: false,
            recordable: false,
        };
        // Asked of the assembled card rather than recomputed from these locals,
        // so the predicate has one home — and `won`/`mvp` are gated on it for the
        // reason `result_for` gates its own: alone in a room `best` is `-1`, so
        // `0 >= -1` used to congratulate a player for quitting.
        summary.recordable = summary.is_recordable();
        summary.won = summary.recordable && mine >= best;
        summary.mvp = summary.recordable && mine > best;
        summary
    }

    /// File one tick's hitmarkers as floating damage numbers.
    ///
    /// The anchor is looked up in **the roster the renderer is drawing this
    /// frame** — the interpolated one in a match, the dummies on the range — so
    /// the number lands on the body as it is *seen* rather than where the last
    /// snapshot put it. A victim who is not in it (a kill through smoke, or a
    /// long shot that outlived its target in the buffer) falls back to the
    /// crosshair rather than being dropped.
    fn note_hits(&mut self, hits: &[HitMarker]) {
        if hits.is_empty() {
            return;
        }
        let dummies;
        let rows: &[PlayerRow] = if self.socket.is_none() {
            dummies = self.range.rows();
            &dummies
        } else {
            &self.drawn
        };
        self.damage.push(hits, |id| {
            rows.iter().find(|p| p.id == id).map(|p| [p.x, p.y, p.z])
        });
        // The same list, counted rather than drawn. One call site for both, so a
        // path that files a hitmarker can never file it into only one of them.
        self.tally.note(hits);
    }

    /// Sound a kill we made, once per tick however many hitmarkers carried one.
    ///
    /// A burst that kills fills the whole magazine's worth of markers with the
    /// same event; playing one voice per marker turns a shotgun kill into eight
    /// overlapping chords, which is not louder so much as broken.
    fn confirm_kill(&self, hits: &[HitMarker]) {
        if hits.iter().any(|h| h.killed) {
            self.play_own("kill", 0.7, false);
        }
    }

    /// One of our own noises, dead centre. `weapon` gives it the voice of the gun
    /// in our hands rather than the generic shot.
    fn play_own(&self, kind: &str, volume: f32, weapon: bool) {
        let Some(audio) = &self.audio else { return };
        audio.own(kind, volume, if weapon { self.held() } else { None });
    }

    /// Bots to add once we are in a room. Ignored without a socket, which is not
    /// a case to guard against — the launcher only sends a count with `host`.
    // -------------------------------------------------------------------------
    // Edit mode
    // -------------------------------------------------------------------------

    /// Turn this window into the map designer.
    ///
    /// A setter rather than a thirteenth constructor argument, the way
    /// `queue_bots` is: edit mode is a thing a window becomes, not a thing every
    /// window has to be told it is not.
    pub fn enter_edit_mode(
        &mut self,
        server: &str,
        draft: hassault_native::api::DraftInfo,
        owners: Vec<u16>,
    ) {
        let mut editor = Editor::new(draft.id.clone(), &self.world.info);
        editor.owners = owners;
        editor.brush_rects = brush_rects(&draft.doc);
        editor.brush_ops = brush_ops(&draft.doc);
        editor.problem_cells = problem_cells(&draft.lint);
        editor.status = describe_lint(&draft.lint);
        self.camera.yaw = editor::START_YAW;
        self.camera.pitch = editor::START_PITCH;
        self.camera.x = editor.camera.x;
        self.camera.y = editor.camera.y;
        self.camera.z = editor.camera.z;
        self.editor = Some(editor);
        self.edit_jobs = Some(spawn_edit_worker(server, draft.id));
    }

    pub fn editing(&self) -> bool {
        self.editor.is_some()
    }

    /// Move the free camera, and work out what the crosshair is on.
    ///
    /// Replaces the whole prediction/reconciliation path for this frame. There
    /// is no body here and nothing to reconcile against: edit mode has no socket.
    fn fly_and_pick(&mut self, dt: f32) {
        let Some(editor) = self.editor.as_mut() else {
            return;
        };
        let yaw = self.camera.yaw.to_radians();
        let pitch = self.camera.pitch.to_radians();
        editor.camera.fly(
            yaw,
            pitch,
            axis(self.keys.forward, self.keys.back),
            axis(self.keys.right, self.keys.left),
            axis(self.keys.jump, self.keys.crouch),
            self.keys.scores,
            dt.clamp(0.0, MAX_DT),
        );
        self.camera.x = editor.camera.x;
        self.camera.y = editor.camera.y;
        self.camera.z = editor.camera.z;

        editor.hover = editor::pick(
            &self.world,
            [editor.camera.x, editor.camera.y, editor.camera.z],
            hassault_native::trace::aim_vector(yaw, pitch),
        );
        // A drag follows the crosshair, so the ghost tracks the wall you are
        // pointing at rather than the one you started on.
        if let Some(hit) = editor.hover {
            if editor.drag.is_some() {
                editor.update_drag(&hit);
            }
        }
    }

    /// Hand a finished gesture to the worker. Never blocks the frame: the node
    /// API is blocking HTTP, and a compile plus a re-fetch is tens of
    /// milliseconds — enough to be a visible hitch every time you let go of the
    /// mouse.
    fn submit_edit(&mut self, request: EditRequest) {
        let Some(editor) = self.editor.as_mut() else {
            return;
        };
        let body = match &request {
            EditRequest::Reshape { index, rect } => serde_json::json!({
                "op": "brush.update", "index": index, "patch": { "rect": rect }
            }),
            EditRequest::Height {
                index,
                field,
                value,
            } => serde_json::json!({
                "op": "brush.update", "index": index, "patch": { *field: value }
            }),
            EditRequest::Add { op, rect } => serde_json::json!({
                "op": "brush.add", "brush": { "op": op, "rect": rect }
            }),
            EditRequest::Place { kind, cell } => serde_json::json!({
                "op": "ent.add", "entity": { "type": kind, "x": cell.0, "y": cell.1 }
            }),
            EditRequest::Remove { index } => serde_json::json!({
                "op": "brush.remove", "index": index
            }),
        };
        editor.status = "working…".to_string();
        if let Some(jobs) = &self.edit_jobs {
            let _ = jobs.send.send(EditJob::Patch(body));
        }
    }

    /// Take whatever the worker finished, and rebuild the world from it.
    ///
    /// The rebuild is a **full** `build_world_mesh`, deliberately. It is a flat
    /// loop over the grid and finishes in single-digit milliseconds here, so
    /// there is nothing to gain from a dirty-region scheme that could disagree
    /// with the mesh a fresh load produces — and a mesh that is subtly wrong
    /// only after an edit is the worst kind of wrong to debug.
    fn drain_edits(&mut self) {
        let Some(jobs) = &self.edit_jobs else {
            return;
        };
        while let Ok(outcome) = jobs.recv.try_recv() {
            match outcome {
                EditOutcome::Applied {
                    doc,
                    lint,
                    world,
                    owners,
                } => {
                    self.world = *world;
                    // A rebuilt world is a different world: marks recorded
                    // against the old geometry would hang in mid-air, or sit
                    // inside a wall that was not there when they were made.
                    self.decals.clear();
                    self.mesh = build_world_mesh(&self.world);
                    self.radar_plan = hassault_native::radar::floor_plan(&self.world);
                    if let Some(renderer) = &mut self.renderer {
                        renderer.set_world(&self.mesh);
                    }
                    if let Some(editor) = self.editor.as_mut() {
                        editor.brush_rects = brush_rects(&doc);
                        editor.brush_ops = brush_ops(&doc);
                        editor.entities = self.world.info.entities.clone();
                        editor.owners = owners;
                        editor.problem_cells = problem_cells(&lint);
                        editor.status = describe_lint(&lint);
                    }
                }
                EditOutcome::Failed(message) => {
                    if let Some(editor) = self.editor.as_mut() {
                        // Surfaced, not swallowed. A refused edit leaves the
                        // document exactly as it was, so the map on screen is
                        // still correct — but an editor that silently ignored
                        // a gesture would be indistinguishable from one that
                        // had stopped working.
                        editor.status = message;
                    }
                }
            }
        }
    }

    /// Send a job that is not a gesture — undo, redo, save.
    fn edit_job(&mut self, job: EditJob) {
        if let Some(editor) = self.editor.as_mut() {
            editor.status = "working…".to_string();
        }
        if let Some(jobs) = &self.edit_jobs {
            let _ = jobs.send.send(job);
        }
    }

    /// Place the current entity kind at the crosshair.
    ///
    /// The **cell only** — no height. `mapsource` resolves an entity's `z` from
    /// the floor actually built underneath it, which is what stops a spawn moved
    /// in the source from silently keeping an old height. A client that sent the
    /// height it happened to be looking at would be re-introducing exactly the
    /// mapper's-eye problem that put all 1741 official spawns in mid-air.
    fn place_selected_entity(&mut self) {
        let Some(editor) = self.editor.as_ref() else {
            return;
        };
        let Some(hit) = editor.hover else {
            return;
        };
        let kind = editor.place_kind.clone();
        self.submit_edit(EditRequest::Place {
            kind,
            cell: hit.cell,
        });
    }

    /// Raise or lower the selected brush's floor.
    ///
    /// Only a `room` has one — a `solid` stores nothing but `wtex`, and asking
    /// the node to put a floor on one would be refused. So this reads the op off
    /// the document rather than assuming, and says why when it declines.
    fn nudge_height(&mut self, delta: i32) {
        // Everything needed is copied out before anything is written back: the
        // decline below writes to `self.editor`, and a borrow still open across
        // it would not compile.
        let (index, rect, op) = {
            let Some(editor) = self.editor.as_ref() else {
                return;
            };
            let Selection::Brush(index) = editor.selection else {
                return;
            };
            let Some(rect) = editor.brush_rects.get(index).copied() else {
                return;
            };
            (index, rect, editor.brush_ops.get(index).cloned())
        };
        // Which field the height lives in depends on the op, and one op has no
        // height at all. Declining here rather than sending it is the difference
        // between "this brush has no floor" and a 422 the mapper has to read
        // backwards.
        let field = match op.as_deref() {
            Some("room") => "floor",
            Some("stairs") => "from",
            other => {
                if let Some(editor) = self.editor.as_mut() {
                    editor.status = format!(
                        "a {} brush has no floor to raise — it stores only its texture",
                        other.unwrap_or("solid")
                    );
                }
                return;
            }
        };
        // The current height comes from the world rather than the document,
        // because a brush may omit the field entirely and mean the default.
        let centre = (rect[0] + rect[2] / 2, rect[1] + rect[3] / 2);
        let current = self.world.floor_at(centre.0, centre.1).round() as i32;
        self.submit_edit(EditRequest::Height {
            index,
            field,
            value: (current + delta).clamp(-128, 127),
        });
    }

    pub fn queue_bots(&mut self, count: u32, skill: String) {
        if count > 0 {
            self.pending_bots = Some((count, skill));
        }
    }

    /// Stand the player on a spawn point, with no server involved.
    ///
    /// The same `spawn_at` the conformance fixture pins, so Train starts you
    /// exactly where a match would have: the height comes from the world under
    /// the spawn, never from the entity's own `z`, which is the mapper's eye and
    /// scatters up to twenty-two cubes above the floor.
    fn place_offline(&mut self) {
        let spawn = self.world.spawns(None).first().map(|e| physics::Spawn {
            x: e.x,
            y: e.y,
            z: e.z,
            yaw: e.yaw,
        });
        let Some(spawn) = spawn else {
            eprintln!("hassault: this map has no spawn points; nothing to train on");
            return;
        };
        let placed = physics::spawn_at(&self.world, &spawn);
        // The camera works in degrees and the simulation in radians; `reset` takes
        // radians, so the one conversion happens here rather than twice.
        self.camera.yaw = placed.yaw.to_degrees();
        self.camera.pitch = 0.0;
        self.prediction.reset(
            placed.x,
            placed.y,
            placed.z,
            placed.yaw,
            self.camera.pitch.to_radians(),
        );
        self.joined = true;
        self.follow_prediction();

        // Stand the range up around wherever we landed. The rifle rather than
        // slot 0 (the knife), matching the browser's Train: the mode is for
        // learning the movement and the shoot-jump, and neither is reachable
        // with a blade.
        let slot = self
            .weapons
            .iter()
            .position(|w| w.id == "assault")
            .unwrap_or(0);
        let weapons = self.weapons.clone();
        self.range.set_weapons(&weapons, slot);
        self.range.place(&self.world, placed.x, placed.y);
        // The map's own items, exactly where a match would put them: the server
        // resolved these heights, so the range never has to. An empty table is a
        // node too old to serve one — the items still *draw*, they just give
        // nothing, which is a range missing a convenience rather than a lie.
        let items = self.world.info.items.clone();
        self.range
            .place_items(&items, &self.item_table.kinds, self.item_table.reach);
        if !self.range.populated() {
            // One spawn point on the map, and we are standing on it. Not fatal —
            // the movement is still the point — but silence here would read as
            // the dummies having failed to load.
            eprintln!("hassault: no room for training dummies on this map");
        }
        self.you = Some(self.range.self_state());
    }

    /// Move the camera to wherever the prediction currently believes we are.
    ///
    /// The eye, not the feet: `z` on the wire is where you stand, and the offset
    /// between them shrinks as you crouch. `eye_height` is the physics module's
    /// own function rather than a copy of the arithmetic, because a shot leaves
    /// from the same point the camera sits at — two spellings of it would mean
    /// aiming from somewhere you are not looking from.
    fn follow_prediction(&mut self) {
        let p = &self.prediction.state;
        self.camera.x = p.x;
        self.camera.y = p.y;
        self.camera.z = eye_height(p);
    }

    /// Take the server's word for our own body and replay what it has not seen.
    fn reconcile(&mut self, ack: u64) {
        // Copied out before the call: `reconcile` borrows `self.world` mutably
        // through `self.prediction`, and holding a reference into `self.players`
        // across it would borrow `self` twice.
        let Some((x, y, z, ground)) = self
            .players
            .iter()
            .find(|p| p.id == self.self_id)
            .map(|p| (p.x, p.y, p.z, p.ground))
        else {
            return;
        };
        // `ground` comes off the shared row and the momentum off the private
        // half. Both used to be invented here — `false` for the first, the
        // client's own velocity for the second — and both are on the wire. See
        // `Prediction::reconcile` for what inventing the momentum cost.
        let movement = self.you.as_ref().and_then(|y| y.movement.clone());
        self.prediction
            .reconcile(&self.world, ack, x, y, z, ground, movement.as_ref());
        self.follow_prediction();
    }

    fn pump_network(&mut self, event_loop: &ActiveEventLoop) {
        // Nothing to pump in Train, and nothing missing either: `place_offline`
        // already put a body on the map, so the loop below is the only part of
        // the frame a match contributes.
        let Some(socket) = &self.socket else { return };
        for item in socket.drain() {
            match item {
                Incoming::Event(Event::Welcome(w)) => {
                    eprintln!("hassault: joined room {} as {}", w.room, w.player_id);
                    self.room = w.room.clone();
                    self.self_id = w.player_id;
                    self.players = w.players;
                    self.joined = true;
                    // A new room is a new match. The tally is for the whole of
                    // one, so it survives a death and not a join.
                    self.tally.reset();
                    // The moment the room exists, and the only moment it is worth
                    // trying: the request is host-only and we are the host of
                    // whatever this welcome named.
                    if let Some((count, skill)) = self.pending_bots.take() {
                        if let Some(socket) = &self.socket {
                            if let Err(e) = socket.add_bot(count, &skill) {
                                eprintln!("hassault: could not add bots: {e}");
                            }
                        }
                    }
                    // Nothing in the old buffer belongs to this match: the
                    // server's clock and the roster both start again, and one
                    // stale frame is enough to hold every body at a position
                    // from the previous round.
                    self.snapshots.clear();
                    self.drawn.clear();
                    self.ping.reset();
                    // A join is not a correction — there is nothing in flight to
                    // replay, and treating it as one would replay commands from
                    // before we had a body.
                    if let Some(me) = self.players.iter().find(|p| p.id == self.self_id) {
                        let (x, y, z) = (me.x, me.y, me.z);
                        let (yaw, pitch) = (self.camera.yaw, self.camera.pitch);
                        self.prediction
                            .reset(x, y, z, yaw.to_radians(), pitch.to_radians());
                    }
                    self.follow_prediction();
                }
                Incoming::Event(Event::Snapshot(s)) => {
                    let ack = s.ack;
                    // A respawn moves the body without any command of ours
                    // causing it, so it is a reset rather than a correction —
                    // reconciling would try to ease a jump across the map.
                    let respawned = self.was_dead && s.you.alive;
                    self.was_dead = !s.you.alive;
                    if respawned {
                        // A jump back to full health is not healing, and the
                        // fall note from the death that caused it is stale.
                        self.hud.on_respawn();
                        // The numbers describe the fight that killed you.
                        // Floating them over a body across the map would be a
                        // reading of a hit that is no longer happening.
                        self.damage.clear();
                        // And the pouch is refilled, matching `reset_loadout`.
                        // The next snapshot's `you.nades` would correct this
                        // anyway; doing it here means the tray is right on the
                        // frame you respawn rather than one tick later.
                        self.utility.reset();
                    }
                    self.hud.on_self(&s.you);
                    self.hud.on_hits(&s.you.hits);
                    self.confirm_kill(&s.you.hits);
                    self.note_hits(&s.you.hits);
                    // Being hit, and hitting the ground. **One shake for the
                    // tick, not one per marker**: a shotgun lands eight pellets
                    // in a single snapshot, and eight `add` calls would put a
                    // point-blank blast at full trauma while the same total
                    // damage from a rifle burst barely registered. The amounts
                    // are summed and the curve is applied once.
                    //
                    // Both lists are per-tick rather than cumulative — the HUD's
                    // arrows and fall notice rely on that too, and would pile up
                    // forever otherwise.
                    let taken: f32 = s.you.hurt.iter().map(|h| h.amount).sum();
                    self.shake.add(damage_trauma(taken));
                    // A fall has no attacker, so it appears in neither `hurt`
                    // nor `hits` — landing hard is the one damage in the game
                    // that would otherwise be felt by nothing at all.
                    self.shake.add(damage_trauma(s.you.fell));
                    for fx in &s.fx {
                        self.hud.on_fx(fx, &self.self_id);
                        // The muzzle flash, from the server's own account of the
                        // shot rather than from the fire key: this client has no
                        // trigger controller, so the key flashes on shots the
                        // server refused for rate limiting, an empty magazine, or
                        // being dead.
                        // The shot's geometry, which this client parsed and
                        // discarded for as long as shots have existed: the
                        // server resolves every ray and sends the muzzle and
                        // one endpoint per pellet.
                        if let Fx::Shot {
                            id,
                            origin,
                            ends,
                            faces,
                            hit,
                            ..
                        } = fx
                        {
                            // `hit` has been on the wire and parsed into
                            // nothing since shots existed. It is now only the
                            // fallback for a shooter whose backend predates
                            // `faces` — which says per *pellet* whether there
                            // was a surface, and which one.
                            self.effects
                                .shot(*origin, ends, faces, id == &self.self_id, *hit);
                            // And the surface remembers being shot at. Every
                            // player's shots, not only ours: reading the room
                            // afterwards is most of what marks are for.
                            self.decals.shot(ends, faces);
                        }
                        if let Fx::Detonate {
                            nade, at, radius, ..
                        } = fx
                        {
                            self.effects.detonate(nade, *at, *radius);
                            // How hard it is felt is a function of *distance*,
                            // not of whether it hurt us: cover stops the damage
                            // and does not stop the ground moving. Measured from
                            // `camera`, the honest position — the drawn one has
                            // a shake in it already, and feeding that back would
                            // make the shake depend on itself.
                            let d = ((at[0] - self.camera.x).powi(2)
                                + (at[1] - self.camera.y).powi(2)
                                + (at[2] - self.camera.z).powi(2))
                            .sqrt();
                            self.shake.add(blast_trauma(d, *radius));
                        }
                        // Start the sink on the frame the pickup was announced
                        // rather than waiting for the next snapshot to say the
                        // same thing: at 20 Hz that difference is visible on an
                        // item somebody took in front of you.
                        if let Fx::Pickup { item, .. } = fx {
                            self.items.take(*item);
                        }
                        if let Fx::Shot { id, .. } = fx {
                            // Every shooter's upper body kicks, not just ours —
                            // the animation is how a shot reads from the outside.
                            if let Some(squad) = self.squad.as_mut() {
                                squad.note_shot(id);
                            }
                            if id == &self.self_id {
                                self.viewmodel.fire();
                                // From the served `kickback`, so the heavy
                                // weapon is whichever one the server currently
                                // says is heavy.
                                self.shake.add(fire_trauma(
                                    self.held().map(|w| w.kickback).unwrap_or(0.0),
                                ));
                                // Our own gun, in its own voice. The browser
                                // plays this off its local trigger; this client
                                // has no trigger controller, so it would fire on
                                // shots the server refused — and a gunshot for a
                                // shot that never happened is worse than one
                                // arriving a snapshot late.
                                self.play_own("shot", 0.55, true);
                            }
                        }
                    }
                    // What we can hear. The list is already only what is audible
                    // from where we are standing — resolved server-side, because
                    // a client that decided for itself would have been sent the
                    // positions to decide with.
                    if let Some(audio) = &self.audio {
                        let yaw = self.camera.yaw.to_radians();
                        for event in &s.you.noise {
                            audio.heard(event, yaw, &self.weapons);
                        }
                    }
                    // Grenades and clouds. Both were declared in `protocol.rs`
                    // and read by nothing at all until now — parsed every tick,
                    // 20 times a second, and thrown away.
                    self.nades.sync(&s.nades, &s.zones);
                    // `as_ref`, not `unwrap_or_default`: an absent `itemsOut`
                    // means this server has no items, which is a different fact
                    // from "every item is present" — read the second way it
                    // would pop every taken item back once a tick.
                    self.items.sync(s.items_out.as_ref());
                    self.scores = s.scores.clone();
                    self.apply_match_recoil(s.you.spray_index, s.you.weapon);
                    self.you = Some(s.you.clone());
                    // Filed for interpolation *before* the roster is replaced,
                    // and cloned rather than moved: the roster is what
                    // reconciliation reads and the buffer is what the renderer
                    // reads, and they are deliberately different things.
                    self.snapshots
                        .push(s.t, s.players.clone(), Self::clock_ms());
                    self.players = s.players;
                    if respawned {
                        // From the shared row, not from `you`: the private half
                        // carries no position at all. Reading one off it got
                        // `0.0` three times over — the world origin, which is
                        // inside the solid border, where the body wedges and
                        // cannot move.
                        let placed = self
                            .players
                            .iter()
                            .find(|p| p.id == self.self_id)
                            .map(|p| (p.x, p.y, p.z));
                        if let Some((x, y, z)) = placed {
                            let (yaw, pitch) = (self.camera.yaw, self.camera.pitch);
                            self.prediction
                                .reset(x, y, z, yaw.to_radians(), pitch.to_radians());
                            self.follow_prediction();
                        }
                    } else {
                        self.reconcile(ack);
                    }
                }
                Incoming::Event(Event::Error(e)) => {
                    if e.code == "not_signed_in" {
                        eprintln!(
                            "hassault: {} — sign in and choose a username in the dashboard first",
                            e.message
                        );
                    } else {
                        eprintln!("hassault: server refused: {}", e.message);
                    }
                    event_loop.exit();
                }
                Incoming::Event(Event::Pong(p)) => {
                    // A round trip on one clock. `p.t` is the stamp we sent, so
                    // this is a subtraction rather than a comparison of two
                    // machines' ideas of the time.
                    let rtt = (Self::clock_ms() - p.t).max(0.0) as f32;
                    self.ping.record(rtt);
                }
                Incoming::Event(Event::Invite(invite)) => {
                    // The event this client dropped that actually cost somebody
                    // something: `fabric.py` broadcasts an invite the moment it
                    // arrives, so a client already in a game is exactly who it
                    // is aimed at — and a friend inviting a native player got
                    // silence and no way to know why.
                    //
                    // Shown, not acted on. Joining would mean leaving this match
                    // and reconnecting to another room mid-session, which is a
                    // bigger change than telling the player it happened; the
                    // room id is on screen so `--room` can be used deliberately.
                    let who = if invite.host_name.is_empty() {
                        invite.host.clone()
                    } else {
                        invite.host_name.clone()
                    };
                    let map = if invite.map.is_empty() {
                        String::new()
                    } else {
                        format!(" on {}", invite.map)
                    };
                    eprintln!("hassault: {who} invited you to room {}{map}", invite.room);
                    self.hud
                        .note(format!("{who} invites you: room {}", invite.room), true);
                }
                Incoming::Event(Event::Invites(list)) => {
                    // Only in answer to asking, which this client does not do
                    // yet. Handled so it is not reported as unhandled forever —
                    // an empty branch that says why is worth more than a name in
                    // a divergence log nobody will action.
                    if !list.invites.is_empty() {
                        eprintln!("hassault: {} invite(s) waiting", list.invites.len());
                    }
                }
                Incoming::Event(Event::Joined(joined)) => {
                    if joined.player.id != self.self_id {
                        let name = name_or_id(&joined.player.name, &joined.player.id);
                        self.hud.note(format!("{name} joined"), false);
                    }
                }
                Incoming::Event(Event::Left(left)) => {
                    // Worth saying, and the one thing a snapshot cannot: a body
                    // that disconnected and a body behind a wall both simply
                    // stop appearing.
                    let name = self
                        .players
                        .iter()
                        .find(|p| p.id == left.player_id)
                        .map(|p| name_or_id(&p.name, &p.id))
                        .unwrap_or_else(|| left.player_id.clone());
                    if left.player_id != self.self_id {
                        self.hud.note(format!("{name} left"), false);
                    }
                }
                Incoming::Event(Event::Roster(roster)) => {
                    if !roster.added.is_empty() {
                        self.hud
                            .note(format!("{} bots fielded", roster.added.len()), false);
                    } else if roster.removed > 0 {
                        self.hud
                            .note(format!("{} bots kicked", roster.removed), false);
                    }
                }
                Incoming::Event(Event::ConsoleRes(res)) => {
                    self.console.on_response(&res, &mut self.cvars);
                }
                // Already reported by name in `protocol::classify` — see
                // `divergence::note_event`. Nothing to do here beyond not
                // pretending this was a handled message.
                Incoming::Event(Event::Other(_)) => {}
                Incoming::Closed(why) => {
                    eprintln!("hassault: connection closed: {why}");
                    event_loop.exit();
                }
            }
        }
    }

    /// This frame's view angles **in the simulation's units**.
    ///
    /// One expression, called by both the command that goes on the wire and the
    /// prediction that runs locally. That is the whole point of it: those two
    /// have to agree, they are written eighteen lines apart, and when one of them
    /// converted and the other did not the result was a client whose server-side
    /// body walked up to 93 degrees away from where the player was aiming —
    /// silently, and only in a match.
    ///
    /// The camera is the only thing in this client that thinks in degrees.
    /// Everything downstream of here is radians.
    fn view_angles(&self) -> (f32, f32) {
        (self.camera.yaw.to_radians(), self.camera.pitch.to_radians())
    }

    /// The frame's input, applied.
    ///
    /// Two paths, one set of rules: in a match the command goes on the wire and
    /// the prediction runs it locally, and in Train there is no wire and the same
    /// `physics::step` runs it directly. Train deliberately does not fake a
    /// server — no ack, no reconciliation, no interpolation — because there is
    /// nothing to be wrong about.
    fn send_input(&mut self, dt: f32) {
        if !self.joined {
            return;
        }
        // Bank the frame's real time and produce a command only once a whole
        // interval has passed. `dt` below is therefore the time that genuinely
        // elapsed since the previous command, never a frame time rounded up to
        // a floor — see `INPUT_HZ` for what the floor cost.
        self.input_accum += dt.max(0.0);
        if self.input_accum < INPUT_INTERVAL {
            return;
        }
        // The upper clamp still discards: a stall longer than `MAX_DT` is time
        // the server would refuse anyway, and banking it would only spend the
        // next second of honest movement paying it back.
        let dt = self.input_accum.min(MAX_DT);
        self.input_accum = 0.0;
        let input = MoveInput {
            forward: axis(self.keys.forward, self.keys.back),
            strafe: axis(self.keys.right, self.keys.left),
            jump: self.keys.jump,
            crouch: self.keys.crouch,
        };
        if self.socket.is_none() {
            self.local_seq += 1;
            let (yaw, pitch) = self.view_angles();
            self.prediction.state.yaw = yaw;
            self.prediction.state.pitch = pitch;
            physics::step(&self.world, &mut self.prediction.state, &input, dt);
            // The range plays the server's part, and it has to run *after* the
            // step: a shot leaves from where the body is this frame, and firing
            // before moving aims from where it was on the previous one.
            self.train(dt);
            self.follow_prediction();
            return;
        }
        let mut cmd = Command::new(0);
        cmd.dt = dt;
        cmd.forward = axis(self.keys.forward, self.keys.back);
        cmd.strafe = axis(self.keys.right, self.keys.left);
        cmd.jump = self.keys.jump;
        cmd.crouch = self.keys.crouch;
        // Firing rides on the movement command rather than being its own message,
        // which is the server's design: the shot then carries the exact angles and
        // sequence number of the frame it was fired on.
        //
        // **Rate-limited to the weapon's own interval**, which the browser has
        // always done (`combat.ts`) and this client never did: the flag used to
        // ride *every* command the key was held for, so a 700rpm rifle sent the
        // server thirty trigger pulls a second and it discarded all but eleven.
        // That was invisible while recoil was random; with a pattern the discards
        // are what decide how far the pattern has walked, so the courtesy became
        // load-bearing.
        //
        // Semi-automatic weapons still need the button released between shots,
        // which the server enforces for everyone — this only avoids sending
        // input that is certain to be thrown away.
        // A grenade in hand blocks the trigger, and the block is **client-side
        // only**: the server has no concept of an equipped grenade and needs
        // none. The obvious alternative — asking for a weapon switch — would put
        // `weapon: n` on this command, and `_handle_combat` cancels an in-flight
        // reload on a switch, so taking out a grenade would silently abort one.
        cmd.fire = self.keys.fire && !self.utility.equipped() && self.trigger_ready();
        // And the instant it was aimed at. Bodies are drawn `INTERP_DELAY_MS` in
        // the past, so without this every shot is resolved against positions a
        // tenth of a second newer than the ones on screen when the trigger was
        // pulled — which is not a small error at a running target, and reads as
        // "the hit registration is bad" rather than as a missing field.
        //
        // Left absent before the first snapshot rather than defaulted: a
        // fabricated render time asks the server to rewind to a moment that
        // never existed. The clamp on the far side is a bound, not a repair.
        if cmd.fire {
            cmd.view_t = self.snapshots.render_time(Self::clock_ms());
            self.last_fire_sent_ms = Self::clock_ms();
        }
        // Which cone the server should use for it. Clamped there against the
        // weapon this command lands on — the wire parser cannot know that.
        cmd.scoped = self.scoped;
        // `-1` is "no change", so this is naturally absent on every frame that
        // did not ask for one; taken rather than left set, or every command for
        // the rest of the match repeats a switch that already happened.
        cmd.weapon = std::mem::replace(&mut self.want_weapon, -1);
        cmd.reload = std::mem::take(&mut self.want_reload);
        // The throw, on the same command and for the same reason firing is: it
        // then carries the exact angles and sequence number of the frame the key
        // went down on. Edge-triggered in the controller — a key read as held
        // would set this sixty times a second, the server's cooldown would take
        // one, and the player would watch a full pouch turn into one grenade.
        // `clock_ms` and not `local_clock`: the latter is Train's own clock and
        // is only advanced by `train`, so in a match it sits at zero forever and
        // a cooldown measured against it would never elapse.
        let intent = self.utility.frame(Self::clock_ms(), self.you.as_ref());
        if intent.throwing {
            cmd.r#throw = true;
            cmd.nade = intent.nade;
            cmd.lob = intent.lob;
            // Ours, played locally. The server does send a `throw` noise, but it
            // sends it to everyone *else* — your own noises never come back, so
            // the thrower would be the one person in the room who could not hear
            // it leave their hand.
            self.play_own("throw", 0.4, false);
        }
        // **Radians on the wire, not degrees**, and from the same expression the
        // prediction uses — see `view_angles`. This line read `self.camera.yaw`
        // raw. The server does not range-check an angle, because there is no
        // range to check: it assigns `player.state.yaw = command.yaw` and takes
        // its sine. So a heading of 315 arrived as 315 *radians* — 48 degrees —
        // and the body was walked 93 degrees away from where the player was
        // looking, far enough that forward carried a backward component.
        //
        // Zero error at yaw 0, growing with the angle, which is what made it read
        // as "sometimes W goes backwards" rather than as a units bug — and why
        // every test in this crate missed it: they all aim along +x. Only a match
        // could show it at all, because Train sends no command; it steps the
        // physics straight off `to_radians()` and so was the one mode that had
        // the conversion right.
        let (yaw, pitch) = self.view_angles();
        cmd.yaw = yaw;
        cmd.pitch = pitch;

        // `push_command` stamps the sequence number, and the prediction is keyed
        // by it. Predicting under a number we invented separately would make
        // every `ack` refer to a different command than the one it replays.
        let Some(socket) = &mut self.socket else {
            return;
        };
        let seq = socket.push_command(cmd);
        // The same angles the command carries — literally, not equivalently.
        self.prediction
            .predict(&self.world, seq, input, dt, yaw, pitch);
        self.prediction.ease(dt);
        self.follow_prediction();

        // Rate-limited inside `flush` — see `SEND_INTERVAL`. Called every frame
        // regardless, so a command is never held longer than one interval.
        let rtt = self.ping.rtt();
        let stamp = Self::clock_ms();
        if let Some(socket) = &mut self.socket {
            if let Err(e) = socket.flush(rtt) {
                eprintln!("hassault: {e}");
            }
            if let Err(e) = socket.ping(stamp) {
                eprintln!("hassault: {e}");
            }
        }
    }

    /// The training range's frame: ammo, reloads, dummies, and the trigger.
    ///
    /// Everything here is what a match server would otherwise be doing, which is
    /// why it produces a `SelfState` and hands it to exactly the consumers a
    /// snapshot feeds. The HUD, the view model and the hitmarkers have no idea
    /// which half of the game they are drawing.
    fn train(&mut self, dt: f32) {
        self.local_clock += dt;
        self.range.update(dt);
        // After the step that produced this position, for the same reason the
        // server collects in `_movement_consequences`: you pick something up by
        // having moved onto it, and reading the position from before the step
        // would take an item a frame early and miss one you ran straight
        // through.
        let body = self.prediction.state;
        self.range.collect_items(body.x, body.y, body.z);
        // Offline there is no `itemsOut` on any wire, so the range is the
        // authority on what is currently gone — the same split as online, with
        // the range standing in for the server.
        self.items.sync(Some(&self.range.taken_items()));
        if self.keys.fire {
            self.try_fire();
        } else {
            // Released, so the next press is a fresh pull. A semi-automatic
            // weapon that did not track this would fire at the frame rate.
            self.trigger_used = false;
        }

        // Drained and forwarded exactly as a snapshot's would be — `self_state`
        // takes the hitmarkers with it, so this must happen once per frame and
        // the result must be the one everything downstream reads.
        let you = self.range.self_state();
        self.hud.on_self(&you);
        self.hud.on_hits(&you.hits);
        // Off the hitmarkers rather than off `Fx::Kill`, so this is one code
        // path: the range has no server and so no kill effects, and a Train mode
        // where downing a dummy is silent is the one place a player would first
        // notice the sound was missing.
        self.confirm_kill(&you.hits);
        self.note_hits(&you.hits);
        self.you = Some(you);
    }

    /// One trigger pull on the range, rate-limited the way the server would.
    ///
    /// The limit is not decoration: without it a 62 rpm sniper fires once per
    /// frame, which at this client's frame rate is roughly two thousand rounds a
    /// second and makes the mode useless for learning anything about timing.
    fn try_fire(&mut self) {
        // A grenade in hand blocks the range's trigger too. Train is where a
        // player learns which button does what, and a range where the left
        // button both threw a grenade and fired the rifle would teach neither.
        if self.utility.equipped() {
            return;
        }
        let Some(weapon) = self.held().cloned() else {
            return;
        };
        let interval = if weapon.interval > 0.0 {
            weapon.interval
        } else {
            0.1
        };
        if self.local_clock - self.last_fire < interval {
            return;
        }
        // Semi-automatic weapons need the button released between shots. `auto`
        // is served, so this cannot disagree with the server about which weapons
        // hold down.
        if !weapon.auto && self.trigger_used {
            return;
        }
        if !self.range.can_fire() {
            // Out of rounds, or mid-reload. The click is spent either way, so a
            // semi-automatic does not fire the instant a reload finishes with the
            // button still held.
            self.trigger_used = true;
            return;
        }

        let state = &self.prediction.state;
        let (x, y, z) = (state.x, state.y, state.z);
        let eye = physics::eye_offset(state);
        let crouching = state.crouch > 0.5;
        let (yaw, pitch) = (self.camera.yaw.to_radians(), self.camera.pitch.to_radians());
        let scoped = self.scoped;
        let Some(shot) = self
            .range
            .fire(&self.world, x, y, z, eye, yaw, pitch, scoped)
        else {
            return;
        };
        self.last_fire = self.local_clock;
        self.trigger_used = true;

        // **The kick.** In a match the server applies this and reconciliation
        // hands it back; on the range there is nobody to do it, and without it
        // the shoot-jump — the whole reason a training mode exists — silently
        // does nothing. Computed from the served `kickback`, so it is the same
        // shove a match would give.
        let kick = kick_vector(&weapon, yaw, pitch, crouching);
        physics::apply_impulse(&mut self.prediction.state, kick[0], kick[1], kick[2]);

        self.viewmodel.fire();
        // The same jolt a match gives, from the same served number — Train is
        // where the shoot-jump is learnt, and learning it against a camera that
        // behaves differently would be learning the wrong thing.
        self.shake.add(fire_trauma(weapon.kickback));
        self.play_own("shot", 0.55, true);
        // The range resolves its own shots, so its faces come from `trace.rs`
        // rather than off the wire — the same numbers, pinned against the
        // server's by `physics-vectors.json`. Tracers and impacts too: the range
        // is where a spray pattern is learnt, and it is not learnable without
        // seeing where the rounds went.
        self.effects
            .shot(shot.origin, &shot.ends, &shot.faces, true, false);
        self.decals.shot(&shot.ends, &shot.faces);
    }

    /// Whether enough time has passed to bother sending another `fire`.
    ///
    /// A courtesy to the server rather than a second enforcement — it owns the
    /// fire rate and this cannot beat it. What it buys is that the commands the
    /// server *keeps* are the ones this client thinks it sent, which is what
    /// makes the spray index it reports back line up with the shots the player
    /// took.
    fn trigger_ready(&self) -> bool {
        let Some(weapon) = self.held() else {
            return true;
        };
        Self::clock_ms() - self.last_fire_sent_ms >= (weapon.interval as f64) * 1000.0
    }

    /// Kick the camera by however far the server says the pattern has moved.
    ///
    /// **Driven from the server's index, not from the trigger.** This client has
    /// no local trigger controller, so a kick applied when the fire key went down
    /// would move the view for shots the server refused — rate limiting, an empty
    /// magazine, being dead — and the crosshair would drift permanently away from
    /// where the bullets go. Reading the index off the snapshot is a frame or two
    /// late and *exact*, which is the right trade for a pattern whose only value
    /// is being learnable.
    ///
    /// **The step, not the position.** The served table is absolute — it is a
    /// shape on a wall a player memorises — while the camera accumulates, so what
    /// goes on it is `spray[i] - spray[i-1]`. Applying the absolute walks the
    /// view away by the running sum and is unusable within half a magazine, while
    /// reading exactly like a badly-chosen constant.
    ///
    /// `Shake` is deliberately untouched: it is a *copy* of the camera by design
    /// (`camera.rs`), never a mutation, and turning it into recoil the server
    /// honours would make a screen effect part of the aim.
    fn apply_match_recoil(&mut self, index: i32, slot: i32) {
        let previous = std::mem::replace(&mut self.match_spray_index, index);
        // Only a burst *advancing*. A reset to zero is the pattern starting
        // again, and giving that back as a downward kick would drag the view
        // every time somebody stopped shooting.
        if index <= previous {
            return;
        }
        let Some(weapon) = self.weapons.get(slot.max(0) as usize) else {
            return;
        };
        if weapon.spray.is_empty() {
            return;
        }
        for i in previous.max(0)..index {
            let here = weapon.spray_offset(i as usize);
            let before = if i > 0 {
                weapon.spray_offset((i - 1) as usize)
            } else {
                [0.0, 0.0]
            };
            self.camera.yaw += (here[0] - before[0]).to_degrees();
            self.camera.pitch += (here[1] - before[1]).to_degrees();
        }
        // The camera's own clamp lives in `look`, which this does not go through.
        // Re-applied here for the reason `camera.rs` documents for `shaken`: an
        // unclamped pitch reaches vertical, where the view matrix's up vector
        // becomes parallel to the forward one and every number in it turns NaN.
        self.camera.pitch = self.camera.pitch.clamp(-89.9, 89.9);
    }

    /// Reload, in whichever half of the game is running.
    fn reload(&mut self) {
        if self.socket.is_some() {
            // A flag on the next command, like firing: the server owns the
            // magazine, and a reload it did not agree to is a client counting
            // rounds it does not have.
            self.want_reload = true;
            return;
        }
        if self.range.request_reload_started() {
            self.play_own("reload", 0.5, false);
        }
    }

    /// Switch weapons. Offline the range owns the slot; in a match the *server*
    /// does, so this is a request on the next command rather than a local change
    /// — setting it locally would show a weapon the server has not given us.
    fn select_weapon(&mut self, slot: usize) {
        // A weapon key puts the grenade away — the counterpart of a number key
        // taking one out. Purely cosmetic: `holster` sets no slot and puts
        // nothing on the wire.
        self.utility.holster();
        if slot >= self.weapons.len() {
            return;
        }
        // Asking for the slot we already hold is not a switch, and dipping the
        // weapon for it would punish leaning on the number row.
        let held = self.you.as_ref().map(|y| y.weapon).unwrap_or(-1);
        if slot as i32 != held {
            // Down *now*, on the press. In a match the answer is a round trip
            // away and a holster that waited for it would read as input lag;
            // if the switch is refused the weapon comes back up on its own. See
            // `WeaponViewModel::holster`.
            self.viewmodel.holster();
        }
        if self.socket.is_some() {
            self.want_weapon = slot as i32;
            return;
        }
        self.range.select(slot);
    }

    /// Open or close the pause menu.
    ///
    /// The pointer follows it: a menu you cannot click is a menu with a second
    /// and worse set of controls, and the game underneath keeps running either
    /// way — **this is not a pause**. It cannot be: in a match the server is
    /// still simulating, and a client that stopped reading its socket to show a
    /// settings page would come back to a body that had been shot at for a
    /// minute. Train runs on for the same reason rather than growing a second
    /// rule nobody could see.
    fn toggle_menu(&mut self) {
        self.menu.toggle();
        let open = self.menu.open;
        self.set_grab(!open);
        if open {
            // Whatever was held when the menu opened stays held otherwise, and
            // you return to the game already walking.
            self.keys = Keys::default();
            self.trigger_used = true;
        }
    }

    /// Which side we are on, from the shared roster.
    ///
    /// Read off our own `PlayerRow` rather than kept as state: the server can
    /// move a player between teams (autobalance), and a cached copy would leave
    /// the radar colouring the wrong half of the room with nothing reporting it.
    /// `-1` while there is no row for us yet — a team nobody is on, so the
    /// radar shows no friendlies rather than treating every stranger as one.
    fn my_team(&self) -> i32 {
        self.players
            .iter()
            .find(|p| p.id == self.self_id)
            .map(|p| p.team)
            .unwrap_or(-1)
    }

    /// The live `net.graph` step.
    ///
    /// The CVar wins when it is set, and `net_graph_default` is what F3 has been
    /// stepping. Reading through one accessor is the point: the browser pane
    /// draws its NetGraph from `net.graph` and this client drew it from a
    /// private field, so `net.graph 2` typed into a console changed one client
    /// and not the other — with nothing anywhere reporting that.
    fn net_graph(&self) -> u32 {
        self.cvars
            .number("net.graph")
            .map(|v| v.clamp(0.0, 3.0) as u32)
            .unwrap_or(self.net_graph_default)
    }

    /// Whether hitboxes are drawn. The CVar over the saved setting, because a
    /// console assignment is a statement about *now* and the setting is a
    /// preference — and the browser pane resolves the same pair the same way.
    fn show_hitboxes(&self) -> bool {
        self.cvars
            .boolean("draw.hitboxes")
            .unwrap_or(self.settings.show_hitboxes)
    }

    /// The crosshair this frame, with any console overrides folded in.
    ///
    /// Built per frame rather than mutated in place so the saved setting is
    /// never overwritten by a console experiment: closing the console with
    /// `draw.crosshair.size 9` still on it should not leave 9 in the settings
    /// bag, and a mutation would.
    fn crosshair(&self) -> Crosshair {
        let mut c = self.settings.crosshair;
        if let Some(v) = self.cvars.number("draw.crosshair.size") {
            c.size = v.clamp(1.0, 12.0);
        }
        if let Some(v) = self.cvars.number("draw.crosshair.gap") {
            c.gap = v.clamp(0.0, 20.0);
        }
        if let Some(v) = self.cvars.number("draw.crosshair.thickness") {
            c.thickness = v.clamp(0.2, 3.0);
        }
        if let Some(v) = self.cvars.string("draw.crosshair.style") {
            c.style = CrosshairStyle::parse(v);
        }
        if let Some(v) = self.cvars.boolean("draw.crosshair.outline") {
            c.outline = v;
        }
        if let Some(v) = self.cvars.boolean("draw.crosshair.dot") {
            c.dot = v;
        }
        if let Some(v) = self.cvars.number("draw.crosshair.alpha") {
            c.alpha = v.clamp(0.15, 1.0);
        }
        c
    }

    /// Mouse sensitivity, CVar over setting. Same resolution as the crosshair.
    fn sensitivity(&self) -> f32 {
        self.cvars
            .number("player.sensitivity")
            .map(|v| v.clamp(0.05, 10.0))
            .unwrap_or(self.sensitivity)
    }

    /// Make the drawn prop match the weapon in hand.
    ///
    /// Called every frame with whatever the server last said we are holding.
    /// **Nothing is parsed here.** It used to be — a swap decoded the weapon's
    /// GLB inline, 57–110 ms of frame thread on the key press, and again on
    /// every press after it because only one prop stayed resident. Pressing
    /// 1/2/3 therefore stalled the whole game, which is what this shape fixes:
    /// `prop::preload` parses off the loop, this drains what it has finished and
    /// pays only the upload, and picking a weapon is a lookup.
    ///
    /// Every failure falls back to the box model rather than propagating. A
    /// weapon with no GLB, a GLB that will not parse, a prop that cannot be
    /// fitted, a prop that has not arrived yet: all four are a weapon that draws
    /// as boxes, which is a complete working weapon and was the only kind there
    /// was until recently. The parse failure is *reported* through `divergence`,
    /// though — silently drawing boxes because an asset is corrupt is the shape
    /// of bug this client keeps finding.
    fn sync_prop(&mut self, weapon: &str) {
        // No renderer yet means the window has not come up. Nothing to upload
        // to, and the next frame that has one runs this again — the preloader's
        // results wait in the channel until then.
        let Some(renderer) = self.renderer.as_mut() else {
            return;
        };
        // Drained here rather than in a handler of its own: this is the one
        // place per frame that already owns the renderer, and an upload is a few
        // milliseconds spread over the frames the props happen to land on.
        while let Ok((id, parsed)) = self.prop_loads.try_recv() {
            match parsed {
                Ok(prop) => renderer.set_prop(&id, &prop),
                Err(e) => hassault_native::divergence::note_prop(&id, &e),
            }
        }
        if weapon != self.prop_weapon {
            self.prop_weapon = weapon.to_string();
            self.prop_fitted = false;
        }
        // Retried until it lands, because a weapon can be picked up before its
        // prop has finished parsing — and then never again, which is what the
        // flag is for: `fit_prop` walks the box model's vertices, and doing that
        // every frame for a prop that has not moved is work for nothing.
        if !self.prop_fitted {
            match renderer.use_prop(weapon) {
                Some((min, max)) if self.viewmodel.fit_prop(min, max).is_some() => {
                    self.prop_fitted = true;
                }
                Some(_) => {
                    // Degenerate bounds: this prop will never fit, so stop
                    // asking and leave the boxes.
                    self.prop_fitted = true;
                    renderer.clear_prop();
                    self.viewmodel.clear_prop();
                }
                None => {
                    // No prop for this weapon — none exists (see `WEAPON_GLBS`),
                    // it failed to parse, or it has not landed yet.
                    renderer.clear_prop();
                    self.viewmodel.clear_prop();
                }
            }
        }
    }

    /// Open or close the console.
    ///
    /// Releases the pointer on the way open, for the same reason Escape does:
    /// you cannot type into a window that is eating your mouse, and a console
    /// that left the view spinning while you typed would be unusable. Restoring
    /// the grab on close would be wrong in the menu's case and is left to the
    /// player's next click, exactly as `set_grab(false)` elsewhere.
    fn toggle_console(&mut self) {
        self.console.open = !self.console.open;
        if !self.console.open {
            // Closing hands the mouse back, the same way closing the menu does.
            // It used to release the pointer on the way in and never take it
            // again, so the tilde that put you back in the game left you with a
            // free cursor and a view that would not turn until you clicked —
            // which reads as the console having broken the mouse.
            //
            // Unless the menu is up behind it: `Escape` while the console is
            // open closes the console, and grabbing the pointer would then hide
            // the cursor over a menu that is still there to be clicked.
            if !self.menu.open {
                self.set_grab(true);
            }
            return;
        }
        {
            self.set_grab(false);
            // Every movement key is released, not remembered. A console opened
            // mid-strafe otherwise leaves the body walking into a wall for as
            // long as it takes to type a command — and the key-up that would
            // have stopped it is swallowed by the console.
            self.keys = Keys::default();
        }
    }

    /// A left click inside the open console.
    ///
    /// Hit-tested against **`hud::console_hits`, the same layout the painter
    /// draws from**, which is the rule `Menu::rows_at` already follows: a second
    /// computation of where a chip is drawn is a click that lands on its
    /// neighbour, and nothing anywhere reports that.
    ///
    /// A click that hits nothing is swallowed rather than falling through. The
    /// panel covers the top half of the screen and the world is still live
    /// behind it; a miss that reached the trigger would fire a shot at whatever
    /// the crosshair was on while the player was reading their own scrollback.
    fn console_click(&mut self) {
        let (w, h) = self.window_size();
        let (w, h) = (w as f32, h as f32);
        let quick = self.console.quick_actions(&self.cvars);
        let detail = self.console.suggestion_detail(&self.cvars);
        let hits = hud::console_hits(&quick, &self.console.suggestions, detail.is_some(), w, h);
        let (x, y) = self.pointer;

        if let Some((index, _)) = hits.chips.iter().find(|(_, r)| r.contains(x, y)) {
            if let Some(command) = self.console.quick_command(*index, &self.cvars) {
                // Echoed like a typed line, for the same reason F1-F8 echo it:
                // a chip that changed something without saying what would be the
                // one part of this console you could not audit.
                self.console
                    .push(format!("] {command}"), console::Tone::Echo);
                self.run_console(&command);
            }
            return;
        }

        if let Some((index, _)) = hits.suggestions.iter().find(|(_, r)| r.contains(x, y)) {
            self.console.select_suggestion(*index);
            self.console.complete();
        }
    }

    /// Run one console line and send it on if the node has to answer it.
    fn run_console(&mut self, line: &str) {
        let online = self.socket.is_some();
        let dispatch = self.console.execute(line, &mut self.cvars, online);
        self.dispatch_console(dispatch);
    }

    fn dispatch_console(&mut self, dispatch: Dispatch) {
        match dispatch {
            Dispatch::Handled => {}
            Dispatch::SaveTranscript { text } => self.save_transcript(&text),
            Dispatch::Send { command, req_id } => self.send_console(&command, req_id),
        }
    }

    /// Write the scrollback out and say where it went.
    ///
    /// Into the **system temp directory**, which is a deliberate choice and not
    /// laziness. This client writes nothing else to disk at all — its settings
    /// live on the node and arrive over HTTP — so there is no data directory
    /// here to put it in, and inventing one would mean a second answer to a
    /// question `backend/paths.py` is the single authority on. A transcript is
    /// also a transient artifact: you write it to attach it to a bug report, not
    /// to keep it. Temp is where both of those point.
    fn save_transcript(&mut self, text: &str) {
        // Seconds since the epoch, not a formatted date: `std` cannot render a
        // local time, and all this has to do is stop two saves in one session
        // overwriting each other.
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let path = std::env::temp_dir().join(format!("hassault-console-{stamp}.log"));
        match std::fs::write(&path, text) {
            Ok(()) => self.console.note(format!("wrote {}", path.display())),
            Err(e) => self
                .console
                .error(format!("could not write {}: {e}", path.display())),
        }
    }

    fn send_console(&mut self, command: &str, req_id: u64) {
        let Some(socket) = &self.socket else {
            // `execute` already refuses when offline, so reaching here means the
            // socket went away between the two — worth saying rather than
            // dropping the line into nothing.
            self.console
                .error("the connection is gone; nothing was sent");
            return;
        };
        if let Err(e) = socket.console_exec(command, req_id) {
            self.console.error(format!("could not send: {e}"));
        }
    }

    /// The console's keyboard. Returns whether the key was consumed.
    ///
    /// The console takes the keyboard **whole** while it is up, the same rule
    /// the menu follows: a `W` typed into a command must not also walk you
    /// forward, and the key that closes it must not also do something in the
    /// game on the way past.
    fn console_key(&mut self, code: KeyCode, text: Option<&str>) {
        match code {
            KeyCode::Escape | KeyCode::Backquote => {
                self.console.open = false;
                return;
            }
            KeyCode::Enter | KeyCode::NumpadEnter => {
                let online = self.socket.is_some();
                let dispatch = self.console.submit(&mut self.cvars, online);
                self.dispatch_console(dispatch);
                return;
            }
            KeyCode::Backspace => {
                self.console.backspace();
                return;
            }
            KeyCode::Tab => {
                self.console.complete();
                return;
            }
            KeyCode::ArrowUp => {
                self.console.history_prev();
                return;
            }
            KeyCode::ArrowDown => {
                self.console.history_next();
                return;
            }
            KeyCode::ArrowLeft => {
                self.console.move_cursor(-1);
                return;
            }
            KeyCode::ArrowRight => {
                self.console.move_cursor(1);
                return;
            }
            KeyCode::PageUp => {
                self.console.scroll_by(1, 8);
                return;
            }
            KeyCode::PageDown => {
                self.console.scroll_by(-1, 8);
                return;
            }
            // The browser's filter tabs. Ctrl is not needed — nothing in a
            // console line is typed with a bare function key — but `^F` is what
            // the header advertises and what every other text surface uses, so
            // the plain key is left alone for whoever binds it.
            KeyCode::KeyF if self.modifiers.control_key() => {
                self.console.cycle_filter();
                let hidden = self.console.hidden_count();
                self.console.note(format!(
                    "filter: {} ({hidden} lines hidden)",
                    self.console.filter.label()
                ));
                return;
            }
            // The browser's toolbar, as keys. The command comes from
            // `quick_actions` rather than being spelled again here: a toggle has
            // to know what it is toggling *from*, and a second copy of that
            // arithmetic is how a key ends up only ever turning something on.
            KeyCode::F1
            | KeyCode::F2
            | KeyCode::F3
            | KeyCode::F4
            | KeyCode::F5
            | KeyCode::F6
            | KeyCode::F7
            | KeyCode::F8 => {
                let index = match code {
                    KeyCode::F1 => 0,
                    KeyCode::F2 => 1,
                    KeyCode::F3 => 2,
                    KeyCode::F4 => 3,
                    KeyCode::F5 => 4,
                    KeyCode::F6 => 5,
                    KeyCode::F7 => 6,
                    _ => 7,
                };
                if let Some(command) = self.console.quick_command(index, &self.cvars) {
                    // Echoed and run through the same path a typed line takes,
                    // so the log records what the key did. A quick action whose
                    // effect was invisible in the scrollback would be the one
                    // part of this console you could not audit.
                    self.console
                        .push(format!("] {command}"), console::Tone::Echo);
                    self.run_console(&command);
                }
                return;
            }
            _ => {}
        }
        // Text comes from the *logical* key, never from the physical one: a
        // physical `KeyZ` is a `Z` on QWERTY and a `W` on AZERTY, and a console
        // that spelled commands by scancode would be unusable on half the
        // keyboards in the world.
        if let Some(text) = text {
            self.console.insert(text);
        }
    }

    fn window_size(&self) -> (f32, f32) {
        self.renderer
            .as_ref()
            .map(|r| {
                let (w, h) = r.size();
                (w as f32, h as f32)
            })
            .unwrap_or((1280.0, 800.0))
    }

    fn menu_rows(&self) -> usize {
        self.menu.rows(&self.settings, self.socket.is_some()).len()
    }

    fn menu_key(&mut self, code: KeyCode, event_loop: &ActiveEventLoop) {
        let count = self.menu_rows();
        match code {
            KeyCode::Escape => {
                if self.menu.escape() {
                    self.set_grab(true);
                }
            }
            KeyCode::ArrowUp | KeyCode::KeyW => self.menu.move_cursor(-1, count),
            KeyCode::ArrowDown | KeyCode::KeyS => self.menu.move_cursor(1, count),
            KeyCode::ArrowLeft | KeyCode::KeyA => {
                self.menu_activate(self.menu.cursor(), -1, event_loop)
            }
            KeyCode::ArrowRight | KeyCode::KeyD => {
                self.menu_activate(self.menu.cursor(), 1, event_loop)
            }
            KeyCode::Enter | KeyCode::NumpadEnter | KeyCode::Space => {
                self.menu_activate(self.menu.cursor(), 1, event_loop)
            }
            _ => {}
        }
    }

    /// Act on one row. `step` is which way a value moves; a click is +1, which is
    /// what makes one control serve both the mouse and the keyboard.
    fn menu_activate(&mut self, index: usize, step: i32, event_loop: &ActiveEventLoop) {
        let rows = self.menu.rows(&self.settings, self.socket.is_some());
        let Some(row) = rows.get(index) else { return };
        match row.action {
            Action::Resume => {
                self.menu.close();
                self.set_grab(true);
            }
            Action::Open(page) => {
                self.menu.page = page;
                self.menu.move_cursor(-(self.menu.cursor() as i32), 1);
            }
            Action::Back => {
                self.menu.page = Page::Root;
                self.menu.move_cursor(-(self.menu.cursor() as i32), 1);
            }
            Action::Quit => {
                // **In a match, the card comes first.** Leaving is the end of
                // the match for this player, and it is the same instant the
                // server settles up — `leave` reads `result_for` before `remove`
                // drops the counters. So this is the one moment a debrief is
                // both earned and still wanted by somebody looking at the game.
                //
                // Train has no match to summarise and no opponents to be
                // measured against, so it quits as it always did rather than
                // showing a card of numbers this client would have to invent.
                if self.socket.is_some() {
                    self.menu.close();
                    self.summary.open();
                    // The pointer comes back with it: the card has a button, and
                    // a page you can only leave with the keyboard while the
                    // mouse is captured reads as frozen.
                    self.set_grab(false);
                    self.keys = Keys::default();
                    return;
                }
                self.leave_and_exit(event_loop);
            }
            action => {
                let keys = menu::apply(action, step, &mut self.settings);
                if keys.is_empty() {
                    return;
                }
                // Applied to the live client *before* it is saved: the point of
                // an in-game menu is seeing the change on the frame you make it,
                // and the write is a round trip on another thread.
                self.apply_settings(action);
                for key in keys {
                    if let Some(value) = self.settings.value_for(key) {
                        self.writer.save(key, value);
                    }
                }
            }
        }
    }

    /// Push a changed setting into whatever owns it.
    ///
    /// Only the ones that live somewhere else need this — the crosshair is read
    /// straight off `self.settings` when the HUD is built, so it needs nothing.
    fn apply_settings(&mut self, action: Action) {
        match action {
            Action::Sensitivity => self.sensitivity = self.settings.sensitivity,
            Action::Fullscreen => self.set_fullscreen(self.settings.video.fullscreen),
            // Nothing to apply: the flag is read straight from `self.settings`
            // by the render path, so toggling it is already in force.
            Action::ShowHitboxes => {}
            // The FOV is the camera's, not the renderer's, and it goes through
            // `apply_zoom` rather than straight onto the camera: setting
            // `camera.fov` here would be overwritten by the next scope step, and
            // the bug would only show while scoped.
            Action::Fov => {
                self.base_fov = self.settings.video.fov;
                self.apply_zoom();
            }
            // Nothing to apply: the limiter reads `self.settings` at the top of
            // every frame, so the next one is already capped.
            Action::FpsLimit => {}
            Action::RenderScale
            | Action::Quality
            | Action::Vsync
            | Action::Antialias
            | Action::Shadows => {
                if let Some(renderer) = &mut self.renderer {
                    renderer.set_video(self.settings.video);
                }
            }
            _ => {}
        }
    }

    /// Borderless rather than exclusive fullscreen.
    ///
    /// Exclusive takes the display mode, which is a mode switch on the way in and
    /// another on the way out, a black screen at each end, and every other window
    /// on that monitor rearranged when the game exits. Borderless costs a
    /// compositor pass that this client's present mode already avoids the worst
    /// of, and alt-tab is instant — which matters for a game launched from a
    /// dashboard you are going back to.
    fn set_fullscreen(&mut self, on: bool) {
        let Some(window) = &self.window else { return };
        window.set_fullscreen(if on {
            Some(winit::window::Fullscreen::Borderless(None))
        } else {
            None
        });
        self.fullscreen = on;
    }

    fn set_grab(&mut self, grab: bool) {
        let Some(window) = &self.window else { return };
        if grab {
            // Locked first, Confined as the fallback: Windows does not implement
            // `Locked`, and macOS does not implement `Confined`. Trying one and
            // giving up leaves the mouse free on one of the two platforms — and
            // it fails silently, as a view that drifts to a screen edge and stops.
            let locked = window
                .set_cursor_grab(CursorGrabMode::Locked)
                .or_else(|_| window.set_cursor_grab(CursorGrabMode::Confined));
            if locked.is_err() {
                eprintln!("hassault: could not capture the pointer");
                return;
            }
            window.set_cursor_visible(false);
            self.focused = true;
        } else {
            let _ = window.set_cursor_grab(CursorGrabMode::None);
            window.set_cursor_visible(true);
            self.focused = false;
            // Coming back to the window at 4× with no memory of having scoped
            // reads as the mouse having broken.
            self.unscope();
        }
    }

    fn update_title(&mut self) {
        let Some(window) = &self.window else { return };
        let (backend, gpu) = self
            .renderer
            .as_ref()
            .map(|r| (r.backend.as_str(), r.adapter_name.as_str()))
            .unwrap_or(("?", "?"));
        // `pending` is a direct read on the round trip: it is how many commands
        // the server has not acknowledged yet, which is the latency prediction is
        // covering for. Worth showing, because it is the one number that says
        // whether the prediction is working hard or barely at all.
        // Offline there is nothing in flight, and printing "0 in flight" would
        // read as a match with a perfect connection rather than as no match.
        let link = match &self.socket {
            Some(_) => format!("{} in flight", self.pending),
            None => "train".to_string(),
        };
        // The rendered resolution, but only when it is not the window's: a
        // render scale is invisible in a screenshot, and somebody who left it at
        // 50% would otherwise have no way to notice.
        let scale = self
            .renderer
            .as_ref()
            .map(|r| {
                let (sw, sh) = r.scene_size();
                let (ww, _) = r.size();
                if sw == ww {
                    String::new()
                } else {
                    format!(" · {sw}×{sh}")
                }
            })
            .unwrap_or_default();
        window.set_title(&format!(
            "HorribleAssault — {} — {:.0} fps · {} · {} · {} tris{} · {}{}",
            self.map_name,
            self.fps,
            backend,
            gpu,
            self.mesh.triangles,
            scale,
            link,
            if self.focused {
                ""
            } else {
                " · click to play"
            },
        ));
    }
}

/// A key as the console spells it.
///
/// `KeyF` is `f` and `Digit1` is `1`, because `bind keyf "..."` is not something
/// anybody would type. Derived from winit's own name rather than from a table:
/// a table would need a row per key and would be missing exactly the one
/// somebody wanted to bind.
/// A player's display name, falling back to their id.
///
/// A blank name is not an error — a bot's name is its own, and a browser client
/// that has not been told a username yet sends none — but "  joined" is a line
/// nobody can read.
fn name_or_id(name: &str, id: &str) -> String {
    if name.trim().is_empty() {
        id.to_string()
    } else {
        name.to_string()
    }
}

fn key_name(code: KeyCode) -> String {
    let raw = format!("{code:?}");
    let trimmed = raw
        .strip_prefix("Key")
        .or_else(|| raw.strip_prefix("Digit"))
        .unwrap_or(&raw);
    trimmed.to_ascii_lowercase()
}

fn axis(positive: bool, negative: bool) -> f32 {
    (positive as i32 as f32) - (negative as i32 as f32)
}

impl ApplicationHandler for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        // **Fullscreen from the first frame, not applied after one.** Creating
        // a window and then toggling it means a visible flash of a 1280×800
        // window on a 4K monitor, and on some compositors a resize the renderer
        // has to service before anything has been drawn.
        //
        // Borderless rather than exclusive: see `set_fullscreen`.
        let fullscreen = self
            .settings
            .video
            .fullscreen
            .then_some(winit::window::Fullscreen::Borderless(None));
        let attrs = Window::default_attributes()
            .with_title("HorribleAssault")
            .with_fullscreen(fullscreen)
            // The size a windowed session opens at, and what fullscreen returns
            // to when it is turned off in the menu.
            .with_inner_size(winit::dpi::LogicalSize::new(1280.0, 800.0));
        let window = match event_loop.create_window(attrs) {
            Ok(w) => Arc::new(w),
            Err(e) => {
                eprintln!("hassault: could not open a window: {e}");
                event_loop.exit();
                return;
            }
        };
        self.fullscreen = self.settings.video.fullscreen;
        match pollster::block_on(Renderer::new(
            window.clone(),
            &self.mesh,
            self.settings.video,
        )) {
            Ok(renderer) => {
                eprintln!(
                    "hassault: {} on {} — {} triangles",
                    renderer.backend, renderer.adapter_name, self.mesh.triangles
                );
                self.renderer = Some(renderer);
                if let (Some(renderer), Some(squad)) = (self.renderer.as_mut(), self.squad.as_ref())
                {
                    renderer.install_characters(squad.operator());
                }
            }
            Err(e) => {
                eprintln!("hassault: {e}");
                event_loop.exit();
                return;
            }
        }
        self.window = Some(window);
        self.update_title();
        // Poll rather than Wait: frames are driven by this loop, not by the
        // compositor asking for one.
        event_loop.set_control_flow(ControlFlow::Poll);
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => {
                if let Some(socket) = &self.socket {
                    let _ = socket.leave();
                }
                event_loop.exit();
            }
            WindowEvent::Resized(size) => {
                if let Some(r) = &mut self.renderer {
                    r.resize(size.width, size.height);
                }
            }
            // Dragging the window to a monitor with a different scale factor.
            //
            // winit does **not** guarantee a `Resized` alongside this, and the
            // surface is configured in *physical* pixels — so without this arm
            // the swapchain keeps the old monitor's size and the picture is
            // stretched or cropped until something else happens to resize the
            // window. It reads as "the game went blurry on my second screen",
            // which is a long way from the actual cause.
            WindowEvent::ScaleFactorChanged { .. } => {
                if let (Some(r), Some(w)) = (&mut self.renderer, &self.window) {
                    let size = w.inner_size();
                    r.resize(size.width, size.height);
                }
            }
            WindowEvent::Focused(false) => self.set_grab(false),
            // The wheel scrolls the console and nothing else. In play it is
            // deliberately inert: a weapon-cycle bound to it is the one binding
            // people rebind first, and guessing wrong is worse than nothing.
            WindowEvent::MouseWheel { delta, .. } if self.editing() => {
                // Multiplicative, so one notch does the same proportional thing
                // whether you are inching along a wall or crossing the map.
                let notches = match delta {
                    MouseScrollDelta::LineDelta(_, y) => y,
                    MouseScrollDelta::PixelDelta(p) => p.y as f32 / 40.0,
                };
                if let Some(editor) = self.editor.as_mut() {
                    editor.camera.adjust_speed(notches);
                }
            }
            WindowEvent::MouseWheel { delta, .. } if self.console.open => {
                let lines = match delta {
                    MouseScrollDelta::LineDelta(_, y) => y,
                    // A trackpad reports pixels. Divided by a line height rather
                    // than used raw, or one flick scrolls past the whole buffer.
                    MouseScrollDelta::PixelDelta(pos) => pos.y as f32 / 20.0,
                };
                let step = lines.round() as i32;
                if step != 0 {
                    self.console.scroll_by(step, 8);
                }
            }
            WindowEvent::CursorMoved { position, .. } => {
                self.pointer = (position.x as f32, position.y as f32);
                // Only meaningful while the menu or the console is up: in play
                // the pointer is locked and the look comes from `DeviceEvent`,
                // which is a raw delta and not a position. See the module docs.
                if self.menu.open {
                    let (w, h) = self.window_size();
                    let count = self.menu_rows();
                    self.menu
                        .hover(position.x as f32, position.y as f32, count, w, h);
                } else if self.summary.open {
                    let (w, h) = self.window_size();
                    self.summary
                        .pointer(position.x as f32, position.y as f32, w, h);
                }
            }
            WindowEvent::MouseInput { state, button, .. } if self.summary.open => {
                // Only the button does anything. A click anywhere else on a card
                // whose only action is irreversible would leave a match by
                // accident.
                if button == MouseButton::Left && state == ElementState::Pressed {
                    let (w, h) = self.window_size();
                    let (x, y) = self.pointer;
                    if self.summary.hit(x, y, w, h) {
                        self.leave_and_exit(event_loop);
                    }
                }
            }
            WindowEvent::MouseInput { state, button, .. } if self.menu.open => {
                if button == MouseButton::Left && state == ElementState::Pressed {
                    let (w, h) = self.window_size();
                    let count = self.menu_rows();
                    if let Some(index) = self.menu.hit(count, w, h) {
                        self.menu_activate(index, 1, event_loop);
                    }
                }
            }
            // A click with the console up is a click *on the console*: grabbing
            // the pointer there would hide the cursor and start turning the view
            // while the player is still typing. What it does instead is what the
            // toolbar buttons in the browser pane do — the chips and the
            // completion row are the two things there that are worth a click
            // rather than a key.
            WindowEvent::MouseInput { state, button, .. } if self.console.open => {
                if button == MouseButton::Left && state == ElementState::Pressed {
                    self.console_click();
                }
            }
            WindowEvent::MouseInput { state, button, .. } => {
                // **With a grenade in hand the mouse means throw and toss.**
                // Left is the full overhand throw, right is the short underhand
                // lob — the two the server has always known about, now on the
                // two buttons a hand is already on rather than on `G` and `H`.
                //
                // This is why selecting a grenade *equips* it rather than merely
                // readying one: a global right-click toss would take the scope
                // away from the sniper, whose whole identity is that scope.
                if self.focused && !self.editing() && self.utility.equipped() {
                    if state == ElementState::Pressed {
                        match button {
                            MouseButton::Left => self.utility.press(false),
                            MouseButton::Right => self.utility.press(true),
                            _ => {}
                        }
                    }
                } else if button == MouseButton::Right && self.focused {
                    if state == ElementState::Pressed {
                        self.cycle_scope();
                    }
                } else if button == MouseButton::Left {
                    if !self.focused {
                        // The click that captures the pointer must not also be a
                        // shot: you clicked on a window, not on a player.
                        if state == ElementState::Pressed {
                            self.set_grab(true);
                        }
                    } else if self.editing() {
                        // The trigger is a drag here. A press that does not move
                        // is a selection — `end_drag` returns nothing for it —
                        // so click-to-select and drag-to-reshape are the same
                        // gesture distinguished by what it did, not by a
                        // modifier the user has to remember.
                        if state == ElementState::Pressed {
                            if let Some(editor) = self.editor.as_mut() {
                                if let Some(hit) = editor.hover {
                                    editor.select_at(&hit);
                                    editor.begin_drag(&hit);
                                }
                            }
                        } else if let Some(request) =
                            self.editor.as_mut().and_then(|e| e.end_drag())
                        {
                            self.submit_edit(request);
                        }
                    } else {
                        self.keys.fire = state == ElementState::Pressed;
                    }
                }
            }
            // Tracked rather than read off the key event, because winit reports
            // modifier state on its own event and a `KeyboardInput` carries no
            // modifiers at all. The only consumer is the console's `^F`.
            WindowEvent::ModifiersChanged(modifiers) => {
                self.modifiers = modifiers.state();
            }
            WindowEvent::KeyboardInput { event, .. } => {
                let down = event.state == ElementState::Pressed;
                let typed = event.text.clone();
                if let PhysicalKey::Code(code) = event.physical_key {
                    // The console takes the keyboard before the menu does, and
                    // before the game: it is the innermost thing on screen, and
                    // Escape has to unwind from the inside out or it closes the
                    // wrong layer.
                    if self.console.open {
                        if down {
                            self.console_key(code, typed.as_deref());
                        }
                        return;
                    }
                    // The menu takes the keyboard whole while it is up. Letting
                    // movement keys through would leave you walking into a wall
                    // while reading a settings page, and the key that closes the
                    // menu would also be a key that does something in the game.
                    if self.menu.open {
                        if down {
                            self.menu_key(code, event_loop);
                        }
                        return;
                    }
                    // The summary card, which is reached *from* the menu and
                    // replaces it. Two keys and nothing else: Escape unwinds one
                    // layer like everywhere else in this client, and Enter is the
                    // only thing that actually leaves. Every other key is
                    // swallowed rather than falling through — a card you can
                    // walk around behind is a card that is not really up.
                    if self.summary.open {
                        if down {
                            match code {
                                KeyCode::Escape => {
                                    self.summary.close();
                                    self.set_grab(true);
                                }
                                KeyCode::Enter | KeyCode::NumpadEnter => {
                                    self.leave_and_exit(event_loop);
                                }
                                _ => {}
                            }
                        }
                        return;
                    }
                    // A key the console has bound runs its command instead of
                    // whatever the game would have done with it. Checked before
                    // the match so a bind can shadow a default — which is the
                    // only thing a bind is *for*.
                    if down {
                        if let Some(cmd) = self.console.bound(&key_name(code)) {
                            self.run_console(&cmd);
                            return;
                        }
                    }
                    if self.editing() {
                        match code {
                            // Movement is shared with the game's keys — the
                            // struct is the same, only what reads it differs —
                            // so only the verbs edit mode adds are here.
                            KeyCode::KeyZ if down => self.edit_job(EditJob::Undo),
                            KeyCode::KeyY if down => self.edit_job(EditJob::Redo),
                            KeyCode::KeyR if down => self.edit_job(EditJob::Save(String::new())),
                            KeyCode::Delete if down => {
                                if let Some(Selection::Brush(index)) =
                                    self.editor.as_ref().map(|e| e.selection)
                                {
                                    self.submit_edit(EditRequest::Remove { index });
                                }
                            }
                            KeyCode::KeyE if down => self.place_selected_entity(),
                            KeyCode::BracketLeft if down => self.nudge_height(-1),
                            KeyCode::BracketRight if down => self.nudge_height(1),
                            _ => {}
                        }
                    }
                    match code {
                        KeyCode::KeyW => self.keys.forward = down,
                        KeyCode::KeyS => self.keys.back = down,
                        KeyCode::KeyA => self.keys.left = down,
                        KeyCode::KeyD => self.keys.right = down,
                        KeyCode::Space => self.keys.jump = down,
                        KeyCode::ShiftLeft => self.keys.crouch = down,
                        KeyCode::Tab => self.keys.scores = down,
                        KeyCode::KeyR if down => self.reload(),
                        // Inspect. Purely local — see `WeaponViewModel::inspect`
                        // — so it is not a command and never touches the wire.
                        KeyCode::KeyF if down => self.viewmodel.inspect(),
                        // The number row picks a weapon. `Digit1` is the knife,
                        // matching the server's slot order — which is the order
                        // `GET /api/hassault/weapons` serves them in, so the two
                        // cannot drift.
                        KeyCode::Digit1 if down => self.select_weapon(0),
                        KeyCode::Digit2 if down => self.select_weapon(1),
                        KeyCode::Digit3 if down => self.select_weapon(2),
                        KeyCode::Digit4 if down => self.select_weapon(3),
                        KeyCode::Digit5 if down => self.select_weapon(4),
                        // The four grenades sit on the number row after the
                        // weapons, where a hand already is, and match
                        // `DEFAULT_CONTROLS` in the browser's `controls.ts`:
                        // one game, one set of keys.
                        //
                        // **Selecting equips.** The weapon goes down and the two
                        // mouse buttons become throw and toss. Picking a grenade
                        // and choosing the moment are still two decisions — the
                        // second one is now a click rather than a second key on
                        // the other side of the keyboard.
                        KeyCode::Digit6 if down => self.utility.equip(0),
                        KeyCode::Digit7 if down => self.utility.equip(1),
                        KeyCode::Digit8 if down => self.utility.equip(2),
                        KeyCode::Digit9 if down => self.utility.equip(3),
                        // Still bound, and still working: a player who has
                        // learned these should not lose them because the default
                        // moved. `KeyG` is where every shooter since Half-Life
                        // has put it, and the underhand gets a modifier-free key
                        // of its own rather than being Shift+G — a throw you have
                        // to hold two keys for is one you will fumble under fire.
                        KeyCode::KeyG if down => self.utility.press(false),
                        KeyCode::KeyH if down => self.utility.press(true),
                        // F3 steps the *fallback*, and clears any override so
                        // the key the player is pressing is the thing they see.
                        // Stepping a private field while `net.graph` sat unread
                        // beside it is the exact divergence this work is about.
                        KeyCode::F3 if down => {
                            self.net_graph_default = (self.net_graph() + 1) % 4;
                            self.cvars
                                .set("net.graph", serde_json::json!(self.net_graph_default));
                        }
                        KeyCode::Backquote if down => self.toggle_console(),
                        // Escape opens the menu — and releases the pointer on
                        // the way, because the reflex it has to serve first is
                        // still "give me my mouse back". A client that exited
                        // the match instead is one nobody presses Escape in
                        // twice.
                        KeyCode::Escape if down => self.toggle_menu(),
                        _ => {}
                    }
                }
            }
            WindowEvent::RedrawRequested => {
                // Built before the renderer is borrowed: `self.hud.build` and
                // `self.viewmodel.vertices` both read the rest of `self`, and
                // holding `&mut self.renderer` across them borrows it twice.
                // The dummies are bodies like any other — which is exactly why
                // the range hands them back as `PlayerRow`s. Offline
                // `self.players` is empty (nobody is sending snapshots), so a
                // build that only read it would draw an empty range and make the
                // whole mode look broken.
                let dummies;
                let rows: &[PlayerRow] = if self.socket.is_none() {
                    dummies = self.range.rows();
                    &dummies
                } else {
                    // `drawn`, never `players`: the roster is where the server
                    // last said everyone was, which is a different question from
                    // where they should be shown this frame.
                    &self.drawn
                };
                // The box rig is the fallback, not a second layer: drawing both
                // puts two overlapping characters on every player. The skinned
                // path still contributes here — the operator carries no weapon,
                // so the prop in its hand rides the untextured stream.
                let mut verts = match self.squad.as_ref() {
                    Some(squad) => held::build(squad.poses()),
                    None => bodies::build(rows, &self.self_id, &self.hitbox),
                };
                // Into the same opaque stream as the bodies: a grenade is a
                // small solid object and wants no pass of its own. The clouds do
                // — see `volume_verts` below.
                self.nades.vertices(&mut verts);
                // Same stream and same reason: an item is a small solid object.
                self.items.vertices(&mut verts);
                if self.show_hitboxes() {
                    // Appended to the same stream, so the wireframes are depth
                    // tested against the world like everything else: a hitbox
                    // behind a wall is hidden by the wall. Drawn in their own
                    // always-on-top pass they would be a wall hack.
                    verts.extend(bodies::build_hitboxes(rows, &self.self_id, &self.hitbox));
                }
                self.volume_verts.clear();
                // **Written first, deliberately.** This pass has a fixed vertex
                // budget shared with the clouds, the tracers and the water, and
                // an overflow only reaches `divergence::note_overflow` — so if
                // it ever bites, it should be the newest transient effects that
                // are dropped rather than the world's record of the fight.
                self.decals.quads(|position, rgb, alpha| {
                    self.volume_verts.push(VolumeVertex {
                        position,
                        // Flat-shaded, so a mark reads the same on every face
                        // rather than being lit as though it were a surface of
                        // its own sitting a millimetre off the wall.
                        normal: [0.0, 0.0, 1.0],
                        color: [rgb[0], rgb[1], rgb[2], alpha],
                        mode: MODE_FLAT,
                    });
                });
                if let Some(preview) = &self.throw_arc {
                    let out = &mut self.volume_verts;
                    arc::arc_vertices(preview, &mut |position, rgb, alpha| {
                        out.push(VolumeVertex {
                            position,
                            // Flat-shaded: the arc is a readout, not a surface,
                            // and lighting it would make the far half of a
                            // curve darker than the near half for no reason a
                            // player could act on.
                            normal: [0.0, 0.0, 1.0],
                            color: [rgb[0], rgb[1], rgb[2], alpha],
                            mode: MODE_FLAT,
                        });
                    });
                }
                nades::volume_vertices(&self.nades, &mut self.volume_verts);
                // The live camera, not the shot's origin: a beam is clipped
                // clear of wherever the eye got to this frame. See `EYE_CLEAR`.
                self.effects.vertices(
                    &mut self.volume_verts,
                    [self.camera.x, self.camera.y, self.camera.z],
                );
                // The water plane rides the translucent pass with the clouds —
                // it is the one piece of the *map* that is not opaque. On the
                // same clock as the reveal, so the ripple does not restart when
                // something else does.
                hassault_native::water::vertices(&self.world, self.elapsed, &mut self.volume_verts);
                // The designer's markers, ghosts and lint cells, into the same
                // translucent pass. Last, so a wireframe reads over the water it
                // is standing in rather than under it.
                if let Some(editor) = &self.editor {
                    editor.overlay(&self.world, &mut self.volume_verts);
                }
                self.viewmodel.vertices(&mut self.weapon_verts);
                // Built here rather than in the painter: sorting is a game-mode
                // question and the painter has no business having an opinion.
                // `None` when the key is not held, which is a different fact
                // from "an empty match" and must not draw the same.
                let scoreboard = self.keys.scores.then(|| self.score_rows());
                let (width, height) = self.renderer.as_ref().map(|r| r.size()).unwrap_or((1, 1));
                // **A copy, and only for the draw.** `self.camera` is what
                // `view_angles` puts on the wire and what a shot's ray is built
                // from; the shake exists on the way to the GPU and nowhere else.
                // `active` keeps the common case — no trauma at all — at exactly
                // the cost it had before.
                //
                // Computed here rather than at the `render` call below because
                // the damage numbers project through it too, and they have to
                // use *the same* camera the world does: projected through the
                // true one they would sit still while the body underneath them
                // shook, which reads as the number belonging to the HUD.
                let drawn_camera = if self.shake.active() {
                    self.camera.shaken(&self.shake)
                } else {
                    self.camera
                };
                self.damage
                    .placed(&drawn_camera, width, height, &mut self.damage_placed);
                // The name and the cone are copied out rather than borrowed:
                // `self.overlay` is taken mutably below, and a `HudView` holding
                // a `&str` into `self.weapons` would borrow `self` twice.
                let held = self.held();
                let weapon_name = held.map(|w| w.name.clone()).unwrap_or_default();
                // The cone the **next** shot actually gets, which is
                // `residual_cone` and not `spread`/`hipfire_spread`.
                //
                // Those two are the cone a weapon *without* a pattern uses. A
                // patterned weapon is aimed by `apply_spray` first and then
                // scattered by the much tighter `residual_spread` — the server
                // passes exactly that to `resolve_shot` for **every** shot, not
                // only for the ones after the first. Drawing the wide number
                // here made the rifle's reticle 5× wider than its own cone
                // (0.021 against 0.004), which reads as "this gun is
                // inaccurate" when it is the drawing that is wrong. Nothing
                // errored, because both numbers are real numbers about the same
                // weapon.
                //
                // The reticle still *moves* with the burst: recoil walks the
                // camera through the same pattern the server aims by
                // (`apply_match_recoil`), so the crosshair follows the shots
                // rather than opening around them. That is what makes a pattern
                // learnable, and it is why this is a cone question and not a
                // spread-over-time one. `residual_cone` keeps the hip-fire
                // penalty for the sniper, which has no pattern.
                let spread = held.map(|w| w.residual_cone(self.scoped)).unwrap_or(0.0);
                let magnification = self.magnification();
                let mut overlay = std::mem::take(&mut self.overlay);
                // Filtered here rather than in the painter: the rule that keeps
                // an unspotted enemy off the radar is the one part of this that
                // would be a cheat if it were wrong, and a painter is not a
                // place anything can test it. See `radar::blips`.
                //
                // Drawn from `drawn`, not `players`: the roster is where the
                // server last said everyone was, which is a different question
                // from where they should be shown this frame — and a radar that
                // stepped 20 times a second while the bodies moved smoothly
                // would disagree with the screen it sits on.
                self.blips.clear();
                if let Some(you) = &self.you {
                    self.blips.extend(radar::blips(
                        &self.drawn,
                        &self.self_id,
                        self.my_team(),
                        &you.spotted,
                    ));
                }
                // Centred on the camera, which is the prediction — where we
                // believe we are — and not on the last snapshot. Anything else
                // makes your own arrow lag your own movement.
                let radar = self.joined.then(|| RadarView {
                    plan: &self.radar_plan,
                    x: self.camera.x,
                    y: self.camera.y,
                    yaw: self.camera.yaw.to_radians(),
                    blips: &self.blips,
                });
                // Built before the view, not inside it: each of these is an
                // owned value the borrowed `ConsoleView` points at, and one
                // built inside the closure would not outlive the expression. All
                // three are empty when the console is closed, which costs
                // nothing — an empty `Vec` does not allocate — and keeps the
                // common case off the scrollback entirely.
                let (console_lines, console_quick, console_detail) = if self.console.open {
                    (
                        self.console.visible(),
                        self.console.quick_actions(&self.cvars),
                        self.console.suggestion_detail(&self.cvars),
                    )
                } else {
                    (Vec::new(), Vec::new(), None)
                };
                // The pouch, built here rather than borrowed out of the
                // controller: the HUD wants a name and a count per slot, and
                // handing it the controller would make the painter ask the
                // predictor questions.
                let utility = (!self.utility.catalogue().is_empty()).then(|| UtilityView {
                    slots: self
                        .utility
                        .catalogue()
                        .iter()
                        .enumerate()
                        .map(|(i, spec)| UtilitySlot {
                            name: spec.name.clone(),
                            kind: spec.kind.clone(),
                            count: self.utility.count_of(i),
                        })
                        .collect(),
                    selected: self.utility.selected(),
                });
                let view = HudView {
                    hud_scale: self.settings.video.hud_scale,
                    radar,
                    utility: utility.as_ref(),
                    // Straight off the snapshot. Parsed and then ignored until
                    // now, which made the flashbang the one grenade with no
                    // effect on the person it went off in front of.
                    flash: self.you.as_ref().map(|y| y.flash).unwrap_or(0.0),
                    crosshair: self.crosshair(),
                    console: self.console.open.then(|| ConsoleView {
                        lines: &console_lines,
                        input: &self.console.input,
                        cursor: self.console.cursor,
                        scroll: self.console.scroll,
                        suggestions: &self.console.suggestions,
                        suggestion: self.console.suggestion,
                        detail: console_detail.as_deref(),
                        registry_loaded: !self.console.definitions().cvars.is_empty(),
                        filter: self.console.filter.label(),
                        hidden: self.console.hidden_count(),
                        room: &self.room,
                        map: &self.map_name,
                        rtt: self.ping.rtt(),
                        cheats: self.console.server_bool("server.cheats"),
                        quick: &console_quick,
                    }),
                    width,
                    height,
                    you: self.you.as_ref(),
                    weapon_name: &weapon_name,
                    // The cone the *next* shot would use. This client has no
                    // scope yet, so it is always the hip-fire one — which is the
                    // honest reading either way: the crosshair must never be
                    // narrower than the shot it describes.
                    spread,
                    // Served, never inferred from the largest `reloadIn` seen:
                    // the arc has to be right on the first reload of a match.
                    reload_time: self.held().map(|w| w.reload_time).unwrap_or(0.0),
                    magnification,
                    speed: self.ground_speed(),
                    move_speed: MOVE_SPEED,
                    yaw: self.camera.yaw.to_radians(),
                    on_ground: self.prediction.state.on_ground,
                    crouching: self.prediction.state.crouch > 0.5,
                    // From the predicted body, not from the snapshot: this is
                    // the same test the simulation runs to take the jump away,
                    // and reading it a tick late would tint the screen after the
                    // jump had already stopped working.
                    underwater: physics::submerged(&self.world, &self.prediction.state),
                    playing: self.joined,
                    rtt: self.ping.rtt(),
                    fps: if self.fps > 0.0 { Some(self.fps) } else { None },
                    net_graph: self.net_graph(),
                    scoreboard: scoreboard.as_deref(),
                    scores: &self.scores,
                    damage: &self.damage_placed,
                };
                self.hud.build(&view, &mut overlay);
                // After the HUD, so the scrim covers it: the menu is *over* the
                // game, and a crosshair drawn on top of a settings panel reads as
                // the panel being transparent rather than the game being behind
                // it.
                self.menu.build(
                    &self.settings,
                    self.socket.is_some(),
                    view.width as f32,
                    view.height as f32,
                    &mut overlay,
                );
                // Last, so it is over everything: the card is the only thing on
                // screen once it is up, and the menu it was reached from has
                // already closed itself.
                if self.summary.open {
                    let summary = self.build_summary();
                    self.summary.build(
                        &summary,
                        view.width as f32,
                        view.height as f32,
                        &mut overlay,
                    );
                }
                self.overlay = overlay;

                let Some(renderer) = &mut self.renderer else {
                    return;
                };
                renderer.set_bodies(&verts);
                renderer.set_volumes(&self.volume_verts);
                renderer.set_reveal(self.reveal);
                if let Some(squad) = self.squad.as_ref() {
                    renderer.set_characters(squad.poses());
                }
                renderer.set_viewmodel(&self.weapon_verts);
                renderer.set_overlay(&self.overlay);
                // `Ok(false)` is a frame that did not happen — minimised,
                // occluded, or a surface that has moved on. Routine, and already
                // handled inside; there is nothing to report and nothing to stop.
                // The shaken copy built above, shared with the damage numbers.
                match renderer.render(&drawn_camera) {
                    // Only a frame that actually presented counts. Occluded and
                    // minimised frames return early, and counting them would make
                    // the fps readout report work that never happened — the one
                    // number in this client that has to be trustworthy.
                    Ok(true) => self.frames += 1,
                    Ok(false) => {}
                    Err(e) => {
                        eprintln!("hassault: {e}");
                        event_loop.exit();
                    }
                }
            }
            _ => {}
        }
    }

    fn device_event(&mut self, _event_loop: &ActiveEventLoop, _id: DeviceId, event: DeviceEvent) {
        // **The raw delta.** Not `CursorMoved`, which is a position on a pixel
        // grid the OS has already accelerated — the difference is the reason this
        // client exists, and taking the convenient one would quietly give up the
        // whole argument for it.
        if let DeviceEvent::MouseMotion { delta } = event {
            if self.focused {
                self.look(delta.0 as f32, delta.1 as f32);
            }
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        // Before the network pump, not after: the socket is drained on the way
        // *out* of the wait, so this frame is built from snapshots that arrived
        // during it. Sleeping after the pump would hold every snapshot that
        // landed mid-sleep until the next iteration — a whole cap period of
        // added latency on the one thing in this loop that came from the server.
        self.wait_for_frame();
        self.pump_network(event_loop);

        let now = Instant::now();
        let dt = now.duration_since(self.last_frame).as_secs_f32();
        self.last_frame = now;
        // Resampled every frame, before the input: the shot this frame may fire
        // is aimed at what is on screen, so what is on screen has to be decided
        // first. See `interp.rs`.
        self.drawn = self.snapshots.sample(Self::clock_ms(), &self.self_id);
        if self.editing() {
            // No command goes on the wire and none is predicted: edit
            // mode has no socket and no body. The camera is the whole
            // simulation.
            self.drain_edits();
            self.fly_and_pick(dt);
        } else {
            self.send_input(dt);
        }
        // After the input, so the weapon sways to the angles this frame is about
        // to be drawn with rather than to the previous one's.
        self.animate(dt.clamp(0.0, MAX_DT));

        let elapsed = now.duration_since(self.fps_since).as_secs_f32();
        if elapsed >= 0.5 {
            self.fps = self.frames as f32 / elapsed;
            self.frames = 0;
            self.fps_since = now;
            self.pending = self.prediction.pending();
            self.update_title();
        }

        if let Some(window) = &self.window {
            window.request_redraw();
        }
    }
}

// ---- the edit worker ---------------------------------------------------------------
//
// `NodeApi` is blocking HTTP on purpose — three requests stand a world up, and a
// tokio runtime for three requests would be absurd. That choice is fine until a
// request happens *per gesture*, which is what the designer does: an edit is a
// PATCH, a compile, a map fetch and a cube fetch, and doing that on the frame
// thread is a visible hitch every time you let go of the mouse.
//
// So edits go to a thread, exactly the way `prop::preload` sends parsed weapon
// props back over a channel. The frame thread never waits; it drains whatever
// finished.

/// What the frame thread asks for.
enum EditJob {
    Patch(serde_json::Value),
    Undo,
    Redo,
    Save(String),
}

/// What comes back. A successful edit carries the **whole rebuilt world**, not a
/// diff: the compiler lives on the node, and a client that patched its own grid
/// would be a second implementation of `mapsource.build` free to disagree with
/// the one the server serves.
enum EditOutcome {
    Applied {
        doc: serde_json::Value,
        lint: Vec<LintFinding>,
        world: Box<World>,
        owners: Vec<u16>,
    },
    Failed(String),
}

struct EditJobs {
    send: std::sync::mpsc::Sender<EditJob>,
    recv: std::sync::mpsc::Receiver<EditOutcome>,
}

fn spawn_edit_worker(server: &str, draft_id: String) -> EditJobs {
    let (job_tx, job_rx) = std::sync::mpsc::channel::<EditJob>();
    let (out_tx, out_rx) = std::sync::mpsc::channel::<EditOutcome>();
    let base = server.to_string();
    std::thread::spawn(move || {
        let node = NodeApi::new(&base);
        while let Ok(job) = job_rx.recv() {
            let result = match job {
                EditJob::Patch(body) => node.edit_draft(&draft_id, body),
                EditJob::Undo => node.undo_draft(&draft_id),
                EditJob::Redo => node.redo_draft(&draft_id),
                EditJob::Save(name) => match node.save_draft(&draft_id, &name) {
                    // A save changes no geometry, so it reports through the same
                    // channel without a rebuild — the status line is the whole
                    // point of it.
                    Ok(saved) => {
                        let _ = out_tx.send(EditOutcome::Failed(format!(
                            "saved as {}{}",
                            saved.name,
                            if saved.lint.is_empty() {
                                String::new()
                            } else {
                                format!(" ({} findings)", saved.lint.len())
                            }
                        )));
                        continue;
                    }
                    Err(e) => Err(e),
                },
            };
            let sent = match result {
                Ok(draft) => match refetch(&node, &draft) {
                    Ok((world, owners)) => out_tx.send(EditOutcome::Applied {
                        doc: draft.doc,
                        lint: draft.lint,
                        world: Box::new(world),
                        owners,
                    }),
                    Err(e) => out_tx.send(EditOutcome::Failed(e)),
                },
                Err(e) => out_tx.send(EditOutcome::Failed(describe_api_error(&e))),
            };
            if sent.is_err() {
                // The window is gone. Nothing left to answer.
                break;
            }
        }
    });
    EditJobs {
        send: job_tx,
        recv: out_rx,
    }
}

/// Reload the draft as a map. The same two calls that stand a world up at
/// startup, which is exactly why there is no third code path here.
fn refetch(
    node: &NodeApi,
    draft: &hassault_native::api::DraftInfo,
) -> Result<(World, Vec<u16>), String> {
    let info = node
        .map_info(&draft.map_name)
        .map_err(|e| describe_api_error(&e))?;
    let expected = info.cubic_size * info.plane_order.len();
    let cubic = info.cubic_size;
    let cubes = node
        .map_cubes(&draft.map_name, expected)
        .map_err(|e| describe_api_error(&e))?;
    let world = World::new(info, &cubes).map_err(|e| e.to_string())?;
    let owners = node
        .draft_owners(&draft.id, cubic)
        .ok()
        .and_then(|bytes| editor::decode_owners(&bytes, cubic))
        .unwrap_or_default();
    Ok((world, owners))
}

/// A status line a mapper can act on.
///
/// The status codes carry real meaning here and flattening them would throw it
/// away: a 409 from a save means "that map exists, pass overwrite", and a 422
/// means "this document will not build" — different problems with different
/// answers.
fn describe_api_error(error: &hassault_native::api::ApiError) -> String {
    match error {
        hassault_native::api::ApiError::Status(409, _) => {
            "that map already exists — save under another name".to_string()
        }
        hassault_native::api::ApiError::Status(422, _) => {
            "that edit would not build — the map is unchanged".to_string()
        }
        hassault_native::api::ApiError::Status(400, _) => {
            "that edit was refused — the map is unchanged".to_string()
        }
        other => format!("{other}"),
    }
}

/// The `rect` of every brush, in document order, so a pick's owner index lands
/// on the right one.
///
/// A brush with no readable rect still takes a slot. Indices are the whole
/// contract between this and the node — dropping a malformed brush would shift
/// every later one and silently point a drag at its neighbour.
fn brush_rects(doc: &serde_json::Value) -> Vec<[i32; 4]> {
    doc.get("brushes")
        .and_then(|b| b.as_array())
        .map(|brushes| {
            brushes
                .iter()
                .map(|brush| {
                    let mut rect = [0i32; 4];
                    if let Some(values) = brush.get("rect").and_then(|r| r.as_array()) {
                        for (slot, value) in rect.iter_mut().zip(values) {
                            *slot = value.as_i64().unwrap_or(0) as i32;
                        }
                    }
                    rect
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Each brush's op, in document order and parallel to `brush_rects`.
fn brush_ops(doc: &serde_json::Value) -> Vec<String> {
    doc.get("brushes")
        .and_then(|b| b.as_array())
        .map(|brushes| {
            brushes
                .iter()
                .map(|brush| {
                    brush
                        .get("op")
                        .and_then(|op| op.as_str())
                        .unwrap_or_default()
                        .to_string()
                })
                .collect()
        })
        .unwrap_or_default()
}

/// The cells the lint complained about, for painting on the floor. Errors only —
/// a warning is worth a line of text, not a red floor.
fn problem_cells(lint: &[LintFinding]) -> Vec<(i32, i32)> {
    lint.iter()
        .filter(|f| f.severity == "error")
        .flat_map(|f| f.cells.iter().map(|cell| (cell[0], cell[1])))
        .collect()
}

fn describe_lint(lint: &[LintFinding]) -> String {
    if lint.is_empty() {
        return "clean".to_string();
    }
    let errors = lint.iter().filter(|f| f.severity == "error").count();
    match lint.first() {
        Some(first) if errors > 0 => format!("{errors} errors — {}", first.message),
        Some(first) => format!("{} warnings — {}", lint.len(), first.message),
        None => "clean".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hassault_native::api::{Entity, MapInfo};
    use hassault_native::geometry::build_world_mesh;

    /// A 4×4 room with a floor at 0 and one spawn in the middle of it.
    ///
    /// Built plane by plane in the wire order, like the conformance fixture's
    /// worlds, because `World::new` slices by the order the server *reports* and
    /// refuses a plane it does not know.
    fn training_world() -> World {
        let ssize = 4;
        let n = (ssize * ssize) as usize;
        let mut bytes = Vec::with_capacity(n * 9);
        bytes.extend(std::iter::repeat_n(2u8, n)); // type: SPACE
        bytes.extend(std::iter::repeat_n(0u8, n)); // floor
        bytes.extend(std::iter::repeat_n(16u8, n)); // ceil
        bytes.extend(std::iter::repeat_n(0u8, n * 6)); // wtex..tag
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: [
                "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
            ]
            .iter()
            .map(|s| s.to_string())
            .collect(),
            entities: vec![Entity {
                // `playerstart`, matching `cgz.ENTITY_NAMES`.
                kind: 2,
                name: "playerstart".into(),
                x: 1.0,
                y: 1.0,
                // Deliberately absurd: a `playerstart`'s z is the mapper's eye at
                // placement time, and AC's editor flies. Taking it literally is
                // what put all 1741 official spawns in mid-air.
                z: 19.0,
                yaw: 90.0,
                attrs: vec![0, 0],
            }],
            ..Default::default()
        };
        World::new(info, &bytes).expect("training world")
    }

    /// An open room `ssize` cubes square with the spawn near the middle.
    ///
    /// `training_world` is 4x4 — a closet, which is all most of these tests need.
    /// Anything that measures a *direction* needs somewhere to walk: against a
    /// wall the collision resolve slides the body along it, and the bearing it
    /// travelled is then the wall's, not the camera's.
    fn open_world(ssize: i32) -> World {
        let n = (ssize * ssize) as usize;
        let mut bytes = Vec::with_capacity(n * 9);
        bytes.extend(std::iter::repeat_n(2u8, n)); // type: SPACE
        bytes.extend(std::iter::repeat_n(0u8, n)); // floor
        bytes.extend(std::iter::repeat_n(16u8, n)); // ceil
        bytes.extend(std::iter::repeat_n(0u8, n * 6)); // wtex..tag
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: [
                "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
            ]
            .iter()
            .map(|s| s.to_string())
            .collect(),
            entities: vec![Entity {
                // `playerstart`, matching `cgz.ENTITY_NAMES`.
                kind: 2,
                name: "playerstart".into(),
                x: (ssize / 2) as f32,
                y: (ssize / 2) as f32,
                z: 19.0,
                yaw: 90.0,
                attrs: vec![0, 0],
            }],
            ..Default::default()
        };
        World::new(info, &bytes).expect("open world")
    }

    fn app_on(world: World) -> App {
        let mesh = build_world_mesh(&world);
        App::new(
            world,
            mesh,
            None,
            Settings::default(),
            SettingsWriter::disabled(),
            weapons(),
            tacticals(),
            // No throw constants: these tests are about the match loop, and a
            // client that was served none draws no trajectory preview — which is
            // the honest default rather than one integrated with zeros.
            None,
            HashMap::new(),
            Default::default(),
            // No item table: these tests are about the match loop, and a range
            // that gives nothing is the honest default for one.
            Default::default(),
            Default::default(),
            Vec::new(),
        )
    }

    /// The grenades the node serves. Four, in slot order, because the wire
    /// carries a slot index and a test that shortened the list would be testing
    /// a different keyboard than the one players have.
    fn tacticals() -> Vec<TacticalSpec> {
        ["he", "flash", "smoke", "molotov"]
            .into_iter()
            .map(|id| TacticalSpec {
                id: id.to_string(),
                name: id.to_uppercase(),
                kind: if id == "molotov" { "fire" } else { id }.to_string(),
                carried: 1,
                ..Default::default()
            })
            .collect()
    }

    /// The loadout the node serves, trimmed to what these tests read.
    fn weapons() -> Vec<WeaponSpec> {
        vec![
            WeaponSpec {
                id: "knife".into(),
                name: "knife".into(),
                ..Default::default()
            },
            WeaponSpec {
                id: "assault".into(),
                name: "assault rifle".into(),
                mag: 30,
                hipfire_spread: 0.02,
                ..Default::default()
            },
            WeaponSpec {
                id: "sniper".into(),
                name: "sniper rifle".into(),
                mag: 5,
                spread: 0.001,
                hipfire_spread: 0.027,
                zoom_levels: vec![2.0, 4.0],
                ..Default::default()
            },
        ]
    }

    /// An app holding the weapon in `slot`, with the pointer captured.
    fn holding(slot: i32) -> App {
        let mut app = training_app();
        app.focused = true;
        app.you = Some(SelfState {
            weapon: slot,
            alive: true,
            ..Default::default()
        });
        app.animate(0.016);
        app
    }

    fn training_app() -> App {
        let world = training_world();
        let mesh = build_world_mesh(&world);
        App::new(
            world,
            mesh,
            None,
            Settings::default(),
            // Nothing to write to: a test must not depend on a node being up,
            // and a settings write is fire-and-forget by design.
            SettingsWriter::disabled(),
            weapons(),
            tacticals(),
            // No throw constants: these tests are about the match loop, and a
            // client that was served none draws no trajectory preview — which is
            // the honest default rather than one integrated with zeros.
            None,
            HashMap::new(),
            // The shipped body. A test that fetched one would depend on a node.
            Default::default(),
            // No item table either, for the same reason. A range with one is
            // covered by `training.rs`'s own tests, which need no App at all.
            Default::default(),
            Default::default(),
            Vec::new(),
        )
    }

    /// Drive `send_input` at `fps` for one real second and report the simulated
    /// time claimed and the number of commands produced.
    fn spin(app: &mut App, fps: u32) -> (f32, u64) {
        let before_t = app.prediction.state.t;
        let before_seq = app.local_seq;
        let dt = 1.0 / fps as f32;
        for _ in 0..fps {
            app.send_input(dt);
        }
        (
            app.prediction.state.t - before_t,
            app.local_seq - before_seq,
        )
    }

    #[test]
    fn the_view_angles_are_radians() {
        // The units the *simulation* uses, which is what both the wire and the
        // prediction consume. The camera is degrees; nothing downstream is.
        let mut app = training_app();
        app.camera.yaw = 315.0;
        app.camera.pitch = 20.0;
        let (yaw, pitch) = app.view_angles();
        assert!((yaw - 315f32.to_radians()).abs() < 1e-6, "yaw {yaw}");
        assert!((pitch - 20f32.to_radians()).abs() < 1e-6, "pitch {pitch}");
        // The bug, stated as the assertion that would have caught it: a heading
        // handed over as 315 rather than 5.5 is not a rounding difference.
        assert!(yaw < std::f32::consts::TAU, "degrees leaked through: {yaw}");
    }

    #[test]
    fn walking_forward_goes_where_the_camera_points() {
        // At **every** heading, not just along +x. Every other test in this
        // crate aims at yaw 0, which is the one angle at which degrees and
        // radians happen to agree — so the units bug was invisible to all of
        // them and to the conformance fixture alike.
        for heading in [0.0f32, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0] {
            let mut app = app_on(open_world(48));
            app.camera.yaw = heading;
            app.keys.forward = true;
            let (x0, y0) = (app.prediction.state.x, app.prediction.state.y);
            for _ in 0..20 {
                app.send_input(1.0 / 60.0);
            }
            let (dx, dy) = (app.prediction.state.x - x0, app.prediction.state.y - y0);
            assert!(
                dx.hypot(dy) > 0.05,
                "did not move at heading {heading}: ({dx}, {dy})"
            );
            // The bearing actually travelled, against the one asked for.
            let want = heading.to_radians();
            let got = dy.atan2(dx);
            let off = (got - want).sin().atan2((got - want).cos()).abs();
            assert!(
                off < 0.02,
                "at heading {heading} the body travelled {:.1} degrees off",
                off.to_degrees()
            );
        }
    }

    #[test]
    fn a_fast_loop_claims_no_more_time_than_actually_passed() {
        // The bug this pins. `dt` used to be clamped up to a 1 ms floor, so an
        // uncapped loop — which is what this client runs, `Immediate` present
        // mode with vsync off — claimed a millisecond for every iteration no
        // matter how short it was. At 4000 iterations a second that is four
        // simulated seconds per real one, and the server grants 1.1; the
        // prediction ends up permanently ahead of the authoritative position and
        // every snapshot drags it back. It reads as being unable to walk.
        for fps in [60, 144, 500, 1000, 4000, 20_000] {
            let mut app = training_app();
            app.keys.forward = true;
            let (simulated, _) = spin(&mut app, fps);
            assert!(
                (simulated - 1.0).abs() < 0.02,
                "at {fps} fps the client claimed {simulated:.3} s for one real second"
            );
        }
    }

    #[test]
    fn the_command_rate_is_the_frame_rate_only_while_it_is_slower() {
        // Below `INPUT_HZ` a command is still one frame — the accumulator never
        // withholds time that has already passed, it only refuses to *round up*.
        let mut app = training_app();
        app.keys.forward = true;
        let (_, commands) = spin(&mut app, 144);
        assert_eq!(commands, 144, "a 144 Hz loop should send 144 commands");
    }

    #[test]
    fn a_fast_loop_stays_inside_one_input_message() {
        // `MatchSocket::flush` may carry 64 commands per 33 ms window, and the
        // surplus used to be dropped *after* being predicted locally — the
        // client moving on commands the server would never see. However fast the
        // loop spins, a flush window's worth has to fit.
        let window = hassault_native::net::SEND_INTERVAL.as_secs_f32();
        let cap = hassault_native::net::MAX_COMMANDS_PER_MESSAGE as f32;
        let per_flush = |commands: u64| commands as f32 * window;
        for fps in [1000, 4000, 20_000] {
            let mut app = training_app();
            app.keys.forward = true;
            let (_, commands) = spin(&mut app, fps);
            assert!(
                per_flush(commands) <= cap,
                "at {fps} fps a flush window carries {} commands",
                per_flush(commands)
            );
        }
    }

    #[test]
    fn a_jittering_loop_claims_exactly_what_it_spent() {
        // A real frame time is not a constant, and the accumulator has to be
        // honest about a *mixture* — a burst of 20 000 fps iterations between two
        // ordinary frames must contribute the microseconds it actually took, not
        // one interval each and not nothing at all.
        let mut app = training_app();
        app.keys.forward = true;
        let mut real = 0.0;
        // Ten ordinary frames, each preceded by a burst of very short ones.
        for _ in 0..10 {
            for _ in 0..500 {
                let dt = 1.0 / 20_000.0;
                real += dt;
                app.send_input(dt);
            }
            let dt = 1.0 / 60.0;
            real += dt;
            app.send_input(dt);
        }
        let simulated = app.prediction.state.t;
        assert!(
            (simulated - real).abs() < INPUT_INTERVAL,
            "spent {real:.4} s and claimed {simulated:.4} s"
        );
    }

    #[test]
    fn train_has_a_body_without_a_server() {
        // The point of the mode: no socket, and yet a player standing somewhere
        // real. Before B4 there was no way to ask for this — the launcher's only
        // instruction was "a match on this map, or open one".
        let app = training_app();
        assert!(app.socket.is_none());
        assert!(
            app.joined,
            "training starts deployed; there is nothing to wait for"
        );
        assert!(app.prediction.live, "a body was placed");
        // On the floor, not at the entity's z: the height comes from the world.
        assert!(
            app.prediction.state.z.abs() < 0.001,
            "stood at {}",
            app.prediction.state.z
        );
        assert!(app.prediction.state.on_ground);
    }

    #[test]
    fn training_moves_without_a_socket() {
        let mut app = training_app();
        let before = app.prediction.state.x;
        app.keys.forward = true;
        // Yaw 0 looks along +x, and the spawn faces +y — so the camera is what
        // decides the direction, exactly as it does in a match.
        app.camera.yaw = 0.0;
        for _ in 0..10 {
            app.send_input(1.0 / 60.0);
        }
        assert!(
            app.prediction.state.x > before + 0.05,
            "walked from {before} to {}",
            app.prediction.state.x
        );
        // And the camera followed the body rather than the body following nothing.
        assert!((app.camera.x - app.prediction.state.x).abs() < 1e-6);
    }

    #[test]
    fn bots_are_only_queued_for_a_count_worth_adding() {
        let mut app = training_app();
        app.queue_bots(0, "normal".into());
        // A zero would be a message asking the server to add nothing, refused
        // anyway on a socket Train does not have.
        assert!(app.pending_bots.is_none());
        app.queue_bots(3, "hard".into());
        assert_eq!(app.pending_bots, Some((3, "hard".to_string())));
    }

    #[test]
    fn training_holds_a_weapon_even_though_no_server_said_so() {
        // There is no `you` in Train, so nothing names a slot. An empty hand
        // there reads as the view model having failed rather than as the mode
        // it is — and the browser's Train hands you the rifle too.
        let mut app = training_app();
        app.focused = true;
        app.animate(0.016);
        assert_eq!(app.viewmodel.weapon(), "assault");
    }

    #[test]
    fn the_weapon_slot_is_an_index_into_the_served_loadout() {
        // `you.weapon` is an index into the server's own WEAPONS, in the order
        // the route serves them. Read as anything else, every weapon draws as
        // some other weapon — and silently, since they are all valid models.
        let mut app = training_app();
        app.focused = true;
        app.you = Some(SelfState {
            weapon: 0,
            alive: true,
            ..Default::default()
        });
        app.animate(0.016);
        assert_eq!(app.viewmodel.weapon(), "knife");
    }

    #[test]
    fn a_weapon_slot_the_loadout_does_not_have_draws_nothing() {
        // A server with more weapons than this client fetched: better an empty
        // hand than a rifle standing in for something else.
        let mut app = training_app();
        app.focused = true;
        app.you = Some(SelfState {
            weapon: 99,
            alive: true,
            ..Default::default()
        });
        app.animate(0.016);
        assert_eq!(app.viewmodel.weapon(), "");
    }

    #[test]
    fn the_weapon_is_in_your_hands_whenever_you_are_in_the_world() {
        // Not gated on the pointer being captured. Releasing it here is not a
        // menu — the world is still drawn behind it — so a world with no weapon
        // in it reads as a client that half-loaded, which is exactly how the
        // missing view model was first reported.
        let mut app = training_app();
        app.animate(0.016);
        let mut verts = Vec::new();
        app.viewmodel.vertices(&mut verts);
        assert!(!verts.is_empty(), "no weapon while the pointer was free");
    }

    #[test]
    fn nothing_is_held_before_there_is_a_body() {
        // `joined` is the line, not focus: before the world places you there is
        // no player to be holding anything.
        let mut app = training_app();
        app.joined = false;
        app.animate(0.016);
        let mut verts = Vec::new();
        app.viewmodel.vertices(&mut verts);
        assert!(verts.is_empty());
    }

    #[test]
    fn ground_speed_ignores_falling() {
        // The number is about the run cap and the chained jump. Including the
        // vertical would make a long drop read as a chain boost that landed.
        let mut app = training_app();
        app.prediction.state.vel_x = 3.0;
        app.prediction.state.vel_y = 4.0;
        app.prediction.state.vel_z = -50.0;
        assert!((app.ground_speed() - 5.0).abs() < 1e-5);
    }

    #[test]
    fn the_scope_steps_through_its_magnifications_and_back_to_none() {
        // A cycle rather than a hold: the levels are discrete, and holding for 2×
        // with a different gesture for 4× is two controls for one axis.
        let mut app = holding(2);
        assert_eq!(app.magnification(), 1.0);
        app.cycle_scope();
        assert_eq!(app.magnification(), 2.0);
        app.cycle_scope();
        assert_eq!(app.magnification(), 4.0);
        app.cycle_scope();
        assert_eq!(app.scoped, 0);
        assert_eq!(app.magnification(), 1.0);
    }

    #[test]
    fn a_weapon_without_a_scope_ignores_the_button() {
        // Ignored rather than consumed, so the button stays free to mean
        // something else later.
        let mut app = holding(1);
        app.cycle_scope();
        assert_eq!(app.scoped, 0);
        assert_eq!(app.camera.fov, app.base_fov);
    }

    #[test]
    fn zooming_narrows_the_view_and_slows_the_mouse_by_the_same_number() {
        // Both, or neither. A zoom that narrowed the view without slowing the
        // mouse makes a given hand movement sweep four times as much of the
        // world at 4× — an aim that is wrong only while scoped, which is the one
        // situation the scope exists for.
        let mut app = holding(2);
        app.cycle_scope();
        app.cycle_scope();
        assert_eq!(app.camera.fov, app.base_fov / 4.0);
        let before = app.camera.yaw;
        app.look(100.0, 0.0);
        let scoped_turn = app.camera.yaw - before;
        app.unscope();
        let before = app.camera.yaw;
        app.look(100.0, 0.0);
        let hip_turn = app.camera.yaw - before;
        assert!(
            (hip_turn / scoped_turn - 4.0).abs() < 1e-3,
            "{hip_turn} vs {scoped_turn}"
        );
    }

    #[test]
    fn the_scope_drops_when_the_weapon_does() {
        // The step is an index into *this* weapon's levels. Carried across a
        // swap it is a magnification the gun in your hands does not have.
        let mut app = holding(2);
        app.cycle_scope();
        assert_eq!(app.scoped, 1);
        app.you = Some(SelfState {
            weapon: 1,
            alive: true,
            ..Default::default()
        });
        app.animate(0.016);
        assert_eq!(app.scoped, 0);
        assert_eq!(app.camera.fov, app.base_fov);
    }

    #[test]
    fn dying_unscopes() {
        let mut app = holding(2);
        app.cycle_scope();
        app.you = Some(SelfState {
            weapon: 2,
            alive: false,
            ..Default::default()
        });
        app.animate(0.016);
        assert_eq!(app.scoped, 0);
    }

    #[test]
    fn the_zoom_step_rides_on_the_command() {
        // The server reads it to pick the shot's cone, and clamps it there —
        // the wire parser cannot know which weapon the command lands on.
        let mut app = holding(2);
        app.cycle_scope();
        let mut cmd = Command::new(1);
        cmd.scoped = app.scoped;
        let json = serde_json::to_string(&cmd).unwrap();
        assert!(json.contains(r#""scoped":1"#), "{json}");
    }

    #[test]
    fn crouching_banks_no_stride_at_all() {
        // The whole reason the crouch speed penalty is a trade: 40% of your speed
        // buys arriving without being announced. Banking the distance and paying
        // for it on standing up would give it back.
        let mut app = training_app();
        app.focused = true;
        app.prediction.state.crouch = 1.0;
        app.prediction.state.vel_x = MOVE_SPEED;
        for _ in 0..120 {
            app.footsteps(1.0 / 60.0);
        }
        assert_eq!(app.stride, 0.0);
    }

    #[test]
    fn footsteps_are_paced_by_distance_covered_not_by_time() {
        // A walking player and a sprinting one make the same *stride*, not the
        // same rate — which is what makes a sprinter sound like one.
        let mut app = training_app();
        app.focused = true;
        app.prediction.state.on_ground = true;
        app.prediction.state.vel_x = MOVE_SPEED;
        app.footsteps(0.1);
        let fast = app.stride;
        app.stride = 0.0;
        app.prediction.state.vel_x = MOVE_SPEED / 4.0;
        app.footsteps(0.1);
        assert!(fast > app.stride * 3.0, "{fast} vs {}", app.stride);
    }

    #[test]
    fn an_axis_is_minus_one_zero_or_one() {
        assert_eq!(axis(true, false), 1.0);
        assert_eq!(axis(false, true), -1.0);
        // Both held is a standstill, not a preference for whichever was checked
        // first — the browser client resolves it the same way.
        assert_eq!(axis(true, true), 0.0);
        assert_eq!(axis(false, false), 0.0);
    }
}
