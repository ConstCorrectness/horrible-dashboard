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
use std::sync::Arc;
use std::time::Instant;

use winit::application::ApplicationHandler;
use winit::event::{DeviceEvent, DeviceId, ElementState, MouseButton, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow};
use winit::keyboard::{KeyCode, PhysicalKey};
use winit::window::{CursorGrabMode, Window, WindowId};

use hassault_native::api::WeaponSpec;
use hassault_native::audio::GameAudio;
use hassault_native::bodies;
use hassault_native::camera::Camera;
use hassault_native::geometry::MeshData;
use hassault_native::hud::{Hud, HudView, OverlayVertex};
use hassault_native::menu::{self, Action, Menu, Page};
use hassault_native::net::{Incoming, MatchSocket};
use hassault_native::physics::{self, eye_height, MoveInput, JUMP_SPEED, MOVE_SPEED};
use hassault_native::prediction::Prediction;
use hassault_native::protocol::{Command, Event, Fx, PlayerRow, SelfState};
use hassault_native::renderer::{Renderer, Vertex};
use hassault_native::settings::{Settings, SettingsWriter};
use hassault_native::trace::kick_vector;
use hassault_native::training::TrainingRange;
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

/// Cubes of travel between footsteps at a run, from `noise.py`'s
/// `STRIDE_DISTANCE`. Your own steps are made locally — the server does not send
/// them back, because a footstep that arrives 50 ms late is not a footstep.
const STRIDE_DISTANCE: f32 = 4.2;

pub struct App {
    window: Option<Arc<Window>>,
    renderer: Option<Renderer>,
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
    keys: Keys,
    sensitivity: f32,
    /// Whether the pointer is captured. Look is only applied while it is, so a
    /// click on the title bar does not spin the view.
    focused: bool,
    players: Vec<PlayerRow>,
    self_id: String,
    joined: bool,
    /// Whether the last snapshot had us dead, so a respawn can be told from an
    /// ordinary correction.
    was_dead: bool,
    last_frame: Instant,
    /// Frame timing, reported in the title bar — the number this client exists to
    /// move, so it should not need a profiler to read.
    frames: u32,
    fps_since: Instant,
    fps: f32,
    /// Unacknowledged commands, sampled when the title updates.
    pending: usize,
    map_name: String,
    /// Sequence numbers for the offline simulation, which has no socket to stamp
    /// them. Only the ordering matters here — nothing acknowledges them.
    local_seq: u64,
    /// The served loadout. **Never hardcoded**: the crosshair opens by the
    /// weapon's own cone and the view model is built from the weapon's own id,
    /// and a local copy of either is wrong only for the weapon it is wrong for.
    weapons: Vec<WeaponSpec>,
    /// The equipped skin for each weapon, by weapon id. Fetched once at startup
    /// like the loadout: this process is launched per match, so there is no
    /// moment during one when the armoury could change under it.
    skins: HashMap<String, Skin>,
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
}

impl App {
    /// `socket: None` is Train: a world, a body, and nobody else in it.
    pub fn new(
        world: World,
        mesh: MeshData,
        socket: Option<MatchSocket>,
        settings: Settings,
        writer: SettingsWriter,
        weapons: Vec<WeaponSpec>,
        skins: HashMap<String, Skin>,
    ) -> App {
        let map_name = world.info.name.clone();
        let mut app = App {
            window: None,
            renderer: None,
            mesh,
            world,
            socket,
            pending_bots: None,
            prediction: Prediction::default(),
            camera: Camera::default(),
            keys: Keys::default(),
            sensitivity: settings.sensitivity,
            focused: false,
            players: Vec::new(),
            self_id: String::new(),
            joined: false,
            was_dead: false,
            last_frame: Instant::now(),
            frames: 0,
            fps_since: Instant::now(),
            fps: 0.0,
            pending: 0,
            map_name,
            local_seq: 0,
            weapons,
            skins,
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
            scoped: 0,
            base_fov: Camera::default().fov,
        };
        if app.audio.is_none() {
            // Said out loud: a game that is silently silent reads as a game whose
            // sound is broken, and the noise mechanic is a mechanic.
            eprintln!("hassault: no audio output device; playing silently");
        }
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
        let sensitivity = LOOK_SCALE * self.sensitivity / self.magnification();
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
        self.camera.fov = self.base_fov / self.magnification();
    }

    /// Horizontal speed, in cubes per second. Vertical is deliberately excluded:
    /// the number is about the movement model's ground speed cap, and falling
    /// would otherwise read as a chained jump.
    fn ground_speed(&self) -> f32 {
        let p = &self.prediction.state;
        (p.vel_x * p.vel_x + p.vel_y * p.vel_y).sqrt()
    }

    /// Advance the HUD and the weapon in the hands by one frame.
    fn animate(&mut self, dt: f32) {
        self.hud.update(dt);
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
        };
        self.viewmodel.update(dt, &frame);
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
        }
        self.was_grounded = grounded;
    }

    /// One of our own noises, dead centre. `weapon` gives it the voice of the gun
    /// in our hands rather than the generic shot.
    fn play_own(&self, kind: &str, volume: f32, weapon: bool) {
        let Some(audio) = &self.audio else { return };
        audio.own(kind, volume, if weapon { self.held() } else { None });
    }

    /// Bots to add once we are in a room. Ignored without a socket, which is not
    /// a case to guard against — the launcher only sends a count with `host`.
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
        let Some((x, y, z)) = self
            .players
            .iter()
            .find(|p| p.id == self.self_id)
            .map(|p| (p.x, p.y, p.z))
        else {
            return;
        };
        // `on_ground` is not on the wire — the snapshot sends a position, not a
        // support state — so the replay re-derives it from the first step, which
        // is exactly what it would do anyway.
        self.prediction.reconcile(&self.world, ack, x, y, z, false);
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
                    self.self_id = w.player_id;
                    self.players = w.players;
                    self.joined = true;
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
                    }
                    self.hud.on_self(&s.you);
                    self.hud.on_hits(&s.you.hits);
                    for fx in &s.fx {
                        self.hud.on_fx(fx, &self.self_id);
                        // The muzzle flash, from the server's own account of the
                        // shot rather than from the fire key: this client has no
                        // trigger controller, so the key flashes on shots the
                        // server refused for rate limiting, an empty magazine, or
                        // being dead.
                        if let Fx::Shot { id, .. } = fx {
                            if id == &self.self_id {
                                self.viewmodel.fire();
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
                    self.you = Some(s.you.clone());
                    self.players = s.players;
                    if respawned {
                        let (yaw, pitch) = (self.camera.yaw, self.camera.pitch);
                        self.prediction.reset(
                            s.you.x,
                            s.you.y,
                            s.you.z,
                            yaw.to_radians(),
                            pitch.to_radians(),
                        );
                        self.follow_prediction();
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
                Incoming::Event(Event::Other(_)) => {}
                Incoming::Closed(why) => {
                    eprintln!("hassault: connection closed: {why}");
                    event_loop.exit();
                }
            }
        }
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
        let dt = dt.clamp(0.001, MAX_DT);
        let input = MoveInput {
            forward: axis(self.keys.forward, self.keys.back),
            strafe: axis(self.keys.right, self.keys.left),
            jump: self.keys.jump,
            crouch: self.keys.crouch,
        };
        if self.socket.is_none() {
            self.local_seq += 1;
            self.prediction.state.yaw = self.camera.yaw.to_radians();
            self.prediction.state.pitch = self.camera.pitch.to_radians();
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
        cmd.fire = self.keys.fire;
        // Which cone the server should use for it. Clamped there against the
        // weapon this command lands on — the wire parser cannot know that.
        cmd.scoped = self.scoped;
        // `-1` is "no change", so this is naturally absent on every frame that
        // did not ask for one; taken rather than left set, or every command for
        // the rest of the match repeats a switch that already happened.
        cmd.weapon = std::mem::replace(&mut self.want_weapon, -1);
        cmd.reload = std::mem::take(&mut self.want_reload);
        cmd.yaw = self.camera.yaw;
        cmd.pitch = self.camera.pitch;

        // `push_command` stamps the sequence number, and the prediction is keyed
        // by it. Predicting under a number we invented separately would make
        // every `ack` refer to a different command than the one it replays.
        let Some(socket) = &mut self.socket else {
            return;
        };
        let seq = socket.push_command(cmd);
        self.prediction.predict(
            &self.world,
            seq,
            input,
            dt,
            // The physics works in radians about +x; the wire and the camera are
            // in degrees. One conversion, at the one seam that needs it.
            self.camera.yaw.to_radians(),
            self.camera.pitch.to_radians(),
        );
        self.prediction.ease(dt);
        self.follow_prediction();

        if let Some(socket) = &mut self.socket {
            if let Err(e) = socket.flush(None) {
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
        self.you = Some(you);
    }

    /// One trigger pull on the range, rate-limited the way the server would.
    ///
    /// The limit is not decoration: without it a 62 rpm sniper fires once per
    /// frame, which at this client's frame rate is roughly two thousand rounds a
    /// second and makes the mode useless for learning anything about timing.
    fn try_fire(&mut self) {
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
        self.play_own("shot", 0.55, true);
        let _ = shot;
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
        if slot >= self.weapons.len() {
            return;
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
                // Leaving says goodbye first: without it the room holds a player
                // who is not there until the socket times out.
                if let Some(socket) = &self.socket {
                    let _ = socket.leave();
                }
                event_loop.exit();
            }
            action => {
                let Some(key) = menu::apply(action, step, &mut self.settings) else {
                    return;
                };
                // Applied to the live client *before* it is saved: the point of
                // an in-game menu is seeing the change on the frame you make it,
                // and the write is a round trip on another thread.
                self.apply_settings(action);
                if let Some(value) = self.settings.value_for(key) {
                    self.writer.save(key, value);
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
            Action::RenderScale | Action::Quality | Action::Vsync => {
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
            WindowEvent::Focused(false) => self.set_grab(false),
            WindowEvent::CursorMoved { position, .. } => {
                // Only meaningful while the menu is up: in play the pointer is
                // locked and the look comes from `DeviceEvent`, which is a raw
                // delta and not a position. See the module docs.
                if self.menu.open {
                    let (w, h) = self.window_size();
                    let count = self.menu_rows();
                    self.menu
                        .hover(position.x as f32, position.y as f32, count, w, h);
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
            WindowEvent::MouseInput { state, button, .. } => {
                if button == MouseButton::Right && self.focused {
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
                    } else {
                        self.keys.fire = state == ElementState::Pressed;
                    }
                }
            }
            WindowEvent::KeyboardInput { event, .. } => {
                let down = event.state == ElementState::Pressed;
                if let PhysicalKey::Code(code) = event.physical_key {
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
                    match code {
                        KeyCode::KeyW => self.keys.forward = down,
                        KeyCode::KeyS => self.keys.back = down,
                        KeyCode::KeyA => self.keys.left = down,
                        KeyCode::KeyD => self.keys.right = down,
                        KeyCode::Space => self.keys.jump = down,
                        KeyCode::ShiftLeft => self.keys.crouch = down,
                        KeyCode::KeyR if down => self.reload(),
                        // The number row picks a weapon. `Digit1` is the knife,
                        // matching the server's slot order — which is the order
                        // `GET /api/hassault/weapons` serves them in, so the two
                        // cannot drift.
                        KeyCode::Digit1 if down => self.select_weapon(0),
                        KeyCode::Digit2 if down => self.select_weapon(1),
                        KeyCode::Digit3 if down => self.select_weapon(2),
                        KeyCode::Digit4 if down => self.select_weapon(3),
                        KeyCode::Digit5 if down => self.select_weapon(4),
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
                    &self.players
                };
                let verts = bodies::build(rows, &self.self_id);
                self.viewmodel.vertices(&mut self.weapon_verts);
                let (width, height) = self.renderer.as_ref().map(|r| r.size()).unwrap_or((1, 1));
                // The name and the cone are copied out rather than borrowed:
                // `self.overlay` is taken mutably below, and a `HudView` holding
                // a `&str` into `self.weapons` would borrow `self` twice.
                let held = self.held();
                let weapon_name = held.map(|w| w.name.clone()).unwrap_or_default();
                // The cone the **next** shot would use: the scoped one while
                // scoped, the hip-fire one otherwise. Drawing `spread`
                // unconditionally would hide the hip-fire penalty, which is the
                // whole of what an unscoped sniper is — and it is 27× wider.
                let spread = held
                    .map(|w| {
                        if self.scoped > 0 {
                            w.spread
                        } else {
                            w.hipfire_spread
                        }
                    })
                    .unwrap_or(0.0);
                let magnification = self.magnification();
                let mut overlay = std::mem::take(&mut self.overlay);
                let view = HudView {
                    crosshair: self.settings.crosshair,
                    width,
                    height,
                    you: self.you.as_ref(),
                    weapon_name: &weapon_name,
                    // The cone the *next* shot would use. This client has no
                    // scope yet, so it is always the hip-fire one — which is the
                    // honest reading either way: the crosshair must never be
                    // narrower than the shot it describes.
                    spread,
                    magnification,
                    speed: self.ground_speed(),
                    move_speed: MOVE_SPEED,
                    on_ground: self.prediction.state.on_ground,
                    crouching: self.prediction.state.crouch > 0.5,
                    playing: self.joined,
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
                self.overlay = overlay;

                let Some(renderer) = &mut self.renderer else {
                    return;
                };
                renderer.set_bodies(&verts);
                renderer.set_viewmodel(&self.weapon_verts);
                renderer.set_overlay(&self.overlay);
                // `Ok(false)` is a frame that did not happen — minimised,
                // occluded, or a surface that has moved on. Routine, and already
                // handled inside; there is nothing to report and nothing to stop.
                match renderer.render(&self.camera) {
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
        self.pump_network(event_loop);

        let now = Instant::now();
        let dt = now.duration_since(self.last_frame).as_secs_f32();
        self.last_frame = now;
        self.send_input(dt);
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
            HashMap::new(),
        )
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
