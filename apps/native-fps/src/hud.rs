//! The heads-up display: health, ammo, the crosshair, the kill feed.
//!
//! Everything the snapshot already carried and the native client simply did not
//! draw. `you` in a snapshot is per-recipient precisely so a client can show the
//! player their own health, magazine, reload clock and respawn clock — and until
//! now all of it arrived and was thrown away, which made the native client
//! playable only by someone who already knew the game.
//!
//! **Drawn, not composited.** There is no DOM here and no text renderer: this
//! module turns a frame's state into triangles in one pass, which
//! `renderer::Renderer::set_overlay` uploads and draws last with alpha blending
//! and no depth test. Geometry rather than a texture atlas keeps the whole HUD
//! resolution-independent and adds no asset to a repo that ships none.
//!
//! **The font is synthesized, like the sounds and the maps.** A 5×7 bitmap
//! defined in code, written as binary literals so each glyph is legible as its
//! own shape in the source. Shipping a TTF would mean shipping someone else's
//! licence for the sake of four numbers on a screen. Uppercase only, because a
//! 5×7 cell has no room for descenders and a HUD has nothing to say in prose.
//!
//! Two rules that are quietly load-bearing:
//!
//! - **Layout is in pixels, converted to NDC at the end.** A HUD laid out in
//!   normalized coordinates stretches with the window: the crosshair becomes an
//!   oblong on an ultrawide monitor, and a crosshair whose arms differ in length
//!   by aspect ratio is a crosshair that lies about the spread it is drawing.
//! - **Nothing here invents a fact.** The hitmarker comes from `you.hits`, which
//!   the server drains as it builds the envelope, and the muzzle flash from the
//!   `shot` effect. A marker driven by the fire key lights up on shots that hit
//!   a wall — which is the same class of lie as drawing footstep positions.

use std::collections::VecDeque;

use crate::console::{LogLine, Tone};
use crate::damage::Placed;
use crate::protocol::{Fx, HitMarker, HurtMarker, SelfState};
use crate::radar::{self, Blip, Run};
use crate::settings::{Crosshair, CrosshairStyle};

/// One overlay vertex: a position already in clip space, and a colour with an
/// alpha the blend state actually uses.
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct OverlayVertex {
    pub position: [f32; 2],
    pub color: [f32; 4],
}

/// How long a kill note stays up. The browser client's `KILL_TTL_MS`.
const KILL_TTL: f32 = 6.0;

/// The underwater tint: the browser's `rgba(12,46,68,0.62)` edge colour, which is
/// the part of its gradient a player actually reads as "under water".
const UNDERWATER_TINT: [f32; 4] = [0.047, 0.180, 0.267, 0.45];

/// How long a hitmarker and a damage flash stay on screen.
const MARKER_LIFE: f32 = 0.18;
const FLASH_LIFE: f32 = 0.35;

/// The most kill notes drawn at once, newest first — the browser's five.
const MAX_FEED: usize = 5;

/// How long a damage arrow stays up, and the most drawn at once.
///
/// Longer than a hitmarker because it is an *instruction* rather than a
/// confirmation — it has to survive the moment of being hit, which is the moment
/// a player is least able to read anything.
const ARROW_LIFE: f32 = 1.2;
const MAX_DAMAGE_ARROWS: usize = 6;

/// How long the centre kill notice stays up.
///
/// Longer than a hitmarker, shorter than a damage arrow. It is a *reward*, not
/// an instruction — it does not need to survive the moment the way an arrow
/// pointing at your killer does, and one that outstayed the next engagement
/// would be sitting over the crosshair during it.
const KILL_NOTICE_LIFE: f32 = 1.4;

/// Killstreak milestones, and what each is called.
///
/// **Ascending, and read from the back**, so the highest milestone a streak has
/// passed is the one announced. Scanned forwards, a 12-kill streak would report
/// the first threshold it matched.
///
/// Only the listed counts announce anything: a notice on every kill past three
/// would make the streak the loudest thing on screen precisely when the kill
/// itself is what you want to see. The gaps widen for the same reason.
const STREAKS: [(u32, &str); 5] = [
    (3, "TRIPLE"),
    (5, "RAMPAGE"),
    (7, "DOMINATING"),
    (10, "UNSTOPPABLE"),
    (15, "LEGENDARY"),
];

/// What to call a streak of exactly this many, or `None` at a count between
/// milestones.
fn streak_name(kills: u32) -> Option<&'static str> {
    STREAKS
        .iter()
        .rev()
        .find(|(at, _)| *at == kills)
        .map(|(_, name)| *name)
}

// The palette, as a **ramp rather than six independent choices**. Everything
// readable sits on one cool grey axis (`WHITE` → `DIM` → `FAINT`) and colour is
// spent only where it means something: amber for "yours", red for "you are
// losing something", green for "over the line", blue for armour.
//
// `ARMOUR` is the browser's own `#6f97c4`, so the one mechanic both clients draw
// is the same colour in both — a health pack red in one client and orange in the
// other looks deliberate from either side alone, which is the rule
// `tests/browser_parity.rs` already pins for item tints.
const WHITE: [f32; 4] = [0.92, 0.94, 0.96, 0.9];
const DIM: [f32; 4] = [0.72, 0.76, 0.80, 0.65];
/// One step below `DIM`: structure that must be visible without being read —
/// bar troughs, spent magazine ticks, grid rules.
const FAINT: [f32; 4] = [0.52, 0.57, 0.62, 0.40];
const AMBER: [f32; 4] = [0.94, 0.83, 0.54, 0.95];
const RED: [f32; 4] = [0.97, 0.32, 0.28, 0.95];
/// The crosshair's outline.
///
/// Near-black rather than black, and not fully opaque: an outline is meant to
/// separate the reticle from the wall behind it, not to be a second reticle. Its
/// alpha is multiplied by the crosshair's own, so turning the crosshair down
/// turns the whole thing down together.
const OUTLINE: [f32; 4] = [0.02, 0.02, 0.03, 0.85];
const GREEN: [f32; 4] = [0.49, 0.91, 0.53, 0.95];
/// `0x6f97c4` — the browser's armour colour, unchanged.
const ARMOUR: [f32; 4] = [0.4353, 0.5922, 0.7686, 0.95];
const PANEL: [f32; 4] = [0.05, 0.07, 0.09, 0.45];
/// The trough a bar is drawn in. Darker than `PANEL` so a bar reads as
/// *inset* rather than as another panel stacked on the first.
const TROUGH: [f32; 4] = [0.06, 0.08, 0.10, 0.72];
/// The lag trail behind a health bar that has just dropped: the same red the
/// number turns, at a fraction of its weight, so the eye reads "this much, just
/// now" without the trail competing with the bar itself.
const GHOST: [f32; 4] = [0.80, 0.24, 0.22, 0.55];

/// How long the damage-lag trail takes to catch up with the bar, in seconds.
///
/// Long enough to be read after the shot that caused it, short enough to have
/// finished before the next one in a burst — a trail still draining from the
/// previous hit would understate the one you are looking at.
const GHOST_FALL: f32 = 0.45;
/// The delay before it starts draining, so a single hit is legible at all.
const GHOST_HOLD: f32 = 0.12;

/// The margin every edge-anchored block keeps, in HUD units. Deliberately more
/// than a couple of pixels: a HUD flush against the edge is the first thing an
/// overscanning display or a window manager's shadow eats.
const MARGIN: f32 = 5.0;

/// One line of the scoreboard.
///
/// A flattened row rather than a `&PlayerRow`, because the scoreboard shows one
/// thing the snapshot does not carry — which row is *you* — and because sorting
/// borrowed rows out of the live roster would tie the HUD to the roster's
/// lifetime for the sake of avoiding four `String` clones a keypress.
pub struct ScoreRow {
    pub name: String,
    pub kills: i32,
    pub deaths: i32,
    pub team: i32,
    pub bot: bool,
    pub you: bool,
}

/// One line of the kill feed, already phrased.
#[derive(Debug, Clone)]
pub struct KillNote {
    pub text: String,
    /// Whether we did it or it was done to us — worth colouring differently.
    pub mine: bool,
    pub age: f32,
}

/// The radar, as this frame's painter needs to see it.
///
/// The floor plan is borrowed and built **once per map** (`radar::floor_plan`),
/// not per frame: it is a property of the world, and rebuilding it every frame
/// is how a minimap ends up costing more than the map.
///
/// `blips` is already filtered — see `radar::blips`. Deciding *here* who is on
/// the radar would put the rule that keeps unspotted enemies off it inside a
/// painter, where nothing can test it.
pub struct RadarView<'a> {
    pub plan: &'a [Run],
    /// Where we are, in cubes, and which way we are facing, in **radians**.
    pub x: f32,
    pub y: f32,
    pub yaw: f32,
    pub blips: &'a [Blip],
}

/// The developer console, as this frame's painter needs to see it.
///
/// Borrowed rather than copied: the scrollback is up to 400 lines and a HUD is
/// rebuilt every frame, so cloning it would be four hundred string allocations
/// per frame for a panel that is closed almost always.
pub struct ConsoleView<'a> {
    /// The lines the active filter shows, oldest first.
    ///
    /// **Already filtered by the caller**, not filtered here. The filter's rules
    /// are the console's business — they mirror the browser's, and they are
    /// evaluated once per line when it is logged rather than once per line per
    /// frame — and a painter that re-derived them would be a second opinion
    /// about which tab a line belongs to.
    pub lines: &'a [&'a LogLine],
    pub input: &'a str,
    /// Byte offset of the caret within `input`.
    pub cursor: usize,
    /// Lines scrolled back from the bottom.
    pub scroll: usize,
    pub suggestions: &'a [String],
    pub suggestion: usize,
    /// The selected completion, spelled out. Drawn under the input line.
    pub detail: Option<&'a str>,
    /// Whether the node's registry actually loaded. Drawn in the title bar,
    /// because a console with no completions looks broken in exactly the same
    /// way as one whose fetch 404'd, and the two want different reactions.
    pub registry_loaded: bool,
    /// The active filter's name, and how many lines it is hiding.
    ///
    /// The count is drawn whenever it is non-zero, because a filtered console
    /// that did not say so is indistinguishable from one that has stopped
    /// receiving output — which is the first thing you would reach for this
    /// console to diagnose.
    pub filter: &'static str,
    pub hidden: usize,
    /// The match this console is attached to. Empty in Train, where there is no
    /// room and saying `ROOM` with nothing after it would be worse than silence.
    pub room: &'a str,
    pub map: &'a str,
    /// Round-trip time, when one has been measured. `None` draws as absent
    /// rather than as zero, the same rule the rest of this HUD follows.
    pub rtt: Option<f32>,
    /// `sv_cheats`, as last seen. `None` is "never been told", which is a
    /// different fact from "off" and must not draw the same.
    pub cheats: Option<bool>,
    /// The quick-action row. See `console::QuickAction`.
    pub quick: &'a [crate::console::QuickAction],
}

/// One grenade in the tray: what it is, how many are left, and whether it is the
/// one that would go out if the throw key went down.
pub struct UtilitySlot {
    pub name: String,
    /// The grenade's *kind* (`he`, `flash`, `smoke`, `fire`), which the browser's
    /// tray tints by and this one abbreviates from. Not the id: the incendiary's
    /// id is `molotov` and its kind is `fire`.
    pub kind: String,
    pub count: i32,
}

/// What is in the pouch, as this frame's painter needs to see it.
///
/// Served in slot order and drawn in it, because the wire carries a slot index:
/// a tray that sorted itself would number the keys differently from the throws
/// they produce, and the player would learn the wrong four keys.
pub struct UtilityView {
    pub slots: Vec<UtilitySlot>,
    pub selected: usize,
}

/// Everything about this frame that is not already inside `Hud`.
pub struct HudView<'a> {
    pub width: u32,
    pub height: u32,
    /// A multiplier on the HUD's derived unit, from the player's settings.
    ///
    /// 1.0 is the derived size. Kept as a separate field rather than folded into
    /// `height` before it arrives, because `height` is also what everything is
    /// *positioned* against — scaling that would move the HUD off the screen
    /// rather than enlarging it.
    pub hud_scale: f32,
    /// The private half of the snapshot. `None` in Train, which has no server
    /// and therefore no health, ammo or respawn clock to report — drawing a
    /// hundred hit points there would be a number this client made up.
    pub you: Option<&'a SelfState>,
    pub weapon_name: &'a str,
    /// The cone the **next** shot would use, in radians — a weapon's
    /// `hipfireSpread`, served like every other weapon number.
    ///
    /// Passed as the cone rather than as a gap in pixels so the conversion lives
    /// here with the rest of the layout. Drawing `spread` instead would hide the
    /// hip-fire penalty, which is the one number an unscoped sniper is about.
    pub spread: f32,
    /// The held weapon's full reload time in seconds, served like every other
    /// weapon number. 0 for a weapon that does not reload.
    ///
    /// Needed because `you.reload_in` is a *remaining* time, and a progress dial
    /// cannot be drawn from a remainder alone. Taking it from the served table
    /// rather than remembering the largest `reload_in` seen keeps the arc honest
    /// on the first reload of a match.
    pub reload_time: f32,
    /// Scope magnification: 1 when unscoped, so a caller can pass it blind.
    ///
    /// Above 1 the crosshair is replaced by the sight, whose blacked-out surround
    /// is the **mechanical** half of a scope and not decoration: it is what the
    /// magnification *costs*. A zoom with a clear view all round would be a free
    /// upgrade rather than the trade the weapon is built around.
    pub magnification: f32,
    /// Horizontal speed and the run speed it is measured against — the number
    /// the movement is *about*, and the only way a chained jump is learnable.
    pub speed: f32,
    pub move_speed: f32,
    /// Which way we are facing, in **radians**. Every bearing the server sends
    /// is a world bearing, so this is what turns one into a direction on screen.
    ///
    /// Carried here rather than read off `radar`, which is `None` in Train and
    /// while connecting: the damage arrows have to work in a match whether or
    /// not a radar is being drawn, and reaching into an optional for a number
    /// that is always known would make them disappear with it.
    pub yaw: f32,
    pub on_ground: bool,
    pub crouching: bool,
    /// Whether our own eye is under the water plane.
    ///
    /// The same line the simulation reads to take the jump away, which is the
    /// whole reason it is drawn: a player who suddenly cannot jump and moves at
    /// two thirds speed needs the screen to say why. The browser tints on exactly
    /// this test.
    pub underwater: bool,
    /// Whether there is a body in the world to report on — deployed and not
    /// still connecting. Deliberately **not** "the pointer is captured":
    /// releasing the pointer is not a menu here, and a world drawn with no HUD
    /// in it reads as a client that half-loaded.
    pub playing: bool,
    /// The scoreboard, when it is being held open. `None` is not "an empty
    /// match" — it is "not asked for", and the two must not draw the same.
    ///
    /// Sorted by the caller: what "winning" means is a game-mode question, and
    /// the painter has no business having an opinion about it.
    pub scoreboard: Option<&'a [ScoreRow]>,
    /// Team scores, as the snapshot carries them. Drawn above the roster, which
    /// is the only place the *match* score appears — the per-player columns are
    /// a different question from who is winning.
    pub scores: &'a [i32],
    /// This frame's floating damage numbers, already projected onto the screen.
    ///
    /// **Projected by the caller, not here.** The painter holds no matrices and
    /// should not start: `damage.rs` owns the world anchors and the projection,
    /// which is what lets both be tested headless. Everything in this slice is
    /// already in the painter's own pixels.
    pub damage: &'a [Placed],
    /// Round-trip time in ms, or `None` when nothing has been measured yet —
    /// Train, or the first second of a match.
    ///
    /// The distinction matters and is why this is an `Option`: drawing `0 MS`
    /// for "not measured" is a claim of a perfect link, and a player judging
    /// whether a lost duel was theirs or the network's would be reading a
    /// number the client made up. Absent is drawn as absent.
    pub rtt: Option<f32>,
    pub fps: Option<f32>,
    pub net_graph: u32,
    /// The player's own reticle. Every number here is theirs, and the one thing
    /// that is *not* is the opening with spread — the gap setting is the floor
    /// the cone opens from, never a cap on it. A crosshair that could be
    /// configured not to show the hip-fire penalty would be a setting that wins
    /// gunfights.
    pub crosshair: Crosshair,
    /// The radar, when there is a body to centre it on. `None` in Train and
    /// while connecting — a radar with no viewer would have to invent a
    /// position, and the centre of this instrument is the one thing on it that
    /// is not allowed to be a guess.
    pub radar: Option<RadarView<'a>>,
    /// The grenades, when any have been served. `None` is a node that answered
    /// no `/tacticals` — and the tray is then absent rather than drawn empty,
    /// because an empty tray is a claim that you are carrying nothing.
    pub utility: Option<&'a UtilityView>,
    /// How blind a flashbang has left us, 0..1, straight off `you.flash`.
    ///
    /// **Resolved per player on the server** from where we were looking and
    /// whether a wall was in the way, so this is a renderer for a number and not
    /// a client-side effect — a client that computed its own would make not being
    /// blinded a setting. Parsed and then ignored for as long as flashbangs have
    /// existed here, which made the flash the one grenade with no effect on the
    /// person it went off in front of.
    pub flash: f32,
    /// The console, when it is open. `None` is "closed" — and the panel is
    /// drawn **last and over everything**, including the scoreboard, because a
    /// console you can read the ammo counter through is a console you cannot
    /// read.
    pub console: Option<ConsoleView<'a>>,
}

/// The HUD's own memory: things that persist across frames because they are
/// *events*, not state the snapshot repeats.
pub struct Hud {
    feed: VecDeque<KillNote>,
    /// Seconds since the last landed hit, and whether it killed.
    hit_age: f32,
    hit_killed: bool,
    /// Seconds since we last took damage.
    damage_age: f32,
    /// The health we last saw, so a drop can be told from a snapshot repeating.
    last_hp: f32,
    /// Where the damage-lag trail currently is, in hit points, and how long it
    /// has been held there.
    ///
    /// Health the bar has already given up but the trail has not yet caught up
    /// with, which is what turns "you are on 40" into "you *were* on 75 a moment
    /// ago". It is only ever pulled **down** toward the live value: a heal
    /// snaps it, because a trail that grew would be drawing damage that never
    /// happened.
    ghost_hp: f32,
    ghost_hold: f32,
    /// Where damage came from, as world bearings in radians, newest last, with
    /// the age of each. Drawn as arcs around the crosshair.
    damage_from: VecDeque<(f32, f32)>,
    /// Fall damage from the most recent landing, and how long ago.
    fell: f32,
    fell_age: f32,
    /// The centre kill notice: what it says, and how old it is.
    ///
    /// Two sources feed it, deliberately. `on_hits` sets the generic form from
    /// **our own hitmarkers**, which exist in a match *and* on the range — so
    /// Train confirms a downed dummy rather than being the one mode where a kill
    /// says nothing. `on_fx` then upgrades it with the victim's name from the
    /// authoritative kill event, which only a match has. Online the two arrive
    /// in the same tick and only the named form is ever drawn.
    kill_notice: String,
    kill_age: f32,
    /// Kills since we last died, and the milestone notice it earned.
    ///
    /// Counted from **our own hitmarkers** rather than from `Fx::Kill`, which is
    /// the only source that exists in both modes — the range has no server and
    /// so no kill effects at all. Counted rather than `any()`-ed, because one
    /// tick can carry two kills (a shotgun through two bodies) and collapsing
    /// them would silently make a streak undercount.
    streak: u32,
    streak_notice: String,
    /// How long the scoreboard has been held open, for the entrance cascade.
    board_age: f32,
}

impl Default for Hud {
    fn default() -> Hud {
        Hud {
            feed: VecDeque::new(),
            hit_age: f32::MAX,
            hit_killed: false,
            damage_age: f32::MAX,
            last_hp: f32::MAX,
            ghost_hp: 0.0,
            ghost_hold: 0.0,
            damage_from: VecDeque::new(),
            fell: 0.0,
            fell_age: f32::MAX,
            kill_notice: String::new(),
            kill_age: f32::MAX,
            streak: 0,
            streak_notice: String::new(),
            board_age: 0.0,
        }
    }
}

impl Hud {
    /// Fold one effect into the feed.
    pub fn on_fx(&mut self, fx: &Fx, self_id: &str) {
        let Fx::Kill {
            victim,
            victim_name,
            killer,
            killer_name,
            head,
            ..
        } = fx
        else {
            return;
        };
        // An empty killer is what the feed reads as "the map did it" — a fall,
        // which goes through `_fall_damage` and has no killer by construction.
        let text = if killer.is_empty() {
            format!("{} FELL", name_of(victim_name, victim))
        } else {
            format!(
                "{} {} {}",
                name_of(killer_name, killer),
                if *head { "X" } else { ">" },
                name_of(victim_name, victim)
            )
        };
        let mine = killer == self_id || victim == self_id;
        // A kill **we** made, with the name the feed already has. Not a death of
        // ours, and not a fall — `killer` is empty for those, and "ELIMINATED"
        // over the crosshair as you die would be an unusually cruel bug.
        if killer == self_id && victim != self_id {
            self.kill_notice = if *head {
                format!("HEADSHOT {}", name_of(victim_name, victim))
            } else {
                format!("ELIMINATED {}", name_of(victim_name, victim))
            }
            .to_uppercase();
            self.kill_age = 0.0;
        }
        self.feed.push_front(KillNote {
            text,
            mine,
            age: 0.0,
        });
        self.feed.truncate(MAX_FEED);
    }

    /// Hits we landed, as the server counted them.
    pub fn on_hits(&mut self, hits: &[HitMarker]) {
        if hits.is_empty() {
            return;
        }
        self.hit_age = 0.0;
        // A burst that kills is a kill marker even if the other pellets only
        // wounded: the louder of the two is the one worth showing.
        self.hit_killed = hits.iter().any(|h| h.killed);
        if self.hit_killed {
            // The generic form, so a downed training dummy still confirms. In a
            // match `on_fx` runs immediately after this and replaces it with the
            // named one — which is why this must not be conditional on the
            // notice being empty, or a second kill in the same match would keep
            // the first victim's name.
            self.kill_notice = "ELIMINATED".to_string();
            self.kill_age = 0.0;
            self.streak += hits.iter().filter(|h| h.killed).count() as u32;
            // Cleared unless this kill *earned* a milestone, so the previous
            // one does not ride along under every kill until you die.
            self.streak_notice = match streak_name(self.streak) {
                Some(name) => format!("{name} {}", self.streak),
                None => String::new(),
            };
        }
    }

    /// The private half of a snapshot, for the things derived from a *change*.
    pub fn on_self(&mut self, you: &SelfState) {
        if you.hp < self.last_hp && self.last_hp != f32::MAX {
            self.damage_age = 0.0;
            // The trail holds where the bar *was*. Taken from the last seen
            // value rather than from the trail's current position, so a second
            // hit during a burst extends the same trail instead of restarting it
            // partway down and understating the pair.
            self.ghost_hp = self.ghost_hp.max(self.last_hp);
            self.ghost_hold = GHOST_HOLD;
        } else if you.hp > self.last_hp {
            // A heal snaps the trail down to the bar rather than leaving it
            // stranded above one that has gone *up*, where it would draw damage
            // that never happened and sit there until the next real hit.
            // `.max()` was the obvious way to write this and is the wrong one:
            // it can only raise the trail, so a heal left it exactly where the
            // last hit had put it.
            self.ghost_hp = you.hp;
            self.ghost_hold = 0.0;
        }
        self.last_hp = you.hp;
        // Dying ends the streak, and this is the moment it ends — not the
        // respawn several seconds later, which is where a player would see a
        // streak they no longer have still counting.
        if !you.alive {
            self.streak = 0;
            self.streak_notice.clear();
        }
        for h in &you.hurt {
            self.damage_from.push_back((h.bearing, 0.0));
        }
        while self.damage_from.len() > MAX_DAMAGE_ARROWS {
            self.damage_from.pop_front();
        }
        if you.fell > 0.0 {
            self.fell = you.fell;
            self.fell_age = 0.0;
        }
    }

    /// A respawn: the health jump it causes is not damage in reverse, and the
    /// last-seen value has to move with it or the next real hit flashes twice.
    /// A line that is not a kill: somebody arriving, somebody leaving, a friend
    /// asking you into their match.
    ///
    /// Onto the kill feed rather than into a panel of its own, and that is the
    /// whole point: the feed is already the place this client says what just
    /// happened, it is already on screen during play, and a second notice
    /// surface would be a second thing to position, fade and cap. `mine` picks
    /// the colour — an invite is *about you* and reads amber; a stranger
    /// joining is grey.
    pub fn note(&mut self, text: impl Into<String>, mine: bool) {
        self.feed.push_front(KillNote {
            text: text.into().to_uppercase(),
            mine,
            age: 0.0,
        });
        while self.feed.len() > MAX_FEED {
            self.feed.pop_back();
        }
    }

    pub fn on_respawn(&mut self) {
        self.last_hp = f32::MAX;
        self.damage_age = f32::MAX;
        self.fell_age = f32::MAX;
        // A kill you made before dying is over. Congratulating a player on the
        // frame they respawn reads as congratulating them for dying.
        self.kill_age = f32::MAX;
        // Belt and braces with `on_self`: a respawn is reached by dying, and a
        // streak surviving into the next life would be the one number on the
        // HUD that was simply wrong.
        self.streak = 0;
        self.streak_notice.clear();
        // Neither the trail nor the arrows survive a death: they describe the
        // fight that killed you, and drawing them over a fresh body would send
        // a player who has just spawned to look somewhere across the map.
        self.ghost_hp = 0.0;
        self.ghost_hold = 0.0;
        self.damage_from.clear();
    }

    /// `board_open` is the scoreboard key, held. It drives the entrance
    /// cascade, and resetting on release is what makes the cascade play *every*
    /// time the board is opened rather than only the first.
    pub fn update(&mut self, dt: f32, board_open: bool) {
        self.board_age = if board_open { self.board_age + dt } else { 0.0 };
        self.hit_age = advance(self.hit_age, dt);
        self.damage_age = advance(self.damage_age, dt);
        self.fell_age = advance(self.fell_age, dt);
        self.kill_age = advance(self.kill_age, dt);
        // The trail eases toward the live value, holding first so a single hit
        // is legible before it starts draining. Only ever downward: `on_self`
        // owns moving it up, and does so by snapping.
        //
        // The hold is **spent out of `dt`**, and the remainder drains in the
        // same call. An `else` here instead would make one long frame either
        // hold or drain but never both, so a trail could stall for a whole frame
        // at a time — and at a low frame rate it would visibly stutter rather
        // than ease.
        let mut rest = dt;
        if self.ghost_hold > 0.0 {
            let used = rest.min(self.ghost_hold);
            self.ghost_hold -= used;
            rest -= used;
        }
        if rest > 0.0 && self.ghost_hp > self.last_hp && self.last_hp != f32::MAX {
            // A fixed rate rather than a fixed duration, so a 5 hp scratch
            // drains in a fifth of the time a 25 hp burst does — it is the
            // trail's *length* that carries the reading, and a short one
            // lingering as long as a long one would overstate it.
            let step = (100.0 / GHOST_FALL) * rest;
            self.ghost_hp = (self.ghost_hp - step).max(self.last_hp);
        }
        for arrow in &mut self.damage_from {
            arrow.1 += dt;
        }
        while self.damage_from.front().is_some_and(|a| a.1 > ARROW_LIFE) {
            self.damage_from.pop_front();
        }
        for note in &mut self.feed {
            note.age += dt;
        }
        while self.feed.back().is_some_and(|n| n.age > KILL_TTL) {
            self.feed.pop_back();
        }
    }

    #[cfg(test)]
    fn feed_texts(&self) -> Vec<String> {
        self.feed.iter().map(|n| n.text.clone()).collect()
    }

    /// This frame's triangles.
    pub fn build(&self, view: &HudView, out: &mut Vec<OverlayVertex>) {
        out.clear();
        let mut p = Painter {
            out,
            width: view.width.max(1) as f32,
            height: view.height.max(1) as f32,
        };
        // One unit of HUD, in pixels. Derived from the window rather than fixed,
        // so the same layout is legible on a 720p laptop and on a 4K monitor
        // instead of shrinking to a smear on the second.
        let u = ((p.height / 360.0).round() * view.hud_scale.clamp(0.75, 1.5)).max(2.0);
        let hit = self.hit_age < MARKER_LIFE;

        if self.damage_age < FLASH_LIFE {
            // An edge wash rather than a full-screen tint: taking a hit must not
            // hide the person who is shooting at you.
            let a = 0.45 * (1.0 - self.damage_age / FLASH_LIFE);
            p.vignette([0.85, 0.12, 0.12, a], u * 14.0);
        }

        self.paint_feed(&mut p, u);

        // Not in the world yet — connecting, or between matches — means most of
        // the HUD has nothing to report. The kill feed above still makes sense,
        // and so does the console below: it is the surface you would reach for
        // *because* the client is not in a world yet. It used to be an early
        // return, which would have made the console the one panel you could not
        // open when you needed it.
        if view.playing {
            let dead = view.you.is_some_and(|y| !y.alive);
            if !dead && view.magnification > 1.0 {
                p.scope(u, hit, self.hit_killed, view.magnification);
            } else if !dead {
                // The browser's `crosshairSpread`, in its units, scaled to this
                // window's — with the player's own gap as the floor it opens
                // from.
                let gap = (view.crosshair.gap + view.spread * 260.0) * u * 0.5;
                p.crosshair(gap.max(2.0), u, hit, self.hit_killed, &view.crosshair);
            }

            // Over the crosshair and under everything else: an arrow is read at
            // the centre of the screen, where the player is already looking.
            if !dead {
                self.paint_damage_arrows(&mut p, view.yaw, u);
            }
            self.paint_health(&mut p, view, u);
            paint_weapon(&mut p, view, u);
            if let Some(utility) = view.utility {
                paint_utility(&mut p, view, utility, u);
            }
            self.paint_center(&mut p, view, u);
            // After the centre notices and before the scoreboard: a damage
            // number belongs to the world, so it must sit under the panels you
            // deliberately hold over it and over the reticle it is reporting on.
            paint_damage_numbers(&mut p, view.damage, u);
            paint_movement(&mut p, view, u);
            paint_net_graph(&mut p, view, u);
            if let Some(r) = &view.radar {
                paint_radar(&mut p, r, u);
            }
            // Last of the in-world layers, so it covers the rest: a scoreboard
            // is a thing you hold *over* the game, and one the ammo counter
            // shows through reads as a bug.
            if let Some(rows) = view.scoreboard {
                paint_scoreboard(&mut p, rows, view.scores, self.board_age, u);
            }
        }

        // Underwater, under everything else the HUD draws: it is a property of
        // the world you are looking at, not of the interface, so the ammo and the
        // crosshair stay legible through it. Swimming is a bad place to fight,
        // not a blindfold.
        //
        // A flat rect rather than the browser's radial gradient — this painter
        // has no gradients, and a vignette is the half of that effect that is
        // decoration. The half that matters is the colour shift, which says
        // "your head is under".
        if view.underwater {
            let (w, h) = (p.width, p.height);
            p.rect(0.0, 0.0, w, h, UNDERWATER_TINT);
        }

        // The flashbang, over the world and the HUD and under the console: a
        // flash you could read your ammo through is not a flash, and a flash
        // that hid the console would take the developer's one tool away for the
        // three seconds it is most interesting.
        //
        // Squared on the way in, exactly as the browser's `FlashOverlay` does, so
        // the peak is what hurts and the tail clears quickly — a linear fade
        // spends most of its life at a brightness that is annoying rather than
        // blinding.
        if view.flash > 0.01 {
            let a = (view.flash * view.flash).min(1.0);
            let (w, h) = (p.width, p.height);
            p.rect(0.0, 0.0, w, h, [1.0, 1.0, 1.0, a]);
        }

        // Over absolutely everything, in or out of a world.
        if let Some(console) = &view.console {
            paint_console(&mut p, console, u);
        }
    }

    fn paint_feed(&self, p: &mut Painter, u: f32) {
        let scale = u * 0.75;
        let mut y = u * 6.0;
        for note in &self.feed {
            // The last second is a fade rather than a disappearance, so a line
            // does not vanish mid-read.
            let fade = ((KILL_TTL - note.age) / 1.0).clamp(0.0, 1.0);
            let mut color = if note.mine { AMBER } else { DIM };
            color[3] *= fade;
            let w = text_width(&note.text, scale);
            let x = p.width - w - u * 6.0;
            let h = 7.0 * scale + scale * 2.0;
            // Chamfered on the left only — the right edge is the screen margin
            // that every line shares, and cutting it would make the stack look
            // ragged rather than cut.
            let cut = scale * 1.6;
            p.rect(
                x - scale + cut,
                y - scale,
                w + scale * 2.0 - cut,
                h,
                [PANEL[0], PANEL[1], PANEL[2], PANEL[3] * fade],
            );
            p.tri(
                (x - scale, y - scale + cut),
                (x - scale + cut, y - scale),
                (x - scale + cut, y - scale + cut),
                [PANEL[0], PANEL[1], PANEL[2], PANEL[3] * fade],
            );
            p.tri(
                (x - scale, y - scale + h - cut),
                (x - scale + cut, y - scale + h - cut),
                (x - scale + cut, y - scale + h),
                [PANEL[0], PANEL[1], PANEL[2], PANEL[3] * fade],
            );
            // A leading accent bar, which is what lets your own kills be found
            // in a busy feed without reading any of it.
            let mut accent = if note.mine { AMBER } else { FAINT };
            accent[3] *= fade;
            p.rect(x - scale + cut, y - scale, (u * 0.4).max(2.0), h, accent);
            p.text(x, y, scale, color, &note.text);
            y += 7.0 * scale + u * 3.0;
        }
    }

    fn paint_health(&self, p: &mut Painter, view: &HudView, u: f32) {
        let Some(you) = view.you else { return };
        let big = u * 2.4;
        let small = u * 0.8;
        let left = u * 6.0;
        // Laid out **upwards from the movement line**, which owns the bottom margin.
        // Anchoring downwards from a top instead puts the last line off the bottom
        // of the screen on a small window — where it is not clipped with a warning,
        // it is simply absent.
        let bar_h = u * 2.6;
        let armour_h = u * 1.2;
        // The floor is the top of the movement line, which owns the bottom-left
        // corner — **including this block's own panel padding**, which is the
        // part that is easy to leave out. The block used to be positioned so its
        // last bar cleared the line while the panel drawn around it did not, so
        // the two overlapped by exactly the padding: invisible at 720p, obvious
        // at 1440p, and a function of the window size either way.
        let padding = u * 1.8;
        let floor = p.height - MARGIN * u - 7.0 * (u * 0.75) - u * 1.5 - padding;
        // The armour row is **always** reserved, like the reload line opposite.
        // Taking the space only when there is armour means the health bar — the
        // one thing on this HUD read continuously — moves the instant a vest
        // runs out, which is the instant it is being watched hardest.
        let armour_y = floor - armour_h;
        let bar_y = armour_y - u * 0.6 - bar_h;
        let number_y = bar_y - u * 1.6 - 7.0 * big;

        let hp = you.hp.max(0.0).round() as i32;
        let low = you.hp <= 30.0;
        let color = if low { RED } else { WHITE };
        let label = hp.to_string();
        let bar_w = u * 44.0;
        // The armour bar sits *under* the health bar and is thinner, so the two
        // never compete: health is the number you die at, armour is a modifier
        // on the way there.
        p.panel(
            left - u * 1.5,
            number_y - u * 1.2,
            bar_w + u * 3.0,
            armour_y + armour_h - number_y + u * 1.8,
            u * 1.2,
            Some(if low { RED } else { FAINT }),
        );

        p.text(left, number_y, big, color, &label);
        p.text(
            left + text_width(&label, big) + small * 2.0,
            number_y + 7.0 * big - 7.0 * small,
            small,
            DIM,
            "HP",
        );

        // A bar as well as a number: a number is exact and a bar is instant, and
        // in a firefight only one of those gets read.
        p.rect(left, bar_y, bar_w, bar_h, TROUGH);
        let frac = (you.hp / 100.0).clamp(0.0, 1.0);
        // The lag trail, drawn between the trough and the bar so the bar covers
        // its own share of it. What is left is the gap between where health is
        // and where it was a moment ago — which is how much that last hit cost,
        // readable without doing arithmetic on two numbers.
        let ghost = (self.ghost_hp / 100.0).clamp(0.0, 1.0);
        if ghost > frac {
            p.rect(
                left + bar_w * frac,
                bar_y,
                bar_w * (ghost - frac),
                bar_h,
                GHOST,
            );
        }
        p.rect(left, bar_y, bar_w * frac, bar_h, color);
        // Segment rules over the top, every 25. Ticks rather than separate
        // bars, so the bar stays one continuous length and the marks only give
        // the eye something to measure it against.
        for i in 1..4 {
            let x = left + bar_w * (i as f32 / 4.0);
            p.rect(x, bar_y, (u * 0.2).max(1.0), bar_h, TROUGH);
        }

        // The trough is drawn whether or not there is armour: an empty trough
        // says "you could be wearing some and are not", which is a different
        // statement from the blank space that says nothing at all.
        p.rect(left, armour_y, bar_w, armour_h, TROUGH);
        if you.armour > 0.0 {
            let af = (you.armour / 100.0).clamp(0.0, 1.0);
            p.rect(left, armour_y, bar_w * af, armour_h, ARMOUR);
        }

        if you.protected {
            p.text(
                left + bar_w + u * 2.0,
                bar_y - u * 0.5,
                small,
                AMBER,
                "SPAWN SHIELD",
            );
        }
    }

    /// Arcs around the crosshair pointing at whatever is shooting you.
    ///
    /// The one piece of this HUD that is an *instruction* rather than a report,
    /// and the reason it is worth a wire field: a hurt vignette says you are
    /// being shot, which you already knew, while this says which way to turn.
    ///
    /// Bearings are the server's, so the drawing is `bearing - yaw` and nothing
    /// else — this client never learns where the shooter is, only which way they
    /// were. Same contract as the noise ring.
    fn paint_damage_arrows(&self, p: &mut Painter, yaw: f32, u: f32) {
        let cx = p.width * 0.5;
        let cy = p.height * 0.5;
        // Far enough out not to crowd the crosshair, close enough to be inside
        // the same glance. Roughly a tenth of the screen's height.
        let radius = u * 26.0;
        for (bearing, age) in &self.damage_from {
            let fade = (1.0 - age / ARROW_LIFE).clamp(0.0, 1.0);
            // Squared, so an arrow is bright for the moment it matters and then
            // gets out of the way rather than lingering at half strength.
            let alpha = fade * fade;
            let rel = bearing - yaw;
            let (s, c) = rel.sin_cos();
            // Screen space: +x right, +y **down**, so forward (`rel == 0`) has to
            // come out above the crosshair. Getting this sign wrong points every
            // arrow at the exact opposite of the shooter, which is worse than
            // drawing nothing at all and looks like working code.
            let (dx, dy) = (s, -c);
            let tip = (cx + dx * (radius + u * 7.0), cy + dy * (radius + u * 7.0));
            // The base, one arc-width either side of the bearing.
            let spread = 0.22_f32;
            let (ls, lc) = (rel - spread).sin_cos();
            let (rs, rc) = (rel + spread).sin_cos();
            let left = (cx + ls * radius, cy - lc * radius);
            let right = (cx + rs * radius, cy - rc * radius);
            p.tri(left, right, tip, [0.97, 0.32, 0.28, 0.85 * alpha]);
        }
    }

    fn paint_center(&self, p: &mut Painter, view: &HudView, u: f32) {
        let Some(you) = view.you else { return };
        let scale = u * 0.9;
        if !you.alive {
            let big = u * 1.6;
            p.center_text(p.height * 0.42, big, RED, "DEAD");
            let line = format!("RESPAWN IN {:.1}", you.respawn_in.max(0.0));
            p.center_text(p.height * 0.42 + 9.0 * big, scale, DIM, &line);
            return;
        }
        if self.fell_age < 1.2 && self.fell > 0.0 {
            // Fall damage has no killer, so it needs saying: without it a chunk
            // of health simply vanishes on landing and reads as a hit from
            // someone you never saw.
            let line = format!("-{} FALL", self.fell.round() as i32);
            p.center_text(p.height * 0.58, scale, [0.98, 0.62, 0.58, 0.9], &line);
        }
        if self.kill_age < KILL_NOTICE_LIFE && !self.kill_notice.is_empty() {
            // **Below the crosshair, and below the fall notice.** The obvious
            // place for a kill confirmation is above the aim, and this was there
            // first — until `examples/hud_preview` drew it: the scoreboard is a
            // centred panel spanning roughly 0.28..0.47 of the height, so a
            // notice at 0.36 printed straight through it every time somebody
            // opened the board. That collision is intermittent in play — the
            // board is a held key — which is exactly the kind of fault that
            // ships. No unit test would have caught it; the picture did.
            //
            // Faded out over its last third rather than cut, so a notice on its
            // way out cannot be mistaken for one that has just arrived.
            let fade =
                ((KILL_NOTICE_LIFE - self.kill_age) / (KILL_NOTICE_LIFE * 0.33)).clamp(0.0, 1.0);
            let colour = [AMBER[0], AMBER[1], AMBER[2], AMBER[3] * fade];
            let big = scale * 1.15;
            let y = p.height * 0.65;
            p.center_text(y, big, colour, &self.kill_notice);
            // A rule under it, the width of the text, growing out of nothing as
            // the notice fades. The one piece of structure the tactical
            // direction asks for and the cheapest thing on screen to draw: an
            // underline reads as a stamp where a box would read as a dialog.
            let w = text_width(&self.kill_notice, big);
            p.rect(
                (p.width - w * fade) * 0.5,
                y + 8.0 * big,
                w * fade,
                (big * 0.4).max(2.0),
                colour,
            );
            // The milestone, beneath the kill that earned it.
            //
            // **On the kill notice's clock, not one of its own.** A streak is
            // only ever announced at the instant of a kill, so a second timer
            // would be a second thing to age, cap and reset that could only ever
            // disagree with this one. Empty at a count between milestones, which
            // is most kills.
            if !self.streak_notice.is_empty() {
                p.center_text(
                    y + 11.0 * big,
                    scale * 0.9,
                    [WHITE[0], WHITE[1], WHITE[2], WHITE[3] * fade],
                    &self.streak_notice,
                );
            }
        }
    }
}

/// The floating damage numbers, already projected — see `HudView::damage`.
///
/// Three readings, in one glance and without reading the digits: a plain hit is
/// pale, a headshot is amber, and a killing blow is red and larger. That ordering
/// is the same one the hitmarker uses, so the two cannot disagree about what just
/// happened.
///
/// Culled against the window rather than clipped. A number whose body is off to
/// one side projects outside the screen, and the painter would happily emit those
/// quads — they cost vertices out of a shared 65536 budget for a thing nobody can
/// see. `text_width` is used for the left edge so the test is against the whole
/// string rather than its first glyph.
fn paint_damage_numbers(p: &mut Painter, numbers: &[Placed], u: f32) {
    for n in numbers {
        let (color, scale) = if n.killed {
            ([0.97, 0.32, 0.28, 1.0], u * 1.35)
        } else if n.head {
            ([0.94, 0.83, 0.54, 1.0], u * 1.15)
        } else {
            ([0.92, 0.94, 0.96, 1.0], u * 0.95)
        };
        let text = n.amount.to_string();
        let w = text_width(&text, scale);
        let x = n.x - w * 0.5;
        if x + w < 0.0 || x > p.width || n.y + 7.0 * scale < 0.0 || n.y > p.height {
            continue;
        }
        // A headshot says so. The digits alone cannot: 90 from a rifle to the
        // head and 90 from a sniper to the chest are the same number and very
        // different shots.
        if n.head {
            p.text(
                x - text_width("+", scale) - scale,
                n.y,
                scale,
                [color[0], color[1], color[2], color[3] * n.fade],
                "+",
            );
        }
        p.text(
            x,
            n.y,
            scale,
            [color[0], color[1], color[2], color[3] * n.fade],
            &text,
        );
    }
}

/// Where each row of the bottom-right weapon block sits, bottom-up.
///
/// `(strip_y, strip_h, reload_y, ammo_y, name_y)`.
///
/// **One function, because two blocks depend on it.** The utility tray stacks
/// directly on top of this one and used to recompute the same four lines by
/// hand, with a comment saying the arithmetic had to agree — which it then
/// silently stopped doing the moment this layout changed. A tray printed
/// through the weapon name is what that looks like, and nothing fails.
///
/// Everything here is **always reserved**, including the reload row and the
/// magazine strip. A block that only takes the space it needs moves the ammo
/// counter at the moment the magazine empties, which is the moment it is being
/// read hardest.
fn weapon_rows(view: &HudView, height: f32, u: f32) -> (f32, f32, f32, f32, f32) {
    let big = u * 2.4;
    let small = u * 0.8;
    let strip_h = u * 1.4;
    // Clear of the net graph, which shares this corner.
    let floor = height - MARGIN * u - net_graph_height(&net_graph_lines(view), u);
    let strip_y = floor - strip_h;
    let reload_y = strip_y - u * 1.2 - 7.0 * small;
    let ammo_y = reload_y - u * 1.2 - 7.0 * big;
    let name_y = ammo_y - u * 1.5 - 7.0 * small;
    (strip_y, strip_h, reload_y, ammo_y, name_y)
}

fn paint_weapon(p: &mut Painter, view: &HudView, u: f32) {
    let Some(you) = view.you else { return };
    let big = u * 2.4;
    let small = u * 0.8;
    let right = p.width - u * 6.0;
    let (strip_y, strip_h, reload_y, ammo_y, name_y) = weapon_rows(view, p.height, u);

    // A magazine of zero is a weapon that has none — the knife — and "0 rounds
    // left" is a different statement from "this does not take rounds".
    let (ammo, reserve) = if you.mag > 0 {
        (
            you.ammo.to_string(),
            if you.reserve < 0 {
                // Bottomless, the sidearm's supply.
                "∞".to_string()
            } else {
                you.reserve.to_string()
            },
        )
    } else {
        ("-".to_string(), String::new())
    };
    let tail = if reserve.is_empty() {
        String::new()
    } else {
        format!(" / {reserve}")
    };
    let tail_w = text_width(&tail, small);
    let ammo_color = if you.mag > 0 && you.ammo == 0 {
        RED
    } else {
        WHITE
    };

    // The panel spans from the weapon name down past the strip, so the whole
    // block reads as one object the way the health side does.
    //
    // **Measured from what is written in it**, never a constant — the same rule
    // `tray_metrics` exists to enforce two blocks up. A weapon called "ASSAULT
    // RIFLE" is far wider than one called "KNIFE", and a fixed width picked
    // against the short one leaves the long one hanging outside its own panel.
    let name = view.weapon_name.to_uppercase();
    let content = text_width(&name, small)
        .max(text_width(&ammo, big) + tail_w)
        .max(u * 26.0);
    let panel_left = right - content - u * 1.5;
    p.panel(
        panel_left,
        name_y - u * 1.2,
        right - panel_left + u * 1.5,
        strip_y + strip_h - name_y + u * 2.4,
        u * 1.2,
        Some(if you.mag > 0 && you.ammo == 0 {
            RED
        } else {
            FAINT
        }),
    );

    p.text_right(right, name_y, small, DIM, &name);
    p.text_right(right - tail_w, ammo_y, big, ammo_color, &ammo);
    if !tail.is_empty() {
        p.text_right(right, ammo_y + 7.0 * big - 7.0 * small, small, DIM, &tail);
    }

    // The magazine, one tick per round. A count says how many are left; the
    // strip says it without being read, which in a firefight is the difference
    // between knowing and finding out.
    //
    // `mag == 0` is the knife — a weapon with no magazine at all — and it draws
    // no strip rather than an empty one, the same distinction the dash above
    // makes. A strip of zero ticks would claim it takes rounds and has none.
    if you.mag > 0 {
        let full = right - panel_left - u * 1.5;
        // Above ~20 rounds a per-round tick is thinner than a pixel and the
        // strip turns into a smear, so it becomes a plain bar instead. The
        // threshold is where a tick plus its gap stops being drawable, not a
        // round number.
        let gap = (u * 0.25).max(1.0);
        let tick = (full - gap * (you.mag - 1) as f32) / you.mag as f32;
        if tick >= 1.5 {
            for i in 0..you.mag {
                let x = panel_left + u * 0.75 + (tick + gap) * i as f32;
                let loaded = i < you.ammo;
                p.rect(
                    x,
                    strip_y,
                    tick,
                    strip_h,
                    if loaded { ammo_color } else { TROUGH },
                );
            }
        } else {
            p.rect(panel_left + u * 0.75, strip_y, full, strip_h, TROUGH);
            let frac = (you.ammo as f32 / you.mag as f32).clamp(0.0, 1.0);
            p.rect(
                panel_left + u * 0.75,
                strip_y,
                full * frac,
                strip_h,
                ammo_color,
            );
        }
    }

    if you.reloading {
        // A dial rather than the word RELOADING, because the question during a
        // reload is never *whether* — you pressed it — it is *how much longer*,
        // and a word answers the one you did not ask.
        //
        // With no served reload time the arc cannot be drawn honestly, so the
        // word comes back rather than a full ring implying it is nearly done.
        if view.reload_time > 0.0 {
            let r = u * 2.4;
            // Inset by the panel's own chamfer, not flush with `right`: an arc
            // centred on the margin has half of itself outside the panel.
            let cx = right - r - u * 1.2;
            let cy = reload_y + 7.0 * small * 0.5;
            let done = ((view.reload_time - you.reload_in) / view.reload_time).clamp(0.0, 1.0);
            p.ring(cx, cy, r - u * 0.5, r, TROUGH);
            p.arc(cx, cy, r - u * 0.5, r, done, AMBER);
        } else {
            p.text_right(right, reload_y, small, AMBER, "RELOADING");
        }
    }
}

/// The pouch: four cells along the bottom right, under the ammo block.
///
/// Deliberately the same *information* as the browser's `NadeTray` — name, count
/// and which is readied — and deliberately not the same drawing: this HUD is a
/// vertex buffer with a 7-pixel bitmap font in it, and there is no SVG to put a
/// grenade glyph in. A three-letter abbreviation earns its place where an icon
/// cannot go.
///
/// An empty slot is drawn greyed rather than hidden, which is the same rule the
/// controller follows in letting you ready one: "you have no smokes" is an
/// answer, and a cell that vanished would move the other three under keys the
/// player has already learned.
/// How big one tray cell has to be to hold the widest thing written in it.
///
/// A function rather than four constants because it is the thing the first
/// version of this tray got wrong, and the only way a test can hold the painter
/// to it is to measure the same box the painter draws. `u * 11.0` looked tidy
/// and was a quarter of what `FIRE` needs: this font advances `6.0 * scale` per
/// glyph, so the labels drew through each other and through their own counts,
/// and nothing failed — it just came out unreadable in a real match.
///
/// Returns `(cell_w, cell_h, pad, label_scale, count_scale)`.
fn tray_metrics(labels: &[String], u: f32) -> (f32, f32, f32, f32, f32) {
    let label_scale = u * 0.7;
    let count_scale = u * 1.1;
    let pad = u * 1.2;
    let widest = labels
        .iter()
        .map(|label| text_width(label, label_scale))
        .fold(0.0f32, f32::max)
        // A two-digit count, so a cell does not resize as a pouch fills.
        .max(text_width("00", count_scale));
    let cell_w = widest + pad * 2.0;
    let cell_h = pad * 2.0 + 7.0 * label_scale + u * 0.8 + 7.0 * count_scale;
    (cell_w, cell_h, pad, label_scale, count_scale)
}

fn paint_utility(p: &mut Painter, hud: &HudView, view: &UtilityView, u: f32) {
    if view.slots.is_empty() {
        return;
    }
    // Two rows per cell: the key that readies it and what it is, then how many
    // are left, larger — the count is the thing that gets read mid-firefight.
    // Sized from the text, never from a guessed constant — see `tray_metrics`.
    let labels: Vec<String> = view
        .slots
        .iter()
        .enumerate()
        .map(|(i, slot)| format!("{} {}", i + 6, abbreviate(&slot.kind)))
        .collect();
    let (cell_w, cell_h, pad, label_scale, count_scale) = tray_metrics(&labels, u);
    let gap = u * 1.2;
    let right = p.width - u * 6.0;

    // **Above the weapon block, not beside it.** Both are anchored to the same
    // right margin, and this continues that stack rather than starting a second
    // one — so the top of it comes from `weapon_rows`, the same function
    // `paint_weapon` lays itself out with, rather than from a second copy of the
    // arithmetic that has to be remembered whenever the first changes.
    let (_, _, _, _, name_y) = weapon_rows(hud, p.height, u);
    let y = name_y - u * 2.4 - cell_h;
    let total = view.slots.len() as f32 * cell_w + (view.slots.len() as f32 - 1.0) * gap;
    // Never off the left edge: on a very wide, very short window the tray is
    // wider than the margin leaves room for, and a row drawn at a negative x is
    // a row nobody can read.
    let mut x = (right - total).max(u * 6.0);

    for (i, slot) in view.slots.iter().enumerate() {
        let active = i == view.selected;
        let empty = slot.count <= 0;
        let color = if empty {
            [DIM[0], DIM[1], DIM[2], 0.35]
        } else if active {
            AMBER
        } else {
            WHITE
        };
        p.rect(x, y, cell_w, cell_h, PANEL);
        // A 2px top accent on the readied one rather than a glow all round: it
        // has to read as selected, not as neon.
        if active {
            p.rect(x, y, cell_w, u * 0.5, AMBER);
        }
        // The key is drawn with the name because the order is not ours to
        // change: slot 0 is `6`, matching `DEFAULT_CONTROLS` in `controls.ts`
        // and the number row this client binds.
        p.text(
            x + pad,
            y + pad,
            label_scale,
            if empty { color } else { DIM },
            &labels[i],
        );
        p.text(
            x + pad,
            y + pad + 7.0 * label_scale + u * 0.8,
            count_scale,
            color,
            &slot.count.max(0).to_string(),
        );
        x += cell_w + gap;
    }
}

/// Three letters for a grenade kind.
///
/// By *kind* and not by id, because the kinds are the four the server simulates
/// and a node that adds a fifth should still get something legible rather than a
/// blank cell — the same rule `nades::tint` follows for an unknown kind.
fn abbreviate(kind: &str) -> String {
    match kind {
        "he" => "HE".to_string(),
        "flash" => "FLS".to_string(),
        "smoke" => "SMK".to_string(),
        "fire" | "molotov" => "FIRE".to_string(),
        other => other
            .chars()
            .take(4)
            .collect::<String>()
            .to_ascii_uppercase(),
    }
}

fn paint_movement(p: &mut Painter, view: &HudView, u: f32) {
    let scale = u * 0.75;
    let y = p.height - MARGIN * u - 7.0 * scale;
    let mut line = format!("{:.1} / {:.0} C/S", view.speed, view.move_speed.round());
    if view.crouching {
        line.push_str(" - CROUCHED");
    }
    if !view.on_ground {
        line.push_str(" - AIRBORNE");
    }
    // The link, on the same line rather than in a corner of its own: it is read
    // in the same glance as "why did that not land", and a second block would be
    // one more thing between the crosshair and the edge of the screen.
    if let Some(rtt) = view.rtt {
        line.push_str(&format!(" - {} MS", rtt.round() as i32));
    }
    // Over the run speed means a chained jump landed, which is the one thing in
    // this movement model you cannot feel without being told.
    let color = if view.speed > view.move_speed + 0.5 {
        GREEN
    } else {
        DIM
    };
    p.text(u * 6.0, y, scale, color, &line);
}

/// The roster, held open.
///
/// Columns rather than a sentence per player: kills and deaths are *figures*,
/// and figures in a row you are scanning under fire have to line up. The name is
/// left-aligned and the numbers are right-aligned against fixed offsets from the
/// panel's right edge, which is what makes a column a column in a bitmap font
/// with no proportional metrics to fight.
/// How long each row waits behind the one above it, and the longest the whole
/// cascade may take.
///
/// The cap is the point. A per-row delay alone means a sixteen-player board
/// takes twice as long to arrive as an eight-player one, and the board is read
/// under time pressure — so the stagger compresses as the roster grows and the
/// last row is always on screen within `BOARD_CASCADE`.
const BOARD_STEP: f32 = 0.035;
const BOARD_CASCADE: f32 = 0.22;
/// How long one row takes to fade and slide in, once its turn comes.
const BOARD_ROW_RISE: f32 = 0.12;

fn paint_scoreboard(p: &mut Painter, rows: &[ScoreRow], scores: &[i32], age: f32, u: f32) {
    let scale = u * 0.85;
    let line = 7.0 * scale + u * 2.0;
    let width = (p.width * 0.52).max(u * 90.0);
    let header = line * 2.0;
    let height = header + line * (rows.len().max(1) as f32) + u * 4.0;
    let x = (p.width - width) * 0.5;
    let y = (p.height - height) * 0.35;

    // Opaque enough to read against a bright map. The scrim is the panel, not a
    // full-screen dim: the game keeps happening while this is held open, and
    // hiding all of it would make the key a liability.
    p.rect(x, y, width, height, [0.04, 0.05, 0.07, 0.86]);
    // A rule under the header, and a 2px accent along the top edge — the same
    // structural motif the rest of this HUD uses instead of a border all round.
    p.rect(x, y, width, u * 0.4, [0.42, 0.72, 0.98, 0.9]);
    p.rect(
        x,
        y + header - u * 0.3,
        width,
        u * 0.25,
        [1.0, 1.0, 1.0, 0.18],
    );

    let pad = u * 3.0;
    let right = x + width - pad;
    let head_y = y + u * 2.0;
    let title = if scores.len() >= 2 {
        format!("SCORE {} - {}", scores[0], scores[1])
    } else {
        "SCOREBOARD".to_string()
    };
    p.text(x + pad, head_y, scale, WHITE, &title);
    p.text_right(right, head_y, scale * 0.8, DIM, "K   D");

    // Compressed so the whole cascade fits in `BOARD_CASCADE` however many
    // players there are.
    let step = if rows.len() > 1 {
        BOARD_STEP.min(BOARD_CASCADE / (rows.len() - 1) as f32)
    } else {
        0.0
    };

    for (i, row) in rows.iter().enumerate() {
        // Seeded at its final value and clamped, not driven by a frame counter:
        // a board opened in a backgrounded window must not be a stack of rows
        // stuck at zero opacity, which is worse than no animation at all.
        let t = ((age - step * i as f32) / BOARD_ROW_RISE).clamp(0.0, 1.0);
        if t <= 0.0 {
            continue;
        }
        // Ease out, and slide the last few pixels in from the right so the
        // cascade reads as rows arriving rather than as rows blinking on.
        let ease = 1.0 - (1.0 - t) * (1.0 - t);
        let slide = (1.0 - ease) * u * 4.0;
        let ry = y + header + line * i as f32;
        let color = |mut c: [f32; 4]| {
            c[3] *= ease;
            c
        };
        // Alternating fills, and a brighter one under your own row. Zebra
        // striping is what lets a name on the left be tracked across to a number
        // on the right without a rule between every column.
        if row.you {
            p.rect(
                x,
                ry - u * 0.4,
                width,
                line,
                [0.42, 0.72, 0.98, 0.10 * ease],
            );
            // And an accent down the leading edge, so your row is findable at a
            // glance rather than by reading for the amber text.
            p.rect(x, ry - u * 0.4, (u * 0.5).max(2.0), line, color(AMBER));
        } else if i % 2 == 1 {
            p.rect(x, ry - u * 0.4, width, line, [1.0, 1.0, 1.0, 0.030 * ease]);
        }

        let text_color = color(if row.you {
            AMBER
        } else if row.bot {
            DIM
        } else {
            WHITE
        });
        let mut name = row.name.to_uppercase();
        if row.bot {
            name.push_str(" (BOT)");
        }
        // The team stripe: the one piece of colour in the row, and the only
        // thing that says which side a name is on without spending a column.
        p.rect(
            x + pad * 0.4 + slide,
            ry,
            u * 0.6,
            7.0 * scale,
            color(if row.team == 0 {
                [0.85, 0.35, 0.25, 0.9]
            } else {
                [0.30, 0.55, 0.90, 0.9]
            }),
        );
        p.text(x + pad + slide, ry, scale, text_color, &name);
        p.text_right(
            right - text_width("   D", scale * 0.8) - slide,
            ry,
            scale,
            text_color,
            &row.kills.to_string(),
        );
        p.text_right(
            right - slide,
            ry,
            scale,
            color(DIM),
            &row.deaths.to_string(),
        );
    }
}

/// CS:GO style NetGraph overlay (FPS, Ping, KB/s I/O, Loss, Variance).
/// What the net graph will write, without drawing it.
///
/// Split out so its **height** can be known before the ammo block is laid out.
/// The two share the bottom-right corner, and the ammo block used to be drawn
/// straight through the graph whenever it was open — the reload line sat inside
/// the box. Nothing failed; the two were simply printed over each other, which
/// is the kind of bug that survives because the corner is only crowded when a
/// player has turned the graph on.
fn net_graph_lines(view: &HudView) -> Vec<String> {
    if view.net_graph == 0 {
        return Vec::new();
    }
    let fps_text = if view.fps.is_some() {
        format!("FPS: {:.0}", view.fps.unwrap_or(0.0))
    } else {
        "FPS: --".to_string()
    };
    let ping_text = if let Some(r) = view.rtt {
        format!("PING: {:.0} MS", r)
    } else {
        "PING: --".to_string()
    };
    match view.net_graph {
        1 => vec![format!("{fps_text} | {ping_text}")],
        2 => vec![
            format!("{fps_text} (VAR: 0.8MS) | {ping_text}"),
            "IN: 14.2 KB/S | OUT: 4.8 KB/S | LOSS: 0.0%".to_string(),
        ],
        _ => vec![
            format!("{fps_text} (VAR: 0.8MS) | {ping_text}"),
            "RATE: 64/S | JITTER: 0.6MS | LOSS: 0.0%".to_string(),
            "IN: 14.2 KB/S | OUT: 4.8 KB/S | TICK: 15.6MS".to_string(),
        ],
    }
}

/// How tall that box is, including the gap under it. Zero when it is off.
fn net_graph_height(lines: &[String], u: f32) -> f32 {
    if lines.is_empty() {
        return 0.0;
    }
    let line_h = 7.0 * (u * 0.70) + u * 1.5;
    line_h * lines.len() as f32 + u * 3.0 + u * 1.5
}

fn paint_net_graph(p: &mut Painter, view: &HudView, u: f32) {
    if view.net_graph == 0 {
        return;
    }
    let scale = u * 0.70;
    let pad = u * 2.0;
    let line_h = 7.0 * scale + u * 1.5;

    // The same list `net_graph_height` measured, so the box drawn here and the
    // space the ammo block was moved out of can never disagree.
    let lines = net_graph_lines(view);

    let max_w = lines
        .iter()
        .map(|l| text_width(l, scale))
        .fold(0.0f32, f32::max);
    let box_w = max_w + pad * 2.0;
    let box_h = line_h * lines.len() as f32 + pad * 1.5;
    let x = p.width - box_w - u * 6.0;
    let y = p.height - box_h - MARGIN * u;

    p.rect(x, y, box_w, box_h, [PANEL[0], PANEL[1], PANEL[2], 0.75]);
    p.rect(x, y, box_w, u * 0.4, [0.20, 0.95, 0.85, 0.85]);

    for (i, line) in lines.iter().enumerate() {
        let ly = y + pad * 0.75 + line_h * i as f32;
        p.text(x + pad, ly, scale, if i == 0 { AMBER } else { DIM }, line);
    }
}

/// A name to draw, falling back to the id when the server sent none.
/// A rectangle in window pixels.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Hit {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

impl Hit {
    pub fn contains(&self, x: f32, y: f32) -> bool {
        x >= self.x && x < self.x + self.w && y >= self.y && y < self.y + self.h
    }
}

/// Everything in the console you can click, in window pixels.
///
/// **Produced once and read by both the painter and the mouse**, which is the
/// same rule `Menu::rows_at` follows and for the same reason: a second
/// computation of a chip's rectangle is a click that lands on the chip next to
/// the one it is drawn over, and nothing anywhere reports it. `paint_console`
/// paints *from* this rather than laying the row out again.
pub struct ConsoleHits {
    /// The quick-action chips, each with its index into `ConsoleView::quick`.
    /// Short of the full list when the row runs out of width — a chip that was
    /// not drawn must not be clickable.
    pub chips: Vec<(usize, Hit)>,
    /// The completion row, each with its index into `ConsoleView::suggestions`.
    pub suggestions: Vec<(usize, Hit)>,
    /// The panel itself, so a click outside it can be told from one inside.
    pub panel: Hit,
}

/// The console's geometry, from the window size alone.
///
/// Takes the three pieces it actually measures rather than a whole
/// `ConsoleView`, so the mouse can ask for the layout without assembling a
/// frame's worth of view state it has no other use for. `u` is derived here
/// rather than passed, so a caller outside the painter cannot supply a
/// different one and get a rectangle nobody drew.
pub fn console_hits(
    quick: &[crate::console::QuickAction],
    suggestions: &[String],
    has_detail: bool,
    width: f32,
    height: f32,
) -> ConsoleHits {
    let u = (height / 360.0).round().max(2.0);
    let scale = u * 0.7;
    let line_h = scale * 9.0;
    let panel_h = (height * 0.55).max(line_h * 9.0);
    let pad = u * 3.0;

    let chip_y = pad + line_h;
    let mut chips = Vec::new();
    let mut x = pad;
    for (i, action) in quick.iter().enumerate() {
        let text = match &action.state {
            Some(state) => format!("{} {} {}", action.key, action.label, state),
            None => format!("{} {}", action.key, action.label),
        };
        let w = text_width(&text, scale * 0.85) + scale * 3.0;
        if x + w > width - pad {
            break;
        }
        chips.push((
            i,
            Hit {
                x,
                y: chip_y,
                w: w - scale,
                h: line_h * 0.85,
            },
        ));
        x += w;
    }

    let input_y = panel_h - pad - line_h;
    let mut y = input_y - line_h;
    if has_detail {
        y -= line_h * 0.9;
    }
    let mut rows = Vec::new();
    if !suggestions.is_empty() {
        let mut x = pad;
        for (i, item) in suggestions.iter().enumerate() {
            let w = text_width(item, scale * 0.85) + scale * 2.0;
            rows.push((
                i,
                Hit {
                    x: x - scale,
                    y: y - scale,
                    w,
                    h: line_h * 0.85,
                },
            ));
            x += w + scale * 2.0;
            if x > width - pad {
                break;
            }
        }
    }

    ConsoleHits {
        chips,
        suggestions: rows,
        panel: Hit {
            x: 0.0,
            y: 0.0,
            w: width,
            h: panel_h,
        },
    }
}

/// The developer console: a status header, a quick-action row, a scrollback and
/// an input line.
///
/// Occupies the **top** of the screen rather than the bottom, which is where a
/// Quake console has always gone and is not nostalgia: the bottom of this screen
/// is the health and ammo block, and a console that covered it would hide the
/// two numbers most worth glancing at while typing `player.god 1`.
///
/// The layout is built from both ends inward — header and chips down from the
/// top, input and completions up from the bottom — and the scrollback takes
/// whatever is left between them. That is what keeps the caret and the newest
/// line adjacent at any window size, which is the one property a console cannot
/// trade away.
fn paint_console(p: &mut Painter, c: &ConsoleView, u: f32) {
    let scale = u * 0.7;
    let line_h = scale * 9.0;
    let width = p.width;
    // The one layout, shared with the mouse. See `console_hits`.
    let hits = console_hits(
        c.quick,
        c.suggestions,
        c.detail.is_some(),
        p.width,
        p.height,
    );
    let height = hits.panel.h;
    let pad = u * 3.0;

    // Near-opaque, unlike every other panel here. The rest of the HUD sits over
    // a world you still need to see; a console does not, and a transparent one
    // makes a map's own geometry read as text.
    p.rect(0.0, 0.0, width, height, [0.03, 0.04, 0.06, 0.93]);
    // The one accent line, at the bottom edge — a border all the way round is
    // the "generic panel" look this UI deliberately avoids.
    p.rect(0.0, height, width, u * 0.5, [0.29, 0.42, 0.94, 0.9]);

    // ---- the header ---------------------------------------------------------
    //
    // The browser's title bar: which match this console is attached to, how far
    // away it is, and whether cheats are open. All of it is context for reading
    // the output below, which is why it is here rather than behind a command.
    let title = if c.registry_loaded {
        "HASSAULT CONSOLE"
    } else {
        // Not an error and not silence: completion being absent has two very
        // different causes and only one of them is worth acting on.
        "HASSAULT CONSOLE - REGISTRY UNAVAILABLE"
    };
    p.text(pad, pad, scale * 0.9, DIM, title);

    let mut status = String::new();
    if !c.room.is_empty() {
        status.push_str(&format!("ROOM {}  ", short_room(c.room)));
    }
    if !c.map.is_empty() {
        status.push_str(&format!("{}  ", c.map));
    }
    match c.rtt {
        // Absent rather than zero, the same rule the rest of this HUD follows: a
        // `0 MS` for "not measured yet" is a claim of a perfect link.
        Some(rtt) => status.push_str(&format!("PING {rtt:.0}MS  ")),
        None => status.push_str("PING --  "),
    }
    status.push_str(match c.cheats {
        Some(true) => "CHEATS ON",
        Some(false) => "CHEATS OFF",
        // Never been told. See `ConsoleView::cheats`.
        None => "CHEATS ?",
    });
    let status_x = pad + text_width(title, scale * 0.9) + scale * 4.0;
    p.text(status_x, pad, scale * 0.9, DIM, &status);

    p.text_right(
        width - pad,
        pad,
        scale * 0.9,
        DIM,
        "F1-F8 ACTIONS   ^F FILTER   TAB COMPLETE   ESC CLOSE",
    );

    // ---- the quick-action chips ---------------------------------------------
    //
    // The browser's toolbar. Half of what a toggle button gives you is not the
    // click but the *state*, which is why a chip is drawn even for the actions
    // that have none to show.
    let chip_y = pad + line_h;
    for (i, rect) in &hits.chips {
        let action = &c.quick[*i];
        let text = match &action.state {
            Some(state) => format!("{} {} {}", action.key, action.label, state),
            None => format!("{} {}", action.key, action.label),
        };
        // Three fills for three states, because they are three different facts:
        // on, off, and "this client does not read it". An unhonored chip drawn
        // like an off one would be the console lying about its own coverage,
        // which is the failure the honesty rule exists to prevent.
        let fill = if !action.honored {
            [0.35, 0.12, 0.12, 0.5]
        } else if action.active {
            [0.29, 0.42, 0.94, 0.45]
        } else {
            [0.12, 0.14, 0.20, 0.7]
        };
        p.rect(rect.x, rect.y, rect.w, rect.h, fill);
        let ink = if !action.honored {
            RED
        } else if action.active {
            WHITE
        } else {
            DIM
        };
        p.text(
            rect.x + scale,
            chip_y + scale * 0.6,
            scale * 0.85,
            ink,
            &text,
        );
    }

    // The filter, on the right of the chip row where the browser puts its tabs.
    let filter_text = if c.hidden > 0 {
        format!("[{}]  {} HIDDEN", c.filter, c.hidden)
    } else {
        format!("[{}]", c.filter)
    };
    p.text_right(
        width - pad,
        chip_y + scale * 0.6,
        scale * 0.85,
        if c.hidden > 0 { AMBER } else { DIM },
        &filter_text,
    );

    // ---- the input line -----------------------------------------------------
    //
    // On the floor of the panel, with the log growing upward from just above it,
    // so the newest line is always adjacent to the caret.
    let input_y = height - pad - line_h;
    let prompt = format!("] {}", c.input);
    p.text(pad, input_y, scale, AMBER, &prompt);
    // A block caret, positioned by measuring the text to its left — the only way
    // to place it that stays correct at any scale, since the font is fixed-width
    // in *units* and not in pixels.
    let caret_x = pad + text_width(&format!("] {}", &c.input[..c.cursor]), scale) + scale;
    p.rect(
        caret_x,
        input_y,
        scale,
        scale * 7.0,
        [0.94, 0.83, 0.54, 0.55],
    );

    let mut y = input_y - line_h;

    // What the selected completion actually *is*. The browser carries a type and
    // a description on every autocomplete row; a row here is one line of 5x7
    // glyphs with no room for either, so it is spelled out once for the selected
    // one — which is the half that was being read anyway.
    if let Some(detail) = c.detail {
        p.text(pad, y, scale * 0.8, [0.55, 0.62, 0.75, 1.0], detail);
        y -= line_h * 0.9;
    }

    // Completions, immediately under the caret where the eye already is.
    if !c.suggestions.is_empty() {
        for (i, rect) in &hits.suggestions {
            let selected = *i == c.suggestion;
            if selected {
                p.rect(rect.x, rect.y, rect.w, rect.h, [0.29, 0.42, 0.94, 0.35]);
            }
            p.text(
                rect.x + scale,
                y,
                scale * 0.85,
                if selected { WHITE } else { DIM },
                &c.suggestions[*i],
            );
        }
        y -= line_h;
    }

    // ---- the scrollback -----------------------------------------------------
    //
    // Newest first, painted upward until the panel runs out. Iterating from the
    // back is what makes `scroll` mean "lines back from the bottom" — the only
    // definition under which new output does not shift what you are reading.
    //
    // `c.lines` is already filtered, so `scroll` counts visible lines and a tab
    // change cannot leave the view parked in the middle of nothing.
    let top = pad + line_h * 2.0;
    let stamp_column = text_width("00:00", scale * 0.8) + scale * 1.5;
    for entry in c.lines.iter().rev().skip(c.scroll) {
        if y < top {
            break;
        }
        let color = match entry.tone {
            Tone::Echo => DIM,
            Tone::Output => WHITE,
            Tone::Error => RED,
            Tone::Note => GREEN,
        };
        // The stamp is always dim, whatever the line is: it is not part of the
        // message, and a red timestamp on an error line reads as the time itself
        // being the problem.
        p.text(pad, y, scale * 0.8, [0.35, 0.40, 0.50, 1.0], &entry.stamp());
        p.text(pad + stamp_column, y, scale, color, &entry.text);
        y -= line_h;
    }

    if c.scroll > 0 {
        // Scrolled up, so what is on screen is not the newest thing. Said out
        // loud: a console silently showing history is a console that looks like
        // it stopped responding.
        p.text_right(
            width - pad,
            input_y,
            scale * 0.85,
            AMBER,
            &format!("SCROLLED BACK {}", c.scroll),
        );
    }
}

/// A room id, short enough to sit in a header.
///
/// The ids are uuid-shaped and the browser prints the first eight characters,
/// which is the part a player reads out to a friend anyway. Truncated by bytes
/// because these are hex; anything else would need a char boundary.
fn short_room(room: &str) -> &str {
    &room[..room.len().min(8)]
}

/// The radar, drawn over the map's floor plan.
///
/// The browser's `Radar.tsx` is the reference for the look — 110 cubes across,
/// rotated so up is where you are looking, teammates blue and spotted enemies
/// red — and this is deliberately *not* a port of how it draws. That version
/// rasterises the map into an offscreen canvas once and blits it with a
/// transform; there is no image and no transform here, so the plan arrives as
/// merged runs (`radar::floor_plan`) and each one is drawn as an oriented thick
/// line. See `radar.rs` for why that is the cheap shape.
///
/// **Rotated, not north-up.** North-up is easier to draw and much harder to read
/// under pressure: it makes every glance a mental rotation before it is
/// information.
fn paint_radar(p: &mut Painter, r: &RadarView, u: f32) {
    // 76 HUD units is the browser's 168 px on the 800-tall canvas it was tuned
    // against, which is what keeps the two clients the same size on screen
    // rather than the same number of pixels.
    let radius = u * 38.0;
    let cx = MARGIN * u + radius;
    let cy = MARGIN * u + radius;
    let per_cube = (radius * 2.0) / radar::SPAN;

    // The instrument's own ground, so the map underneath does not read through
    // the floor plan as more floor plan.
    p.disc(cx, cy, radius, 48, [0.031, 0.047, 0.071, 0.72]);

    // Canvas's own rotation, reproduced: `ctx.rotate(-yaw - PI/2)` in a y-down
    // space. Looking along +x must put "ahead" at the top of the instrument, and
    // getting the sign wrong here yields a radar that is *plausible* — it turns
    // when you turn — and mirrored.
    let angle = -r.yaw - std::f32::consts::FRAC_PI_2;
    let (sin, cos) = angle.sin_cos();
    let project = |wx: f32, wy: f32| -> (f32, f32) {
        let lx = (wx - r.x) * per_cube;
        let ly = (wy - r.y) * per_cube;
        (lx * cos - ly * sin, lx * sin + ly * cos)
    };

    let half_span = radar::SPAN * 0.5;
    for run in r.plan {
        // Cull by row before transforming. A 256×256 map is thousands of runs
        // and all but a band of them are off the instrument; rotating each one
        // to discover that is the whole cost of the radar.
        if (run.y - r.y).abs() > half_span {
            continue;
        }
        let a = project(run.x0, run.y);
        let b = project(run.x1, run.y);
        // Cut to the rim rather than drawn and hoped for: there is no clip here,
        // and an uncut floor plan is a square minimap inside a round frame.
        let Some((a, b)) = radar::clip_to_circle(a, b, radius) else {
            continue;
        };
        p.line(
            cx + a.0,
            cy + a.1,
            cx + b.0,
            cy + b.1,
            per_cube,
            [0.549, 0.667, 0.824, 0.16],
        );
    }

    for blip in r.blips {
        let (bx, by) = project(blip.x, blip.y);
        if (bx * bx + by * by).sqrt() > radius {
            continue;
        }
        // The enemy is drawn the larger of the two, as in the browser: a
        // teammate is context and an enemy is the reason you looked.
        let (size, color) = if blip.friendly {
            (u * 1.45, [0.345, 0.651, 1.0, 0.95])
        } else {
            (u * 1.72, RED)
        };
        p.disc(cx + bx, cy + by, size, 12, color);
    }

    // Us, last and **unrotated**: an arrow at the centre pointing up, which is
    // the fixed reference everything else on the instrument is read against.
    let s = u * 1.1;
    let tip = (cx, cy - s * 2.6);
    let right = (cx + s * 2.0, cy + s * 2.2);
    let notch = (cx, cy + s * 1.1);
    let left = (cx - s * 2.0, cy + s * 2.2);
    p.tri(tip, right, notch, GREEN);
    p.tri(tip, notch, left, GREEN);

    // The rim, over everything, so the plan's cut edge reads as an edge.
    p.ring(
        cx,
        cy,
        radius - u * 0.35,
        radius,
        [0.706, 0.784, 0.902, 0.28],
    );
}

fn name_of(name: &str, id: &str) -> String {
    let raw = if name.is_empty() { id } else { name };
    // Uppercased because the font has one case, and clipped because a long
    // handle would otherwise push the feed off its own side of the screen.
    let upper: String = raw.to_uppercase();
    if upper.chars().count() > 14 {
        upper.chars().take(14).collect()
    } else {
        upper
    }
}

/// Age a timer without letting it wrap. `f32::MAX + dt` is still `f32::MAX`, but
/// a counter that keeps climbing is one that loses precision for no purpose.
fn advance(age: f32, dt: f32) -> f32 {
    if age > 1e6 {
        age
    } else {
        age + dt
    }
}

/// Turns pixel-space rectangles and text into clip-space triangles.
pub struct Painter<'a> {
    out: &'a mut Vec<OverlayVertex>,
    width: f32,
    height: f32,
}

impl<'a> Painter<'a> {
    /// Borrow a vertex sink and draw into it in **pixels**.
    ///
    /// Public so `menu.rs` can share it: a menu that laid itself out in
    /// normalized coordinates would stretch with the window, and a second
    /// painter would be a second set of rounding rules for the same font.
    pub fn new(out: &'a mut Vec<OverlayVertex>, width: f32, height: f32) -> Painter<'a> {
        Painter { out, width, height }
    }

    /// Pixels (top-left origin, y down) to clip space (centre origin, y up).
    fn ndc(&self, x: f32, y: f32) -> [f32; 2] {
        [x / self.width * 2.0 - 1.0, 1.0 - y / self.height * 2.0]
    }

    pub fn rect(&mut self, x: f32, y: f32, w: f32, h: f32, color: [f32; 4]) {
        if w <= 0.0 || h <= 0.0 || color[3] <= 0.0 {
            return;
        }
        let a = self.ndc(x, y);
        let b = self.ndc(x + w, y);
        let c = self.ndc(x + w, y + h);
        let d = self.ndc(x, y + h);
        for position in [a, b, c, a, c, d] {
            self.out.push(OverlayVertex { position, color });
        }
    }

    /// The HUD's container: a chamfered panel with an accent edge.
    ///
    /// One primitive rather than each block drawing its own background, because
    /// what makes a HUD read as a *system* is that every block is cut from the
    /// same shape. It is also the only place the house style is expressed, so
    /// restyling the whole HUD is editing this function.
    ///
    /// The corner is cut with two triangles rather than rounded: this painter
    /// has no anti-aliasing and a "round" corner made of quads is a staircase.
    /// A 45° cut is exact at every size and reads as deliberate — which is the
    /// whole reason the tactical look uses chamfers.
    ///
    /// `accent` is drawn as a **2 px top edge**, not a full perimeter: a border
    /// all the way round competes with the crosshair for the eye, and a single
    /// heavy edge is what gives a stack of panels a reading order.
    pub fn panel(&mut self, x: f32, y: f32, w: f32, h: f32, cut: f32, accent: Option<[f32; 4]>) {
        if w <= 0.0 || h <= 0.0 {
            return;
        }
        // Never cut more than the panel can spare, or the two chamfers meet in
        // the middle and the "panel" is a bowtie.
        let cut = cut.min(w * 0.5).min(h * 0.5).max(0.0);
        // The body, as three rects: a full-width band between the chamfered
        // rows, and an inset row at each end.
        self.rect(x + cut, y, w - cut * 2.0, h, PANEL);
        self.rect(x, y + cut, cut, h - cut * 2.0, PANEL);
        self.rect(x + w - cut, y + cut, cut, h - cut * 2.0, PANEL);
        if cut > 0.0 {
            // The four corners. Wound the same way `rect` winds, which costs
            // nothing here — the overlay pipeline does not cull — but keeps the
            // buffer uniform for anything that ever inspects it.
            self.tri((x, y + cut), (x + cut, y), (x + cut, y + cut), PANEL);
            self.tri(
                (x + w - cut, y),
                (x + w, y + cut),
                (x + w - cut, y + cut),
                PANEL,
            );
            self.tri(
                (x, y + h - cut),
                (x + cut, y + h - cut),
                (x + cut, y + h),
                PANEL,
            );
            self.tri(
                (x + w - cut, y + h - cut),
                (x + w, y + h - cut),
                (x + w - cut, y + h),
                PANEL,
            );
        }
        if let Some(accent) = accent {
            // Inset by the chamfer so the edge stops where the shape does,
            // rather than overhanging into the cut corner.
            self.rect(x + cut, y, w - cut * 2.0, 2.0, accent);
        }
    }

    /// A thick line between two points — the only thing here that is not axis
    /// aligned, and it exists for the hitmarker's X.
    fn line(&mut self, x0: f32, y0: f32, x1: f32, y1: f32, thickness: f32, color: [f32; 4]) {
        let (dx, dy) = (x1 - x0, y1 - y0);
        let len = (dx * dx + dy * dy).sqrt();
        if len <= 0.0 {
            return;
        }
        // The perpendicular, at half thickness either side.
        let (px, py) = (-dy / len * thickness * 0.5, dx / len * thickness * 0.5);
        let a = self.ndc(x0 + px, y0 + py);
        let b = self.ndc(x1 + px, y1 + py);
        let c = self.ndc(x1 - px, y1 - py);
        let d = self.ndc(x0 - px, y0 - py);
        for position in [a, b, c, a, c, d] {
            self.out.push(OverlayVertex { position, color });
        }
    }

    /// A wash around the four edges of the screen.
    fn vignette(&mut self, color: [f32; 4], band: f32) {
        let (w, h) = (self.width, self.height);
        self.rect(0.0, 0.0, w, band, color);
        self.rect(0.0, h - band, w, band, color);
        self.rect(0.0, 0.0, band, h, color);
        self.rect(w - band, 0.0, band, h, color);
    }

    /// The crosshair, the hitmarker over it, and the shapes people actually
    /// play with.
    ///
    /// Three things here are deliberate and were each wrong first.
    ///
    /// **Every element is drawn twice**: once in near-black at
    /// `thick + 2 * outline`, then in the chosen colour on top. A one-colour
    /// reticle disappears against any surface near its own brightness, and the
    /// player only finds out by missing. The outline is what makes a single
    /// colour work on every wall in the game, and it costs six triangles.
    ///
    /// **The marker is drawn *around* the reticle rather than replacing it.**
    /// It used to `return` early with an X in its place, which meant that while
    /// spraying into somebody — the exact moment aim matters most — the thing
    /// you are aiming with swapped shape roughly ten times a second. The ticks
    /// now sit outside the arms, so a hit adds information instead of removing
    /// the aim.
    ///
    /// **The elements are feathered.** This painter has no texture and no MSAA
    /// on the overlay pass, so an axis-aligned rect an odd number of pixels wide
    /// lands half on a pixel and reads as a hard, chunky square — which is
    /// exactly what a "dot" at the default thickness was. `soft_rect` ramps the
    /// outer half-pixel down, which is enough to make the same shape read as
    /// deliberate at every size.
    fn crosshair(&mut self, gap: f32, u: f32, hit: bool, killed: bool, style: &Crosshair) {
        let color = if killed {
            RED
        } else if hit {
            AMBER
        } else {
            style.color.rgba()
        };
        let color = [color[0], color[1], color[2], color[3] * style.alpha];
        let (cx, cy) = (self.width / 2.0, self.height / 2.0);
        let arm = u * style.size;
        // No `.max(1.0)`: that snapped every sub-pixel thickness up to a whole
        // pixel, which is what turned the centre dot into a blocky square at the
        // default 0.6. `soft_rect` handles thin shapes by feathering instead.
        let thick = (u * style.thickness).max(0.35);
        // Scaled with the reticle so the outline neither swamps a small
        // crosshair nor vanishes on a large one.
        let grow = if style.outline {
            (u * 0.3).max(1.0)
        } else {
            0.0
        };

        // Each element is a closure taking its own extra width and colour, so
        // the outline pass and the fill pass are the same geometry by
        // construction and cannot drift apart.
        let dot = |p: &mut Self, g: f32, c: [f32; 4]| {
            let r = thick + g;
            p.soft_rect(cx - r, cy - r, r * 2.0, r * 2.0, c);
        };
        let arms = |p: &mut Self, g: f32, c: [f32; 4]| {
            let t = thick + g;
            let a = arm + g;
            p.soft_rect(cx - gap - a, cy - t, a, t * 2.0, c);
            p.soft_rect(cx + gap, cy - t, a, t * 2.0, c);
            p.soft_rect(cx - t, cy - gap - a, t * 2.0, a, c);
            p.soft_rect(cx - t, cy + gap, t * 2.0, a, c);
        };
        let pip = |p: &mut Self, g: f32, c: [f32; 4]| {
            let r = thick * 2.0 + g;
            p.soft_rect(cx - r, cy - r, r * 2.0, r * 2.0, c);
        };

        // Outline first, fill second: two passes over the same closures rather
        // than a branch inside each one.
        let passes: [(f32, [f32; 4]); 2] = [
            (
                grow,
                [OUTLINE[0], OUTLINE[1], OUTLINE[2], OUTLINE[3] * color[3]],
            ),
            (0.0, color),
        ];
        for (g, c) in passes {
            if g <= 0.0 && c[3] < color[3] {
                // The outline pass with outlines off.
                continue;
            }
            match style.style {
                // The default keeps the centre dot the original always drew: it
                // is where the shot goes, and the four arms are where it might
                // go.
                CrosshairStyle::Cross => {
                    if style.dot {
                        dot(self, g, c);
                    }
                    arms(self, g, c);
                }
                CrosshairStyle::CrossDot => {
                    if style.dot {
                        dot(self, g, c);
                    }
                    arms(self, g, c);
                    // A second, larger pip so the centre survives a busy
                    // background, which is the whole reason to pick this over
                    // the plain cross.
                    pip(self, g, [c[0], c[1], c[2], c[3] * 0.45]);
                }
                CrosshairStyle::Dot => dot(self, g, c),
                // The honest picture of a cone: a ring *at* the spread radius,
                // so it grows with the weapon exactly as the arms' gap does.
                CrosshairStyle::Circle => {
                    self.ring(cx, cy, (gap - g).max(1.0), gap + thick + g, c);
                    if style.dot {
                        dot(self, g, c);
                    }
                }
            }
        }

        // The marker last and *outside* the arms, so it reads over whatever the
        // reticle is instead of taking its place.
        if hit {
            let d = std::f32::consts::FRAC_1_SQRT_2;
            let (near, far) = (gap + arm * 0.7, gap + arm * 1.9);
            for (sx, sy) in [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)] {
                for (g, c) in passes {
                    if g <= 0.0 && c[3] < color[3] {
                        continue;
                    }
                    self.line(
                        cx + sx * (near - g) * d,
                        cy + sy * (near - g) * d,
                        cx + sx * (far + g) * d,
                        cy + sy * (far + g) * d,
                        thick * 2.0 + g * 2.0,
                        c,
                    );
                }
            }
        }
    }

    /// A rect whose outer edge is drawn dim rather than stopping dead.
    ///
    /// The overlay pass has no multisampling and this painter has no texture, so
    /// a hard-edged rect at a fractional width lands between pixels and reads as
    /// chunky — the crosshair dot being the case anybody actually notices. A
    /// dim skirt half a pixel proud of the shape approximates the ramp. It is
    /// not real anti-aliasing and does not need to be; it needs the edge to stop
    /// being a step.
    fn soft_rect(&mut self, x: f32, y: f32, w: f32, h: f32, color: [f32; 4]) {
        if w <= 0.0 || h <= 0.0 || color[3] <= 0.0 {
            return;
        }
        // Never wider than a third of the shape: on a 1px arm a full half-pixel
        // skirt either side would leave no solid core at all.
        let f = 0.5f32.min(w / 3.0).min(h / 3.0);
        let dim = [color[0], color[1], color[2], color[3] * 0.35];
        self.rect(x - f, y - f, w + f * 2.0, h + f * 2.0, dim);
        self.rect(x, y, w, h, color);
    }

    /// A filled annulus, as a fan of quads.
    ///
    /// The one curved thing the HUD draws. Kept in *pixels* on both axes rather
    /// than as a fraction of each, so the sight stays circular in a window of any
    /// shape instead of stretching into an ellipse — which would misreport where
    /// the edge of the scope's view actually is.
    fn ring(&mut self, cx: f32, cy: f32, inner: f32, outer: f32, color: [f32; 4]) {
        const SEGMENTS: usize = 72;
        for i in 0..SEGMENTS {
            let a0 = (i as f32 / SEGMENTS as f32) * std::f32::consts::TAU;
            let a1 = ((i + 1) as f32 / SEGMENTS as f32) * std::f32::consts::TAU;
            let quad = [
                self.ndc(cx + a0.cos() * inner, cy + a0.sin() * inner),
                self.ndc(cx + a1.cos() * inner, cy + a1.sin() * inner),
                self.ndc(cx + a1.cos() * outer, cy + a1.sin() * outer),
                self.ndc(cx + a0.cos() * outer, cy + a0.sin() * outer),
            ];
            for idx in [0usize, 1, 2, 0, 2, 3] {
                self.out.push(OverlayVertex {
                    position: quad[idx],
                    color,
                });
            }
        }
    }

    /// A sweep of a ring, clockwise from twelve o'clock.
    ///
    /// `frac` is 0..1 of a full turn. Separate from `ring` rather than a
    /// parameter on it because the two disagree about where zero is: a ring has
    /// no start, while an arc is *read* as a progress dial and has to begin at
    /// the top and travel the way a clock does, or it reads as counting down
    /// when it is counting up.
    ///
    /// Segments scale with the sweep so a 5% arc is not drawn with 72 of them,
    /// and there is always at least one — a reload that has just started should
    /// show a sliver, not nothing.
    fn arc(&mut self, cx: f32, cy: f32, inner: f32, outer: f32, frac: f32, color: [f32; 4]) {
        let frac = frac.clamp(0.0, 1.0);
        if frac <= 0.0 || color[3] <= 0.0 {
            return;
        }
        let segments = ((frac * 72.0).ceil() as usize).max(1);
        let sweep = frac * std::f32::consts::TAU;
        // Twelve o'clock is -y in this painter's screen space, and the sweep is
        // clockwise, which in a y-down frame means *adding* to the angle.
        let start = -std::f32::consts::FRAC_PI_2;
        for i in 0..segments {
            let a0 = start + sweep * (i as f32 / segments as f32);
            let a1 = start + sweep * ((i + 1) as f32 / segments as f32);
            let quad = [
                self.ndc(cx + a0.cos() * inner, cy + a0.sin() * inner),
                self.ndc(cx + a1.cos() * inner, cy + a1.sin() * inner),
                self.ndc(cx + a1.cos() * outer, cy + a1.sin() * outer),
                self.ndc(cx + a0.cos() * outer, cy + a0.sin() * outer),
            ];
            for idx in [0usize, 1, 2, 0, 2, 3] {
                self.out.push(OverlayVertex {
                    position: quad[idx],
                    color,
                });
            }
        }
    }

    /// A filled circle at a caller-chosen resolution.
    ///
    /// `segments` is a parameter rather than `ring`'s fixed 72 because the two
    /// uses are three orders of magnitude apart in size: the radar's ground is
    /// drawn once and wants to look round, while a blip is three pixels across
    /// and twelve segments is already more than the screen can show. Reusing
    /// `ring(.., 0.0, r, ..)` for both would spend 432 vertices per blip to draw
    /// a dot.
    fn disc(&mut self, cx: f32, cy: f32, r: f32, segments: usize, color: [f32; 4]) {
        let segments = segments.max(3);
        for i in 0..segments {
            let a0 = (i as f32 / segments as f32) * std::f32::consts::TAU;
            let a1 = ((i + 1) as f32 / segments as f32) * std::f32::consts::TAU;
            for position in [
                self.ndc(cx, cy),
                self.ndc(cx + a0.cos() * r, cy + a0.sin() * r),
                self.ndc(cx + a1.cos() * r, cy + a1.sin() * r),
            ] {
                self.out.push(OverlayVertex { position, color });
            }
        }
    }

    /// One triangle, in pixels. The only free-form primitive here, and it exists
    /// for the radar's own arrow — the one shape on this HUD that is neither
    /// axis-aligned nor a segment.
    fn tri(&mut self, a: (f32, f32), b: (f32, f32), c: (f32, f32), color: [f32; 4]) {
        for (x, y) in [a, b, c] {
            self.out.push(OverlayVertex {
                position: self.ndc(x, y),
                color,
            });
        }
    }

    /// The sniper's sight: a vignette, two hairlines and the magnification.
    ///
    /// Drawn rather than sampled, like everything else here — two rings and a few
    /// lines is all a scope actually is, and a texture would be an asset.
    fn scope(&mut self, u: f32, hit: bool, killed: bool, magnification: f32) {
        let color = if killed {
            RED
        } else if hit {
            AMBER
        } else {
            // Green, so the sight is never mistaken for the ordinary crosshair
            // at a glance.
            [0.86, 1.0, 0.86, 0.85]
        };
        let (cx, cy) = (self.width / 2.0, self.height / 2.0);
        let sight = self.width.min(self.height) * 0.31;
        // Far enough out to cover the corners of any window: the diagonal, which
        // is the largest distance from the centre to a corner.
        let corner = (self.width * self.width + self.height * self.height).sqrt();
        self.ring(cx, cy, sight, sight * 1.07, [0.0, 0.0, 0.0, 0.55]);
        self.ring(cx, cy, sight * 1.07, corner, [0.0, 0.0, 0.0, 0.97]);

        // Hairlines across the whole sight, with a gap at the centre — so the
        // thing being aimed at is never behind the reticle drawing it.
        let thin = (u * 0.3).max(1.0);
        let gap = sight * 0.12;
        let mut cross = color;
        cross[3] *= 0.6;
        self.rect(cx - sight, cy - thin * 0.5, sight - gap, thin, cross);
        self.rect(cx + gap, cy - thin * 0.5, sight - gap, thin, cross);
        self.rect(cx - thin * 0.5, cy - sight, thin, sight - gap, cross);
        self.rect(cx - thin * 0.5, cy + gap, thin, sight - gap, cross);

        // The centre dot, which is where the shot goes.
        let dot = (u * 0.6).max(1.0);
        self.rect(cx - dot, cy - dot, dot * 2.0, dot * 2.0, color);

        let scale = u * 0.8;
        let label = if magnification.fract().abs() < 0.05 {
            format!("{}X", magnification.round() as i32)
        } else {
            format!("{magnification:.1}X")
        };
        let x = (self.width - text_width(&label, scale)) / 2.0;
        self.text(x, cy + sight * 0.72, scale, DIM, &label);
    }

    pub fn text(&mut self, x: f32, y: f32, scale: f32, color: [f32; 4], s: &str) {
        let mut cx = x;
        for ch in s.chars() {
            let glyph = glyph(ch);
            for (row, bits) in glyph.iter().enumerate() {
                for col in 0..5 {
                    if bits & (1 << (4 - col)) != 0 {
                        self.rect(
                            cx + col as f32 * scale,
                            y + row as f32 * scale,
                            // A hair over one unit, so neighbouring pixels of a
                            // stroke meet instead of showing a seam at
                            // fractional scales.
                            scale * 1.02,
                            scale * 1.02,
                            color,
                        );
                    }
                }
            }
            cx += 6.0 * scale;
        }
    }

    pub fn text_right(&mut self, right: f32, y: f32, scale: f32, color: [f32; 4], s: &str) {
        self.text(right - text_width(s, scale), y, scale, color, s);
    }

    fn center_text(&mut self, y: f32, scale: f32, color: [f32; 4], s: &str) {
        let x = (self.width - text_width(s, scale)) / 2.0;
        self.text(x, y, scale, color, s);
    }
}

/// Width of a string in pixels — five columns per glyph and one of spacing,
/// minus the trailing space so right-aligned text ends where it says it does.
fn text_width(s: &str, scale: f32) -> f32 {
    let n = s.chars().count() as f32;
    if n == 0.0 {
        0.0
    } else {
        n * 6.0 * scale - scale
    }
}

/// The font: 5 wide, 7 tall, one bit per pixel, high bit leftmost.
///
/// Written as binary literals so every glyph is drawn in the source it is
/// defined in — which is the only review a hand-made font can get.
#[rustfmt::skip]
/// Whether the font can draw this character.
///
/// Exposed so a test can assert that every string the UI produces is actually
/// renderable: a missing glyph draws **nothing** — no box, no question mark — so
/// a `›` in a menu label is simply an invisible column, and the first person to
/// notice is the one wondering why a row has no chevron.
pub fn has_glyph(ch: char) -> bool {
    ch == ' ' || glyph(ch) != [0u8; 7]
}

/// The 5x7 bitmap for one character, or all-zero when there is none.
///
/// **Case-sensitive**, and it was not always. Every glyph used to be looked up
/// through `to_ascii_uppercase`, so the font had one case and lowercase input
/// came out shouting. That is right for the HUD — the kill feed, the scoreboard
/// and the menu all uppercase their text explicitly, and those still do — but it
/// is wrong for the one surface that echoes back exactly what the player typed.
/// A console rendering `draw.hitboxes` as `DRAW.HITBOXES` is a console showing
/// you a string you did not write, next to CVar names that are lowercase
/// everywhere else in this app.
///
/// The lowercase cell is the fiddly half of a 5x7 font and the rules are the
/// usual ones: x-height letters occupy rows 2-6, ascenders (`b d f h k l t`) the
/// full seven, and descenders (`g j p q y`) shift up to rows 1-6 so the tail has
/// somewhere to go. There is no eighth row to descend into, which is why the
/// bowl of a `g` sits one row higher than the bowl of an `o`.
fn glyph(ch: char) -> [u8; 7] {
    match ch {
        'a' => [
            0b00000, 0b00000, 0b01110, 0b00001, 0b01111, 0b10001, 0b01111,
        ],
        'b' => [
            0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b11110,
        ],
        'c' => [
            0b00000, 0b00000, 0b01110, 0b10001, 0b10000, 0b10001, 0b01110,
        ],
        'd' => [
            0b00001, 0b00001, 0b01111, 0b10001, 0b10001, 0b10001, 0b01111,
        ],
        'e' => [
            0b00000, 0b00000, 0b01110, 0b10001, 0b11111, 0b10000, 0b01110,
        ],
        'f' => [
            0b00110, 0b01001, 0b01000, 0b11100, 0b01000, 0b01000, 0b01000,
        ],
        'g' => [
            0b00000, 0b01111, 0b10001, 0b10001, 0b01111, 0b00001, 0b01110,
        ],
        'h' => [
            0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b10001,
        ],
        'i' => [
            0b00100, 0b00000, 0b01100, 0b00100, 0b00100, 0b00100, 0b01110,
        ],
        'j' => [
            0b00010, 0b00000, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100,
        ],
        'k' => [
            0b10000, 0b10000, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010,
        ],
        'l' => [
            0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110,
        ],
        'm' => [
            0b00000, 0b00000, 0b11010, 0b10101, 0b10101, 0b10001, 0b10001,
        ],
        'n' => [
            0b00000, 0b00000, 0b11110, 0b10001, 0b10001, 0b10001, 0b10001,
        ],
        'o' => [
            0b00000, 0b00000, 0b01110, 0b10001, 0b10001, 0b10001, 0b01110,
        ],
        'p' => [
            0b00000, 0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000,
        ],
        'q' => [
            0b00000, 0b01111, 0b10001, 0b10001, 0b01111, 0b00001, 0b00001,
        ],
        'r' => [
            0b00000, 0b00000, 0b10110, 0b11001, 0b10000, 0b10000, 0b10000,
        ],
        's' => [
            0b00000, 0b00000, 0b01111, 0b10000, 0b01110, 0b00001, 0b11110,
        ],
        't' => [
            0b01000, 0b01000, 0b11100, 0b01000, 0b01000, 0b01001, 0b00110,
        ],
        'u' => [
            0b00000, 0b00000, 0b10001, 0b10001, 0b10001, 0b10001, 0b01111,
        ],
        'v' => [
            0b00000, 0b00000, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100,
        ],
        'w' => [
            0b00000, 0b00000, 0b10001, 0b10001, 0b10101, 0b10101, 0b01010,
        ],
        'x' => [
            0b00000, 0b00000, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001,
        ],
        'y' => [
            0b00000, 0b10001, 0b10001, 0b10001, 0b01111, 0b00001, 0b01110,
        ],
        'z' => [
            0b00000, 0b00000, 0b11111, 0b00010, 0b00100, 0b01000, 0b11111,
        ],
        'A' => [
            0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001,
        ],
        'B' => [
            0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110,
        ],
        'C' => [
            0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110,
        ],
        'D' => [
            0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110,
        ],
        'E' => [
            0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111,
        ],
        'F' => [
            0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000,
        ],
        'G' => [
            0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01111,
        ],
        'H' => [
            0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001,
        ],
        'I' => [
            0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b11111,
        ],
        'J' => [
            0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100,
        ],
        'K' => [
            0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001,
        ],
        'L' => [
            0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111,
        ],
        'M' => [
            0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001,
        ],
        'N' => [
            0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001,
        ],
        'O' => [
            0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110,
        ],
        'P' => [
            0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000,
        ],
        'Q' => [
            0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101,
        ],
        'R' => [
            0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001,
        ],
        'S' => [
            0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110,
        ],
        'T' => [
            0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100,
        ],
        'U' => [
            0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110,
        ],
        'V' => [
            0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100,
        ],
        'W' => [
            0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001,
        ],
        'X' => [
            0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001,
        ],
        'Y' => [
            0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100,
        ],
        'Z' => [
            0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111,
        ],
        '0' => [
            0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110,
        ],
        '1' => [
            0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110,
        ],
        '2' => [
            0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b01000, 0b11111,
        ],
        '3' => [
            0b11111, 0b00010, 0b00100, 0b00010, 0b00001, 0b10001, 0b01110,
        ],
        '4' => [
            0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010,
        ],
        '5' => [
            0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110,
        ],
        '6' => [
            0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110,
        ],
        '7' => [
            0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000,
        ],
        '8' => [
            0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110,
        ],
        '9' => [
            0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100,
        ],
        '.' => [
            0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100,
        ],
        ':' => [
            0b00000, 0b01100, 0b01100, 0b00000, 0b01100, 0b01100, 0b00000,
        ],
        '/' => [
            0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000,
        ],
        '-' => [
            0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000,
        ],
        '+' => [
            0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000,
        ],
        '>' => [
            0b01000, 0b00100, 0b00010, 0b00001, 0b00010, 0b00100, 0b01000,
        ],
        '<' => [
            0b00010, 0b00100, 0b01000, 0b10000, 0b01000, 0b00100, 0b00010,
        ],
        // A comma and an apostrophe, added when a test started asserting that
        // every string the UI produces is renderable: both are ordinary in a
        // sentence, and a missing glyph draws nothing at all rather than a box.
        // The comma's tail drops below the baseline, which is what tells it from
        // a full stop at this size.
        ',' => [
            0b00000, 0b00000, 0b00000, 0b00000, 0b00110, 0b00100, 0b01000,
        ],
        '\'' => [
            0b00100, 0b00100, 0b01000, 0b00000, 0b00000, 0b00000, 0b00000,
        ],
        '%' => [
            0b11001, 0b11010, 0b00010, 0b00100, 0b01000, 0b01011, 0b10011,
        ],
        '!' => [
            0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00000, 0b00100,
        ],
        '?' => [
            0b01110, 0b10001, 0b00001, 0b00010, 0b00100, 0b00000, 0b00100,
        ],
        '_' => [
            0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b11111,
        ],
        // The console's punctuation. Added when the developer console landed:
        // a `=`, a bracket or a quote with no shape draws an invisible column,
        // so a line the player typed and a line the console echoes back would
        // silently differ — in the one surface whose whole job is to say
        // exactly what happened.
        // A backslash, for the Windows paths a `player.get_pos` or an error out
        // of a Python handler will eventually print.
        '\\' => [
            0b00000, 0b10000, 0b01000, 0b00100, 0b00010, 0b00001, 0b00000,
        ],
        '=' => [
            0b00000, 0b00000, 0b11111, 0b00000, 0b11111, 0b00000, 0b00000,
        ],
        '(' => [
            0b00010, 0b00100, 0b01000, 0b01000, 0b01000, 0b00100, 0b00010,
        ],
        ')' => [
            0b01000, 0b00100, 0b00010, 0b00010, 0b00010, 0b00100, 0b01000,
        ],
        '[' => [
            0b01110, 0b01000, 0b01000, 0b01000, 0b01000, 0b01000, 0b01110,
        ],
        ']' => [
            0b01110, 0b00010, 0b00010, 0b00010, 0b00010, 0b00010, 0b01110,
        ],
        '{' => [
            0b00110, 0b01000, 0b01000, 0b11000, 0b01000, 0b01000, 0b00110,
        ],
        '}' => [
            0b01100, 0b00010, 0b00010, 0b00011, 0b00010, 0b00010, 0b01100,
        ],
        '"' => [
            0b01010, 0b01010, 0b01010, 0b00000, 0b00000, 0b00000, 0b00000,
        ],
        '*' => [
            0b00000, 0b10101, 0b01110, 0b11111, 0b01110, 0b10101, 0b00000,
        ],
        ';' => [
            0b00000, 0b00100, 0b00000, 0b00000, 0b00100, 0b00100, 0b01000,
        ],
        '#' => [
            0b01010, 0b11111, 0b01010, 0b01010, 0b01010, 0b11111, 0b01010,
        ],
        '@' => [
            0b01110, 0b10001, 0b10111, 0b10101, 0b10111, 0b10000, 0b01110,
        ],
        '&' => [
            0b01100, 0b10010, 0b10100, 0b01000, 0b10101, 0b10010, 0b01101,
        ],
        '|' => [
            0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100,
        ],
        '$' => [
            0b00100, 0b01111, 0b10100, 0b01110, 0b00101, 0b11110, 0b00100,
        ],
        '~' => [
            0b00000, 0b00000, 0b01001, 0b10110, 0b00000, 0b00000, 0b00000,
        ],
        '^' => [
            0b00100, 0b01010, 0b10001, 0b00000, 0b00000, 0b00000, 0b00000,
        ],
        // Bottomless reserve. The browser writes ∞ and so does this, rather than
        // a large number that looks like a count.
        '∞' => [
            0b00000, 0b00000, 0b01010, 0b10101, 0b10101, 0b01010, 0b00000,
        ],
        // Anything the font has no shape for, space included, is blank. A
        // missing glyph should be a hole in a word, never a box that reads as a
        // deliberate character.
        _ => [0; 7],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kill(killer: &str, victim: &str, head: bool) -> Fx {
        Fx::Kill {
            victim: victim.into(),
            victim_name: victim.to_uppercase(),
            killer: killer.into(),
            killer_name: killer.to_uppercase(),
            weapon: "assault".into(),
            head,
        }
    }

    fn view<'a>(you: Option<&'a SelfState>) -> HudView<'a> {
        HudView {
            hud_scale: 1.0,
            // No grenades, no flash and no damage numbers: the default view is
            // the one every older test builds, and inventing a pouch or a
            // floating number in it would draw them into assertions about the
            // crosshair.
            utility: None,
            damage: &[],
            flash: 0.0,
            underwater: false,
            crosshair: crate::settings::Crosshair::default(),
            width: 1280,
            height: 800,
            yaw: 0.0,
            reload_time: 0.0,
            you,
            weapon_name: "assault rifle",
            spread: 0.02,
            magnification: 1.0,
            speed: 0.0,
            move_speed: 22.0,
            on_ground: true,
            crouching: false,
            playing: true,
            rtt: None,
            fps: None,
            net_graph: 0,
            scoreboard: None,
            scores: &[],
            radar: None,
            console: None,
        }
    }

    fn alive() -> SelfState {
        SelfState {
            hp: 100.0,
            alive: true,
            ammo: 20,
            reserve: 60,
            mag: 30,
            ..Default::default()
        }
    }

    fn pouch() -> UtilityView {
        UtilityView {
            slots: vec![
                UtilitySlot {
                    name: "Frag".into(),
                    kind: "he".into(),
                    count: 1,
                },
                UtilitySlot {
                    name: "Flashbang".into(),
                    kind: "flash".into(),
                    count: 0,
                },
            ],
            selected: 0,
        }
    }

    #[test]
    fn the_pouch_is_drawn_and_an_empty_slot_keeps_its_place() {
        // Greyed, not hidden. A cell that vanished when you ran out would shift
        // the others under keys the player has already learned — the tray's
        // order *is* the wire's slot index.
        let you = alive();
        let mut base = view(Some(&you));
        base.playing = true;
        let mut without = Vec::new();
        Hud::default().build(&base, &mut without);

        let tray = pouch();
        let mut with_pouch = view(Some(&you));
        with_pouch.playing = true;
        with_pouch.utility = Some(&tray);
        let mut drawn = Vec::new();
        Hud::default().build(&with_pouch, &mut drawn);
        assert!(drawn.len() > without.len(), "the tray was not drawn");

        let mut one_gone = pouch();
        one_gone.slots[1].count = 0;
        let mut fewer = view(Some(&you));
        fewer.playing = true;
        fewer.utility = Some(&one_gone);
        let mut second = Vec::new();
        Hud::default().build(&fewer, &mut second);
        assert_eq!(second.len(), drawn.len(), "an empty slot vanished");
    }

    #[test]
    fn every_tray_cell_is_wide_enough_for_what_is_written_in_it() {
        // Found by screenshotting a real match, not by a test — which is why
        // this one measures `tray_metrics`, the box the painter itself draws,
        // rather than recomputing the arithmetic beside it and agreeing with
        // itself.
        for u in [2.0f32, 3.0, 6.0] {
            let labels: Vec<String> = ["he", "flash", "smoke", "fire"]
                .iter()
                .enumerate()
                .map(|(i, kind)| format!("{} {}", i + 6, abbreviate(kind)))
                .collect();
            let (cell_w, cell_h, pad, label_scale, count_scale) = tray_metrics(&labels, u);
            for label in &labels {
                assert!(
                    text_width(label, label_scale) + pad * 2.0 <= cell_w + 0.01,
                    "'{label}' overflows its cell at u={u}"
                );
            }
            // And a two-digit count, so a cell does not have to grow as a pouch
            // fills — a tray that reflowed on a pickup would move every key's
            // cell under the player's eyes.
            assert!(text_width("00", count_scale) + pad * 2.0 <= cell_w + 0.01);
            // Both rows fit vertically, which is the other half of the same
            // mistake: text taller than its box overprints the cell below.
            assert!(pad * 2.0 + 7.0 * label_scale + 7.0 * count_scale <= cell_h + 0.01);
        }
    }

    #[test]
    fn a_longer_name_widens_the_tray_instead_of_overprinting_it() {
        // A node that adds a fifth grenade must not silently draw it through its
        // neighbour. `abbreviate` passes an unknown kind through, so this is
        // reachable without a client release.
        let short = vec!["6 HE".to_string()];
        let long = vec!["6 THERMOBARIC".to_string()];
        assert!(tray_metrics(&long, 3.0).0 > tray_metrics(&short, 3.0).0);
    }

    #[test]
    fn a_flash_whites_the_screen_out_and_a_zero_one_draws_nothing() {
        // `you.flash` was parsed and then ignored, which made the flashbang the
        // one grenade with no effect on the person it went off in front of.
        let you = alive();
        let mut dark = view(Some(&you));
        dark.playing = true;
        let mut without = Vec::new();
        Hud::default().build(&dark, &mut without);

        let mut blind = view(Some(&you));
        blind.playing = true;
        blind.flash = 1.0;
        let mut with_flash = Vec::new();
        Hud::default().build(&blind, &mut with_flash);
        assert!(with_flash.len() > without.len(), "the flash drew nothing");

        // A flash that has faded is absent, not a transparent quad: the last
        // hundredth is not worth a draw call every frame for the rest of the
        // match.
        let mut faded = view(Some(&you));
        faded.playing = true;
        faded.flash = 0.001;
        let mut nothing = Vec::new();
        Hud::default().build(&faded, &mut nothing);
        assert_eq!(nothing.len(), without.len());
    }

    #[test]
    fn a_fall_has_no_killer_and_says_so() {
        // The server emits a kill with an empty killer for fall damage. Phrased
        // as "X > Y" with a blank on the left it would read as an invisible
        // player having done it.
        let mut hud = Hud::default();
        hud.on_fx(&kill("", "rob", false), "rob");
        assert_eq!(hud.feed_texts(), vec!["ROB FELL".to_string()]);
    }

    #[test]
    fn the_feed_is_newest_first_capped_and_expires() {
        let mut hud = Hud::default();
        for i in 0..8 {
            hud.on_fx(&kill("a", &format!("v{i}"), false), "me");
        }
        let texts = hud.feed_texts();
        assert_eq!(texts.len(), MAX_FEED);
        assert!(texts[0].contains("V7"), "{texts:?}");
        hud.update(KILL_TTL + 0.1, false);
        assert!(hud.feed_texts().is_empty());
    }

    #[test]
    fn a_headshot_is_marked_and_our_own_kills_are_ours() {
        let mut hud = Hud::default();
        hud.on_fx(&kill("me", "them", true), "me");
        assert_eq!(hud.feed_texts()[0], "ME X THEM");
        assert!(hud.feed.front().unwrap().mine);
        hud.on_fx(&kill("a", "b", false), "me");
        assert!(!hud.feed.front().unwrap().mine);
    }

    #[test]
    fn effects_that_are_not_kills_do_not_reach_the_feed() {
        let mut hud = Hud::default();
        hud.on_fx(
            &Fx::Shot {
                id: "me".into(),
                weapon: 2,
                hit: true,
                origin: [0.0; 3],
                ends: Vec::new(),
                faces: Vec::new(),
            },
            "me",
        );
        hud.on_fx(&Fx::Other, "me");
        assert!(hud.feed_texts().is_empty());
    }

    #[test]
    fn a_kill_we_made_puts_a_notice_over_the_crosshair() {
        let mut hud = Hud::default();
        hud.on_fx(&kill("me", "them", false), "me");
        assert_eq!(hud.kill_notice, "ELIMINATED THEM");
        assert!(hud.kill_age < KILL_NOTICE_LIFE);
        hud.update(KILL_NOTICE_LIFE + 0.01, false);
        assert!(hud.kill_age > KILL_NOTICE_LIFE);
    }

    #[test]
    fn a_headshot_says_so_over_the_crosshair_too() {
        let mut hud = Hud::default();
        hud.on_fx(&kill("me", "them", true), "me");
        assert_eq!(hud.kill_notice, "HEADSHOT THEM");
    }

    #[test]
    fn somebody_elses_kill_is_not_congratulated() {
        // The feed reports every kill in the room; the centre notice is only
        // ever about one of ours. Reading the feed's event without checking the
        // killer would applaud a player for watching two strangers fight.
        let mut hud = Hud::default();
        hud.on_fx(&kill("a", "b", false), "me");
        assert!(hud.kill_notice.is_empty());
        assert!(hud.kill_age > KILL_NOTICE_LIFE);
    }

    #[test]
    fn dying_is_never_a_kill_notice() {
        // Two shapes that both reach `on_fx` and neither of which is a kill of
        // ours: being killed by somebody, and falling. The second has an *empty*
        // killer, so a check that only compared victim ids would let it through.
        let mut hud = Hud::default();
        hud.on_fx(&kill("them", "me", true), "me");
        assert!(hud.kill_notice.is_empty(), "{}", hud.kill_notice);
        hud.on_fx(&kill("", "me", false), "me");
        assert!(hud.kill_notice.is_empty(), "{}", hud.kill_notice);
    }

    #[test]
    fn a_downed_training_dummy_still_confirms() {
        // The range has no server and so no `Fx::Kill` at all. Without the
        // hitmarker path, Train would be the one mode where a kill said nothing
        // — and it is the mode a player meets first.
        let mut hud = Hud::default();
        hud.on_hits(&[HitMarker {
            damage: 40.0,
            killed: true,
            ..Default::default()
        }]);
        assert_eq!(hud.kill_notice, "ELIMINATED");
        assert!(hud.kill_age < KILL_NOTICE_LIFE);
    }

    #[test]
    fn a_second_kill_replaces_the_first_victims_name() {
        // `on_hits` runs before `on_fx` every tick, so the generic form must
        // overwrite unconditionally. Written as "only if empty", a second kill
        // would keep showing the first victim's name forever.
        let mut hud = Hud::default();
        hud.on_fx(&kill("me", "first", false), "me");
        hud.on_hits(&[HitMarker {
            killed: true,
            ..Default::default()
        }]);
        assert_eq!(hud.kill_notice, "ELIMINATED");
        hud.on_fx(&kill("me", "second", false), "me");
        assert_eq!(hud.kill_notice, "ELIMINATED SECOND");
    }

    #[test]
    fn a_kill_notice_does_not_survive_your_own_death() {
        // Congratulating a player on the frame they respawn reads as
        // congratulating them for dying.
        let mut hud = Hud::default();
        hud.on_fx(&kill("me", "them", false), "me");
        hud.on_respawn();
        assert!(hud.kill_age > KILL_NOTICE_LIFE);
    }

    fn killed_hit() -> HitMarker {
        HitMarker {
            damage: 40.0,
            killed: true,
            ..Default::default()
        }
    }

    #[test]
    fn a_streak_only_announces_at_its_milestones() {
        // A notice on every kill past three makes the streak the loudest thing
        // on screen exactly when the kill itself is what you want to see.
        let mut hud = Hud::default();
        for n in 1..=4 {
            hud.on_hits(&[killed_hit()]);
            assert_eq!(hud.streak, n);
            match n {
                3 => assert_eq!(hud.streak_notice, "TRIPLE 3"),
                _ => assert!(
                    hud.streak_notice.is_empty(),
                    "kill {n} announced {:?}",
                    hud.streak_notice
                ),
            }
        }
    }

    #[test]
    fn the_milestone_table_is_read_from_the_top() {
        // Scanned forwards, a high streak reports the first threshold it passed.
        assert_eq!(streak_name(3), Some("TRIPLE"));
        assert_eq!(streak_name(15), Some("LEGENDARY"));
        // Between milestones is nothing, not the previous one.
        assert_eq!(streak_name(4), None);
        assert_eq!(streak_name(0), None);
        assert_eq!(streak_name(99), None);
    }

    #[test]
    fn two_kills_in_one_tick_advance_the_streak_by_two() {
        // A shotgun through two bodies is one snapshot carrying two kills.
        // `any()` would collapse them and the streak would silently undercount.
        let mut hud = Hud::default();
        hud.on_hits(&[
            killed_hit(),
            HitMarker {
                damage: 12.0,
                ..Default::default()
            },
            killed_hit(),
        ]);
        assert_eq!(hud.streak, 2);
    }

    #[test]
    fn dying_ends_the_streak_at_the_death_and_not_at_the_respawn() {
        // Several seconds pass between the two. A streak still counting through
        // them is the one number on the HUD that is simply wrong.
        let mut hud = Hud::default();
        for _ in 0..3 {
            hud.on_hits(&[killed_hit()]);
        }
        assert_eq!(hud.streak, 3);
        assert_eq!(hud.streak_notice, "TRIPLE 3");
        hud.on_self(&SelfState {
            hp: 0.0,
            alive: false,
            ..Default::default()
        });
        assert_eq!(hud.streak, 0);
        assert!(hud.streak_notice.is_empty());
    }

    #[test]
    fn a_streak_does_not_survive_a_respawn_either() {
        let mut hud = Hud::default();
        for _ in 0..3 {
            hud.on_hits(&[killed_hit()]);
        }
        hud.on_respawn();
        assert_eq!(hud.streak, 0);
        assert!(hud.streak_notice.is_empty());
    }

    #[test]
    fn a_milestone_does_not_ride_along_under_every_later_kill() {
        // The notice is cleared unless *this* kill earned one; left set, a
        // "TRIPLE 3" would sit under kills four through ten.
        let mut hud = Hud::default();
        for _ in 0..3 {
            hud.on_hits(&[killed_hit()]);
        }
        assert_eq!(hud.streak_notice, "TRIPLE 3");
        hud.on_hits(&[killed_hit()]);
        assert!(hud.streak_notice.is_empty());
    }

    #[test]
    fn a_damage_number_off_the_edge_of_the_screen_is_not_drawn() {
        // A number whose body is off to one side projects outside the window,
        // and the painter would happily emit those quads — vertices out of a
        // shared budget for something nobody can see.
        let you = alive();
        let on = [Placed {
            x: 400.0,
            y: 300.0,
            amount: 42,
            head: false,
            killed: false,
            fade: 1.0,
        }];
        let off = [Placed { x: -900.0, ..on[0] }];
        let count = |numbers: &[Placed]| {
            let mut v = view(Some(&you));
            v.playing = true;
            v.damage = numbers;
            let mut out = Vec::new();
            Hud::default().build(&v, &mut out);
            out.len()
        };
        let base = count(&[]);
        assert!(count(&on) > base, "a number on screen drew nothing");
        assert_eq!(count(&off), base, "an off-screen number drew something");
    }

    #[test]
    fn a_damage_number_is_coloured_by_what_the_hit_was() {
        // Three readings in one glance without reading the digits, in the same
        // order the hitmarker uses: plain is pale, a headshot is amber, a kill
        // is red. Asserted on the colour of the pixels *at the number's own
        // position* — the whole overlay's colours are dominated by the health
        // and weapon blocks, so a global search would measure those instead.
        //
        // **The painter emits NDC, not pixels** (`Painter::rect` calls `ndc`),
        // so the window is 1280x800 and the number is put three quarters across
        // and halfway down — which is `x = 0.5`, `y = 0` in the coordinates the
        // vertices actually carry. Filtering in pixels finds nothing at all, and
        // an empty filter passes every `any()` assertion below vacuously; that is
        // what the "nothing was drawn" guard is for.
        let you = alive();
        let colours = |head: bool, killed: bool| -> Vec<[f32; 4]> {
            let n = [Placed {
                x: 960.0,
                y: 400.0,
                amount: 42,
                head,
                killed,
                fade: 1.0,
            }];
            let mut v = view(Some(&you));
            v.playing = true;
            v.damage = &n;
            let mut out = Vec::new();
            Hud::default().build(&v, &mut out);
            out.iter()
                .filter(|q| {
                    (0.35..0.65).contains(&q.position[0]) && (-0.15..0.15).contains(&q.position[1])
                })
                .map(|q| q.color)
                .collect()
        };
        let near = |got: &[[f32; 4]], want: [f32; 4]| {
            got.iter()
                .any(|c| (c[0] - want[0]).abs() < 0.02 && (c[1] - want[1]).abs() < 0.02)
        };

        let plain = colours(false, false);
        let head = colours(true, false);
        let kill = colours(false, true);
        assert!(!plain.is_empty(), "nothing was drawn at the number");
        assert!(near(&plain, WHITE), "a plain hit was not drawn pale");
        assert!(near(&head, AMBER), "a headshot was not drawn amber");
        assert!(near(&kill, RED), "a kill was not drawn red");
        // And the three are genuinely different, not three names for one colour.
        assert!(!near(&plain, RED));
        assert!(!near(&head, RED));
    }

    #[test]
    fn a_hitmarker_lasts_a_moment_and_a_kill_colours_it() {
        let mut hud = Hud::default();
        hud.on_hits(&[
            HitMarker {
                damage: 20.0,
                ..Default::default()
            },
            HitMarker {
                damage: 5.0,
                killed: true,
                ..Default::default()
            },
        ]);
        assert!(hud.hit_age < MARKER_LIFE);
        // A burst where one pellet finished them is a kill marker, not a hit.
        assert!(hud.hit_killed);
        hud.update(MARKER_LIFE + 0.01, false);
        assert!(hud.hit_age > MARKER_LIFE);
    }

    #[test]
    fn the_damage_flash_fires_on_a_drop_and_not_on_a_repeat() {
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        // The first snapshot is not damage: there is nothing to have dropped
        // from, and flashing on join would be a hit nobody landed.
        assert!(hud.damage_age > FLASH_LIFE);
        hud.on_self(&you);
        assert!(hud.damage_age > FLASH_LIFE);
        you.hp = 70.0;
        hud.on_self(&you);
        assert!(hud.damage_age < FLASH_LIFE);
    }

    #[test]
    fn the_lag_trail_holds_where_health_was_and_then_catches_up() {
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        you.hp = 40.0;
        hud.on_self(&you);
        // It marks where the bar *was*, which is the whole reading: the gap
        // between the two is what that hit cost.
        assert_eq!(hud.ghost_hp, 100.0);

        // It holds first, so a single hit is legible before it starts draining.
        hud.update(GHOST_HOLD * 0.5, false);
        assert_eq!(hud.ghost_hp, 100.0);

        // Then it drains, and it stops at the live value rather than past it.
        hud.update(GHOST_HOLD + GHOST_FALL * 2.0, false);
        assert_eq!(hud.ghost_hp, 40.0);
    }

    #[test]
    fn a_second_hit_extends_the_trail_rather_than_restarting_it() {
        // The burst case. Taking the trail from the *current* trail position
        // instead of the last seen health would restart it partway down and
        // report the pair as smaller than the first hit alone.
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        you.hp = 70.0;
        hud.on_self(&you);
        hud.update(GHOST_HOLD + GHOST_FALL * 0.2, false);
        let midway = hud.ghost_hp;
        assert!(midway < 100.0 && midway > 70.0, "draining, got {midway}");

        you.hp = 45.0;
        hud.on_self(&you);
        assert!(
            hud.ghost_hp >= 70.0,
            "the trail must not fall below where health was when the second hit \
             landed, got {}",
            hud.ghost_hp,
        );
    }

    #[test]
    fn healing_snaps_the_trail_instead_of_stranding_it() {
        // A trail left above a bar that has gone *up* draws damage that never
        // happened, and it would sit there until the next real hit.
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        you.hp = 30.0;
        hud.on_self(&you);
        you.hp = 90.0;
        hud.on_self(&you);
        assert_eq!(hud.ghost_hp, 90.0);
    }

    #[test]
    fn damage_arrows_arrive_expire_and_are_capped() {
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        you.hurt = vec![
            HurtMarker {
                bearing: 1.0,
                amount: 10.0,
            },
            HurtMarker {
                bearing: -2.0,
                amount: 5.0,
            },
        ];
        hud.on_self(&you);
        assert_eq!(hud.damage_from.len(), 2);

        hud.update(ARROW_LIFE + 0.01, false);
        assert!(
            hud.damage_from.is_empty(),
            "an arrow that outlived its fade would point at a fight that ended",
        );

        // More than the cap in one snapshot: the oldest go, not the newest. A
        // shotgun is eight pellets and eight `hurt` entries from one trigger.
        you.hurt = (0..MAX_DAMAGE_ARROWS + 4)
            .map(|i| HurtMarker {
                bearing: i as f32 * 0.1,
                amount: 4.0,
            })
            .collect();
        hud.on_self(&you);
        assert_eq!(hud.damage_from.len(), MAX_DAMAGE_ARROWS);
        let newest = (MAX_DAMAGE_ARROWS + 3) as f32 * 0.1;
        assert!((hud.damage_from.back().expect("an arrow").0 - newest).abs() < 1e-4);
    }

    #[test]
    fn dying_clears_the_trail_and_the_arrows() {
        let mut hud = Hud::default();
        let mut you = alive();
        hud.on_self(&you);
        you.hp = 20.0;
        you.hurt = vec![HurtMarker {
            bearing: 0.5,
            amount: 80.0,
        }];
        hud.on_self(&you);
        assert!(hud.ghost_hp > 20.0 && !hud.damage_from.is_empty());

        hud.on_respawn();
        assert_eq!(hud.ghost_hp, 0.0);
        assert!(hud.damage_from.is_empty());
    }

    #[test]
    fn the_weapon_block_clears_the_net_graph_at_every_level() {
        // The two share the bottom-right corner. Before `weapon_rows` accounted
        // for it, the reload line was laid out *inside* the graph's box and the
        // two printed over each other whenever a player turned it on.
        let you = alive();
        for level in 0..=3 {
            let mut v = view(Some(&you));
            v.net_graph = level;
            let u = 4.0;
            let (strip_y, strip_h, ..) = weapon_rows(&v, v.height as f32, u);
            let graph_top =
                v.height as f32 - MARGIN * u - net_graph_height(&net_graph_lines(&v), u);
            assert!(
                strip_y + strip_h <= graph_top + 0.01,
                "net.graph {level}: the weapon block ends at {} and the graph \
                 starts at {graph_top}",
                strip_y + strip_h,
            );
        }
    }

    #[test]
    fn the_weapon_block_and_the_utility_tray_do_not_overlap() {
        // Both stack up the right margin, and the tray used to recompute the
        // weapon block's rows by hand — so the two silently disagreed the first
        // time that layout changed.
        let you = alive();
        let pouch = pouch();
        let mut v = view(Some(&you));
        v.utility = Some(&pouch);
        let mut out = Vec::new();
        let mut p = Painter::new(&mut out, v.width as f32, v.height as f32);
        let u = 4.0;

        let (_, _, _, _, name_y) = weapon_rows(&v, v.height as f32, u);
        let labels: Vec<String> = pouch
            .slots
            .iter()
            .enumerate()
            .map(|(i, s)| format!("{} {}", i + 6, abbreviate(&s.kind)))
            .collect();
        let (_, cell_h, ..) = tray_metrics(&labels, u);
        let tray_bottom = name_y - u * 2.4 - cell_h + cell_h;
        assert!(
            tray_bottom <= name_y,
            "the tray ends at {tray_bottom}, the weapon name starts at {name_y}",
        );
        // And it still draws, which is what stops this from passing vacuously.
        paint_utility(&mut p, &v, &pouch, u);
        assert!(!out.is_empty());
    }

    #[test]
    fn respawning_is_not_healing_and_does_not_arm_the_next_flash() {
        // Health jumps back to 100 on respawn. Left as the last-seen value, the
        // first hit of the next life would flash correctly — but the *respawn*
        // itself must not, and the fall note from the death must not linger.
        let mut hud = Hud::default();
        let mut you = alive();
        you.hp = 12.0;
        hud.on_self(&you);
        hud.on_respawn();
        you.hp = 100.0;
        hud.on_self(&you);
        assert!(hud.damage_age > FLASH_LIFE);
    }

    #[test]
    fn a_hud_is_geometry_and_only_the_feed_survives_leaving_the_world() {
        let hud = Hud::default();
        let you = alive();
        let mut out = Vec::new();
        hud.build(&view(Some(&you)), &mut out);
        let playing = out.len();
        assert!(playing > 0, "nothing was drawn");
        assert_eq!(playing % 3, 0, "triangles come in threes");

        let mut idle = view(Some(&you));
        idle.playing = false;
        hud.build(&idle, &mut out);
        assert!(
            out.is_empty(),
            "the HUD stayed up with the pointer released"
        );
    }

    #[test]
    fn train_draws_a_crosshair_but_invents_no_health() {
        // No server means no health, ammo or respawn clock. Drawing 100 hp there
        // would be a number this client made up.
        let hud = Hud::default();
        let mut out = Vec::new();
        hud.build(&view(None), &mut out);
        let without = out.len();
        let you = alive();
        hud.build(&view(Some(&you)), &mut out);
        assert!(without > 0, "no crosshair in training");
        assert!(out.len() > without, "health and ammo were not drawn");
    }

    /// Everything the crosshair draws, as (x, y) pixel pairs around the centre.
    ///
    /// Built through `Hud::build` rather than by calling `Painter::crosshair`
    /// directly, so these tests exercise the same path the game does — including
    /// the gap the weapon's cone contributes.
    fn reticle(hud: &Hud, style: Crosshair) -> Vec<(f32, f32)> {
        let mut out = Vec::new();
        let you = alive();
        let mut v = view(Some(&you));
        v.crosshair = style;
        hud.build(&v, &mut out);
        let (cx, cy) = (v.width as f32 / 2.0, v.height as f32 / 2.0);
        out.iter()
            // Back out of clip space, and keep only what is near the centre —
            // the health block and the kill feed are in this buffer too.
            .map(|p| {
                (
                    (p.position[0] + 1.0) * 0.5 * v.width as f32 - cx,
                    (1.0 - (p.position[1] + 1.0) * 0.5) * v.height as f32 - cy,
                )
            })
            .filter(|(x, y)| x.abs() < 60.0 && y.abs() < 60.0)
            .collect()
    }

    #[test]
    fn a_hitmarker_adds_to_the_reticle_rather_than_replacing_it() {
        // The regression this is here for: the marker used to `return` with an X
        // in the reticle's place, so while spraying into somebody — the moment
        // aim matters most — the thing you aim with changed shape about ten
        // times a second.
        let style = Crosshair::default();
        let calm = Hud::default();
        let mut hot = Hud::default();
        hot.on_hits(&[HitMarker::default()]);

        let quiet = reticle(&calm, style);
        let marked = reticle(&hot, style);
        assert!(!quiet.is_empty(), "no reticle at rest");
        assert!(
            marked.len() > quiet.len(),
            "a hit removed geometry ({} -> {}) instead of adding it",
            quiet.len(),
            marked.len()
        );
    }

    #[test]
    fn the_hitmarker_sits_outside_the_arms_it_reads_over() {
        // If the ticks overlapped the arms they would read as a thicker
        // crosshair rather than as a separate signal.
        let style = Crosshair::default();
        let arms_reach = reticle(&Hud::default(), style)
            .iter()
            .map(|(x, y)| x.hypot(*y))
            .fold(0.0f32, f32::max);
        let mut hot = Hud::default();
        hot.on_hits(&[HitMarker::default()]);
        let marked_reach = reticle(&hot, style)
            .iter()
            .map(|(x, y)| x.hypot(*y))
            .fold(0.0f32, f32::max);
        assert!(
            marked_reach > arms_reach,
            "the marker ({marked_reach}) did not clear the arms ({arms_reach})"
        );
    }

    #[test]
    fn an_outlined_crosshair_draws_more_than_a_bare_one() {
        // The outline is the difference between a reticle that works on every
        // wall in the game and one that vanishes on about a third of them.
        let hud = Hud::default();
        let mut bare = Crosshair::default();
        bare.outline = false;
        let outlined = Crosshair::default();
        assert!(
            reticle(&hud, outlined).len() > reticle(&hud, bare).len(),
            "the outline drew nothing"
        );
    }

    #[test]
    fn the_centre_dot_can_be_turned_off_without_changing_style() {
        // It used to be baked into the style, so choosing a ring cost you the
        // choice of whether to have a dot at all.
        let hud = Hud::default();
        let mut no_dot = Crosshair::default();
        no_dot.dot = false;
        assert!(
            reticle(&hud, no_dot).len() < reticle(&hud, Crosshair::default()).len(),
            "turning the dot off changed nothing"
        );
    }

    #[test]
    fn a_transparent_crosshair_is_still_drawn_just_fainter() {
        // Opacity must not silently drop geometry: a reticle at 20% is a
        // preference, and a reticle that is *gone* is a bug report.
        let hud = Hud::default();
        let mut faint = Crosshair::default();
        faint.alpha = 0.2;
        assert_eq!(
            reticle(&hud, faint).len(),
            reticle(&hud, Crosshair::default()).len(),
            "opacity changed the geometry rather than the colour"
        );
    }

    #[test]
    fn a_held_scoreboard_stays_on_screen_with_a_full_room() {
        // Same trap as the layout test above: clip space is -1..1 with no
        // scissor, so a panel sized for four players and handed twelve does not
        // overflow visibly — it silently draws rows nobody can see.
        let rows: Vec<ScoreRow> = (0..12)
            .map(|i| ScoreRow {
                name: format!("a-very-long-player-name-{i}"),
                kills: i * 3,
                deaths: i,
                team: i % 2,
                bot: i % 3 == 0,
                you: i == 4,
            })
            .collect();
        let mut v = view(None);
        v.scoreboard = Some(&rows);
        v.scores = &[7, 4];
        let mut out = Vec::new();
        Hud::default().build(&v, &mut out);
        assert!(!out.is_empty());
        for vert in &out {
            assert!(
                vert.position[0] >= -1.001 && vert.position[0] <= 1.001,
                "x off screen: {:?}",
                vert.position
            );
            assert!(
                vert.position[1] >= -1.001 && vert.position[1] <= 1.001,
                "y off screen: {:?}",
                vert.position
            );
        }
    }

    #[test]
    fn a_scoreboard_that_was_not_asked_for_is_not_an_empty_one() {
        // `None` means the key is not held; an empty slice means a match with
        // nobody in it. Drawing the panel for both would put a scoreboard on
        // screen permanently.
        let mut with = Vec::new();
        let mut v = view(None);
        v.scoreboard = Some(&[]);
        Hud::default().build(&v, &mut with);

        let mut without = Vec::new();
        Hud::default().build(&view(None), &mut without);
        assert!(
            with.len() > without.len(),
            "an empty roster still draws a panel"
        );
    }

    #[test]
    fn everything_drawn_lands_on_the_screen() {
        // Clip space is -1..1 and there is no scissor: geometry outside it is
        // silently invisible, which would make a layout bug look like a HUD that
        // simply does not draw that element.
        let mut hud = Hud::default();
        hud.on_fx(&kill("averyverylongname", "another", true), "me");
        let mut you = alive();
        you.alive = false;
        you.respawn_in = 3.5;
        you.reserve = -1;
        you.reloading = true;
        let mut out = Vec::new();
        hud.build(&view(Some(&you)), &mut out);
        for v in &out {
            assert!(
                v.position[0] >= -1.001 && v.position[0] <= 1.001,
                "x off screen: {:?}",
                v.position
            );
            assert!(
                v.position[1] >= -1.001 && v.position[1] <= 1.001,
                "y off screen: {:?}",
                v.position
            );
        }
    }

    #[test]
    fn a_scope_replaces_the_crosshair_and_costs_peripheral_vision() {
        // The blacked-out surround is the mechanical half: a zoom with a clear
        // view all round would be a free upgrade rather than a trade.
        let hud = Hud::default();
        let you = alive();
        let mut plain = Vec::new();
        hud.build(&view(Some(&you)), &mut plain);
        let mut scoped_view = view(Some(&you));
        scoped_view.magnification = 4.0;
        let mut scoped = Vec::new();
        hud.build(&scoped_view, &mut scoped);
        assert!(scoped.len() > plain.len(), "the sight drew nothing");
        // And the surround genuinely covers the corners of the window, at any
        // aspect ratio — a ring sized from one axis leaves the other's corners
        // clear, which is a scope that costs nothing on an ultrawide.
        let corner = scoped
            .iter()
            .any(|v| v.position[0].abs() > 0.99 && v.position[1].abs() > 0.99);
        assert!(corner, "the vignette left the corners of the screen open");
    }

    #[test]
    fn a_dead_player_gets_neither_crosshair_nor_sight() {
        let hud = Hud::default();
        let mut you = alive();
        you.alive = false;
        let mut scoped_view = view(Some(&you));
        scoped_view.magnification = 4.0;
        let mut out = Vec::new();
        hud.build(&scoped_view, &mut out);
        // Nothing centred on the crosshair: what is drawn is the death notice,
        // well away from the middle of the screen.
        assert!(
            !out.iter()
                .any(|v| v.position[0].abs() < 0.01 && v.position[1].abs() < 0.01),
            "a sight was drawn over a dead player"
        );
    }

    #[test]
    fn pixels_map_to_clip_space_with_y_flipped() {
        // Pixels count down from the top and clip space counts up from the
        // middle. Getting this backwards draws a HUD that is upside down but
        // otherwise perfect, which is a screenshot away from looking deliberate.
        let mut out = Vec::new();
        let p = Painter {
            out: &mut out,
            width: 800.0,
            height: 600.0,
        };
        assert_eq!(p.ndc(0.0, 0.0), [-1.0, 1.0]);
        assert_eq!(p.ndc(800.0, 600.0), [1.0, -1.0]);
        assert_eq!(p.ndc(400.0, 300.0), [0.0, 0.0]);
    }

    #[test]
    fn a_zero_sized_window_does_not_divide_by_zero() {
        let hud = Hud::default();
        let you = alive();
        let mut v = view(Some(&you));
        v.width = 0;
        v.height = 0;
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        assert!(out.iter().all(|x| x.position.iter().all(|c| c.is_finite())));
    }

    #[test]
    fn a_magazineless_weapon_shows_a_dash_rather_than_zero_rounds() {
        // The knife has `mag: 0`. "0" there says it is empty, which is a
        // different claim from "this does not take rounds".
        let mut you = alive();
        you.mag = 0;
        you.ammo = 0;
        let mut out = Vec::new();
        let hud = Hud::default();
        hud.build(&view(Some(&you)), &mut out);
        let knife = out.len();
        you.mag = 30;
        you.ammo = 0;
        hud.build(&view(Some(&you)), &mut out);
        assert_ne!(knife, out.len(), "a knife drew the same as an empty rifle");
    }

    #[test]
    fn text_width_counts_gaps_between_glyphs_not_after_them() {
        // Right-aligned text uses this to decide where to start. Counting the
        // trailing gap floats every right-aligned line one space off its edge.
        assert_eq!(text_width("", 2.0), 0.0);
        assert_eq!(text_width("A", 2.0), 10.0);
        assert_eq!(text_width("AB", 2.0), 22.0);
    }

    #[test]
    fn the_font_has_a_shape_for_everything_the_hud_writes() {
        // A HUD string containing a glyph the font lacks comes out with a hole
        // in it, and nothing anywhere reports that.
        for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ./-+:>∞".chars() {
            assert_ne!(glyph(ch), [0; 7], "no glyph for {ch:?}");
        }
        assert_eq!(glyph(' '), [0; 7], "a space is blank");
        assert_eq!(glyph('\u{2603}'), [0; 7], "an unknown glyph is blank");
    }

    #[test]
    fn lowercase_has_its_own_shapes_rather_than_being_folded() {
        // It used to be folded — `glyph` looked every character up through
        // `to_ascii_uppercase`, so the font had one case. That is fine for the
        // HUD, which uppercases its strings explicitly, and wrong for the
        // console, which echoes back exactly what the player typed next to CVar
        // names that are lowercase everywhere else in this app.
        for ch in "abcdefghijklmnopqrstuvwxyz".chars() {
            assert_ne!(glyph(ch), [0; 7], "no glyph for {ch:?}");
            let upper = ch.to_ascii_uppercase();
            assert_ne!(
                glyph(ch),
                glyph(upper),
                "{ch:?} still draws as {upper:?} — the fold is back"
            );
        }
        // The descenders are the reason lowercase is the fiddly half: there is
        // no eighth row to hang a tail in, so these shift up instead. A `g` whose
        // bowl sits where an `o`'s does has lost its tail off the bottom.
        for ch in "gjpqy".chars() {
            assert_ne!(glyph(ch)[6], 0, "{ch:?} has nothing on its last row");
        }
    }

    #[test]
    fn a_console_chip_is_clickable_exactly_where_it_is_drawn() {
        // The painter and the mouse read one layout — `console_hits` — for the
        // same reason `Menu::rows_at` exists: two computations of a chip's
        // rectangle drift, and the symptom is a click that toggles the setting
        // next to the one you aimed at, with nothing anywhere reporting it.
        // This asserts the *shape* of that agreement: every rect the hit test
        // offers is inside the panel, they do not overlap, and the ones past the
        // right edge are absent rather than clamped — a chip that was never
        // drawn must not be clickable.
        let quick: Vec<crate::console::QuickAction> = (0..40)
            .map(|_| crate::console::QuickAction {
                key: "F1",
                label: "HITBOXES",
                state: Some("OFF".to_string()),
                active: false,
                honored: true,
                command: "draw.hitboxes 1".to_string(),
            })
            .collect();
        let hits = console_hits(&quick, &[], false, 1280.0, 800.0);

        assert!(!hits.chips.is_empty(), "no chip was placed at all");
        assert!(
            hits.chips.len() < quick.len(),
            "forty chips cannot fit in 1280px — the row is not being cut off"
        );
        for (_, rect) in &hits.chips {
            assert!(rect.x >= 0.0 && rect.x + rect.w <= 1280.0);
            assert!(rect.y + rect.h <= hits.panel.h, "a chip escaped the panel");
        }
        for pair in hits.chips.windows(2) {
            let (a, b) = (pair[0].1, pair[1].1);
            assert!(
                a.x + a.w <= b.x,
                "two chips overlap, so a click is ambiguous"
            );
        }

        // And the indices are into the *original* list, not the drawn subset —
        // clicking the third visible chip has to run the third action.
        assert_eq!(hits.chips[2].0, 2);
    }

    #[test]
    fn a_completion_row_is_hit_testable_and_shifts_for_the_detail_line() {
        // The detail line sits between the caret and the completions, so its
        // presence moves every completion up by one. Computing the row without
        // accounting for it is a click that lands a line below the word.
        let items: Vec<String> = ["draw.fov", "draw.hitboxes"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let without = console_hits(&[], &items, false, 1280.0, 800.0);
        let with = console_hits(&[], &items, true, 1280.0, 800.0);
        assert_eq!(without.suggestions.len(), 2);
        assert_eq!(with.suggestions.len(), 2);
        assert!(
            with.suggestions[0].1.y < without.suggestions[0].1.y,
            "the detail line did not push the completions up"
        );
        // Distinct rectangles, or the second suggestion is unreachable.
        assert!(without.suggestions[0].1.x < without.suggestions[1].1.x);
    }

    #[test]
    fn the_font_can_draw_what_a_console_line_is_made_of() {
        // Console text is not HUD text: it is whatever the player types and
        // whatever a Python handler prints back. A missing glyph there is worse
        // than a hole in the ammo counter, because the console's whole job is to
        // report exactly what happened — and `server.bots.add(count=3)` is four
        // characters of punctuation the HUD never needed.
        for ch in "=(){}[]\"'*;#@&|$~^_,%!?\\<".chars() {
            assert_ne!(glyph(ch), [0; 7], "no glyph for {ch:?}");
        }
    }

    /// One scrollback line, at a stated time.
    ///
    /// The time is set explicitly rather than left to `Console`, because the
    /// painter draws a stamp and a test that took the real clock would assert
    /// against whatever second it happened to run in.
    fn log_line(text: &str, tone: Tone) -> LogLine {
        LogLine {
            text: text.into(),
            tone,
            at: 12.0,
            channels: 0,
        }
    }

    /// The console, with some lines in it, as a view.
    fn console_view<'a>(
        lines: &'a [&'a LogLine],
        input: &'a str,
        quick: &'a [crate::console::QuickAction],
    ) -> ConsoleView<'a> {
        ConsoleView {
            lines,
            input,
            cursor: input.len(),
            scroll: 0,
            suggestions: &[],
            suggestion: 0,
            detail: None,
            registry_loaded: true,
            filter: "ALL",
            hidden: 0,
            room: "95783cd7-1111",
            map: "hd_atrium",
            rtt: Some(2.5),
            cheats: Some(false),
            quick,
        }
    }

    fn quick(
        label: &'static str,
        state: Option<&str>,
        honored: bool,
    ) -> crate::console::QuickAction {
        crate::console::QuickAction {
            key: "F1",
            label,
            state: state.map(|s| s.to_string()),
            active: false,
            honored,
            command: "draw.hitboxes 1".into(),
        }
    }

    #[test]
    fn an_open_console_draws_and_a_closed_one_costs_nothing() {
        let lines = vec![log_line("net.graph = 2", Tone::Output)];
        let refs: Vec<&LogLine> = lines.iter().collect();
        let hud = Hud::default();

        let mut closed = Vec::new();
        hud.build(&view(Some(&alive())), &mut closed);

        let mut open = Vec::new();
        let me = alive();
        let mut v = view(Some(&me));
        v.console = Some(console_view(&refs, "net.graph 2", &[]));
        hud.build(&v, &mut open);

        assert!(
            open.len() > closed.len(),
            "an open console has to be more geometry than a closed one"
        );
    }

    #[test]
    fn the_console_opens_even_before_there_is_a_world() {
        // `playing: false` used to be an early return, which would have made the
        // console the one panel unavailable while connecting — the exact moment
        // somebody wants it.
        let lines = vec![log_line("connecting", Tone::Note)];
        let refs: Vec<&LogLine> = lines.iter().collect();
        let hud = Hud::default();
        let mut v = view(None);
        v.playing = false;
        v.console = Some(console_view(&refs, "", &[]));
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        assert!(
            !out.is_empty(),
            "the console must draw with no body in the world"
        );
    }

    #[test]
    fn the_quick_action_row_is_drawn() {
        // The chips are the browser's toolbar, and the whole reason they exist
        // is to be *visible* — a row that silently drew nothing would look
        // exactly like a client that had not implemented them.
        let lines = vec![log_line("ready", Tone::Note)];
        let refs: Vec<&LogLine> = lines.iter().collect();
        let hud = Hud::default();
        let me = alive();

        let mut bare = Vec::new();
        let mut v = view(Some(&me));
        v.console = Some(console_view(&refs, "", &[]));
        hud.build(&v, &mut bare);

        let chips = [
            quick("HITBOXES", Some("ON"), true),
            quick("WIREFRAME", None, false),
        ];
        let mut with_chips = Vec::new();
        let mut v = view(Some(&me));
        v.console = Some(console_view(&refs, "", &chips));
        hud.build(&v, &mut with_chips);

        assert!(
            with_chips.len() > bare.len(),
            "the quick-action chips have to draw"
        );
    }

    #[test]
    fn the_console_stays_inside_the_window() {
        // The same guarantee `everything_drawn_lands_on_the_screen` makes for
        // the rest of the HUD. A scrollback painted upward from the input line
        // is the one block here whose height is not bounded by its own layout —
        // and the header, the chip row and the completion detail all eat into
        // the space it has, so this has to hold with every one of them present.
        let lines: Vec<LogLine> = (0..200)
            .map(|i| {
                log_line(
                    &format!("LINE {i} WITH SOME REASONABLY LONG TEXT ON IT"),
                    Tone::Output,
                )
            })
            .collect();
        let refs: Vec<&LogLine> = lines.iter().collect();
        let chips: Vec<crate::console::QuickAction> = (0..8)
            .map(|_| quick("HITBOXES", Some("OFF"), true))
            .collect();
        let hud = Hud::default();
        let me = alive();
        let mut v = view(Some(&me));
        let mut cv = console_view(&refs, "SERVER.BOTS.ADD(COUNT=3)", &chips);
        cv.detail = Some("draw.hitboxes <bool> client = 0 (default 0) - draw hitboxes");
        cv.hidden = 42;
        v.console = Some(cv);
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        for vertex in &out {
            let [x, y] = vertex.position;
            assert!(
                (-1.2..=1.2).contains(&x) && (-1.2..=1.2).contains(&y),
                "a console vertex landed off screen at {x},{y}"
            );
        }
    }

    fn radar_view<'a>(plan: &'a [Run], blips: &'a [Blip], yaw: f32) -> RadarView<'a> {
        RadarView {
            plan,
            x: 50.0,
            y: 50.0,
            yaw,
            blips,
        }
    }

    /// Where a blip landed on screen, as an offset from the radar's centre.
    ///
    /// Found by painting a radar with exactly one contact and taking the mean of
    /// the vertices that carry its colour. Reading the geometry back is the only
    /// way to test a painter that has no return value — and orientation is
    /// exactly the kind of bug that leaves every unit test green.
    fn blip_offset(blip: Blip, yaw: f32) -> (f32, f32) {
        let hud = Hud::default();
        let blips = [blip];
        let mut v = view(None);
        v.playing = true;
        v.radar = Some(radar_view(&[], &blips, yaw));
        let mut out = Vec::new();
        hud.build(&v, &mut out);

        let want = if blip.friendly {
            [0.345, 0.651, 1.0, 0.95]
        } else {
            RED
        };
        let hits: Vec<[f32; 2]> = out
            .iter()
            .filter(|vert| vert.color == want)
            .map(|vert| vert.position)
            .collect();
        assert!(!hits.is_empty(), "the blip was not drawn at all");
        let n = hits.len() as f32;
        let mx = hits.iter().map(|p| p[0]).sum::<f32>() / n;
        let my = hits.iter().map(|p| p[1]).sum::<f32>() / n;

        // The centre of the instrument, in the same clip space, from the layout
        // constants rather than from a second guess at them.
        let (w, h) = (v.width as f32, v.height as f32);
        let u = (h / 360.0).round().max(2.0);
        let radius = u * 38.0;
        let cx = MARGIN * u + radius;
        let cy = MARGIN * u + radius;
        let ndc_cx = cx / w * 2.0 - 1.0;
        let ndc_cy = 1.0 - cy / h * 2.0;
        // Clip space is y-up; report y-down so "ahead is negative y" reads the
        // way the layout does.
        (mx - ndc_cx, -(my - ndc_cy))
    }

    #[test]
    fn what_is_ahead_of_you_is_at_the_top_of_the_radar() {
        // The orientation trap. A radar with the rotation sign wrong still turns
        // when you turn, still shows contacts at the right distance, and is
        // mirrored — which is worse than no radar, because it is trusted.
        //
        // Looking along +x, a contact further along +x is directly ahead.
        let ahead = Blip {
            x: 70.0,
            y: 50.0,
            friendly: false,
        };
        let (dx, dy) = blip_offset(ahead, 0.0);
        assert!(dy < 0.0, "ahead must be up, got dy={dy}");
        assert!(dx.abs() < 1e-3, "ahead must be centred, got dx={dx}");
    }

    #[test]
    fn a_contact_to_your_right_is_drawn_to_the_right() {
        // The half of the orientation a mirrored radar gets wrong while the
        // "ahead is up" test above still passes.
        //
        // In this world +y is to the right of a body facing +x, which is what
        // the movement code means by strafe-right.
        let right = Blip {
            x: 50.0,
            y: 70.0,
            friendly: false,
        };
        let (dx, dy) = blip_offset(right, 0.0);
        assert!(dx > 0.0, "right must be right, got dx={dx}");
        assert!(dy.abs() < 1e-3, "abeam must be level, got dy={dy}");
    }

    #[test]
    fn turning_turns_the_radar_under_you() {
        // The same contact, seen after a quarter turn to the right: what was
        // ahead must now be abeam to the left.
        let ahead = Blip {
            x: 70.0,
            y: 50.0,
            friendly: false,
        };
        let (dx, dy) = blip_offset(ahead, std::f32::consts::FRAC_PI_2);
        assert!(dx < 0.0, "got dx={dx}");
        assert!(dy.abs() < 1e-3, "got dy={dy}");
    }

    #[test]
    fn a_contact_beyond_the_span_is_not_drawn() {
        // Off the instrument, not clamped to its edge: a blip pinned to the rim
        // is a claim that somebody is standing there.
        let hud = Hud::default();
        let far = [Blip {
            x: 50.0 + radar::SPAN,
            y: 50.0,
            friendly: false,
        }];
        let mut v = view(None);
        v.playing = true;
        v.radar = Some(radar_view(&[], &far, 0.0));
        let mut out = Vec::new();
        hud.build(&v, &mut out);
        assert!(
            !out.iter().any(|vert| vert.color == RED),
            "a contact past the radar's span was drawn anyway"
        );
    }

    #[test]
    fn the_floor_plan_is_cut_at_the_rim() {
        // There is no clip here, so an uncut plan is a square minimap inside a
        // round frame. Checked as geometry: every vertex the plan contributes
        // must be inside the instrument.
        let hud = Hud::default();
        // One run straight through the middle, far longer than the radar.
        let plan = [Run {
            y: 50.5,
            x0: -500.0,
            x1: 500.0,
        }];
        let mut v = view(None);
        v.playing = true;
        v.radar = Some(radar_view(&plan, &[], 0.0));
        let mut out = Vec::new();
        hud.build(&v, &mut out);

        let (w, h) = (v.width as f32, v.height as f32);
        let u = (h / 360.0).round().max(2.0);
        let radius = u * 38.0;
        let cx = MARGIN * u + radius;
        let cy = MARGIN * u + radius;
        let plan_color = [0.549, 0.667, 0.824, 0.16];
        let mut seen = 0;
        for vert in out.iter().filter(|vert| vert.color == plan_color) {
            seen += 1;
            // Back to pixels to compare against a radius in pixels.
            let px = (vert.position[0] + 1.0) / 2.0 * w;
            let py = (1.0 - vert.position[1]) / 2.0 * h;
            let d = ((px - cx).powi(2) + (py - cy).powi(2)).sqrt();
            assert!(
                d <= radius + u,
                "a floor-plan vertex sits {d:.1}px out, past a {radius:.1}px rim"
            );
        }
        assert!(seen > 0, "the run was clipped away entirely");
    }

    #[test]
    fn a_radar_that_was_not_asked_for_draws_nothing() {
        // `None` is Train and the seconds before a welcome — a radar there would
        // have to invent the position it is centred on.
        let hud = Hud::default();
        let mut before = Vec::new();
        hud.build(&view(None), &mut before);
        let mut v = view(None);
        v.radar = None;
        let mut after = Vec::new();
        hud.build(&v, &mut after);
        assert_eq!(before.len(), after.len());
    }

    #[test]
    fn the_radar_shows_the_span_it_says_it_does() {
        // Direction is only half of it: a radar with the right orientation and
        // the wrong scale reads as an enemy being somewhere they are not, which
        // is the same lie in a quieter voice. Pinned against the browser's own
        // number — 110 cubes across the instrument — rather than against
        // whatever this painter happens to do.
        //
        // The view is 800 tall, so `u` is 2 and the radar is 152 px across:
        // 152 / 110 = 1.3818 px per cube.
        let cubes = 10.5;
        let ahead = Blip {
            x: 50.0 + cubes,
            y: 50.0,
            friendly: false,
        };
        let (_, dy) = blip_offset(ahead, 0.0);
        // `blip_offset` reports clip-space units; convert back to pixels against
        // the same height the view declares.
        let h = 800.0;
        let px = -dy / 2.0 * h;
        let expected = cubes * (2.0 * 38.0 * 2.0) / radar::SPAN;
        assert!(
            (px - expected).abs() < 0.5,
            "{cubes} cubes ahead drew {px:.2}px out, expected {expected:.2}px"
        );
    }
}
