//! The `hassault` `/ws` wire, as Rust types.
//!
//! Every message on the node's shared socket is
//! `{"channel": …, "event": …, "data": {…}}`, and this client speaks exactly one
//! channel of it. Riding the shared socket rather than opening a second one is
//! the backend's decision and a good one — it means the native client shows up in
//! the observability panel like everything else, and there is no second
//! connection to reconnect.
//!
//! **Identity is not on this wire.** `channel.py` takes the player's name from
//! the backend's signed-in account and explicitly ignores `data["name"]`, so
//! there is nothing here for a client to claim and nothing to get wrong. A `join`
//! from a node with no username claimed comes back as an `error` with code
//! `not_signed_in`, which is the correct and only answer.
//!
//! Field names are `camelCase` on the wire because the browser is the other
//! speaker; `serde(rename)` keeps Rust idiomatic on this side without inventing a
//! second spelling anywhere.

use serde::{Deserialize, Serialize};

use crate::divergence::{self, Extra};

// Fields below that this stage does not read are the wire's shape, not dead
// weight: a protocol type trimmed to today's consumers stops documenting the
// protocol, and the next stage adds the reader, not the field.
pub const CHANNEL: &str = "hassault";

/// Anything arriving on the socket. `data` stays raw until the event is known —
/// the channel multiplexes several shapes and there is no discriminant inside
/// `data` itself.
#[derive(Debug, Deserialize)]
pub struct Envelope {
    #[serde(default)]
    pub channel: String,
    #[serde(default)]
    pub event: String,
    #[serde(default)]
    pub data: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct Outbound<'a, T> {
    pub channel: &'a str,
    pub event: &'a str,
    pub data: T,
}

impl<'a, T> Outbound<'a, T> {
    pub fn new(event: &'a str, data: T) -> Outbound<'a, T> {
        Outbound {
            channel: CHANNEL,
            event,
            data,
        }
    }
}

#[derive(Debug, Serialize, Default)]
pub struct JoinRequest {
    pub map: String,
    /// Empty asks the server to place us in (or open) a room on this map.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub room: String,
    /// A friend's node id for a match running on their machine; empty for local.
    #[serde(skip_serializing_if = "String::is_empty")]
    pub host: String,
    /// Sent for the wire's sake only — the backend ignores it and uses the
    /// account's username. Kept because the browser sends it and a wire with two
    /// dialects is a wire nobody can debug.
    pub name: String,
    /// Ask for a **rated** match: the room is opened on the game server and this
    /// node proxies for us. Omitted when false, like `room` and `host`, because
    /// the server reads a present flag as the request itself.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub ranked: bool,
}

/// One input frame.
///
/// Firing rides here as a **flag on a movement command** rather than as its own
/// message, which is the server's design and worth preserving exactly: it means a
/// shot carries the sequence number and view angles of the precise frame it was
/// fired on. A separate fire message would arrive with whatever angles the next
/// movement command happened to have.
#[derive(Debug, Serialize, Clone, Default)]
pub struct Command {
    pub seq: u64,
    pub forward: f32,
    pub strafe: f32,
    pub jump: bool,
    pub yaw: f32,
    pub pitch: f32,
    /// Seconds. Spent from a replenishing server-side budget — a client claiming
    /// time faster than it passes is throttled, not trusted.
    pub dt: f32,
    #[serde(default)]
    pub crouch: bool,
    #[serde(default)]
    pub fire: bool,
    #[serde(default)]
    pub reload: bool,
    /// Weapon slot to switch to, or `-1` for no change.
    pub weapon: i32,
    /// Throw the readied grenade this frame.
    ///
    /// A flag on a movement command for exactly the reason `fire` is one: the
    /// throw then carries the yaw, pitch and sequence number of the frame it
    /// happened on. A message of its own would arrive with none of them, and the
    /// grenade would leave in a direction nobody was looking.
    ///
    /// **Edge-triggered by `utility::GrenadeController`, never read as held.** A
    /// key read as held sets this on every frame it is down — sixty throws a
    /// second, of which the server's cooldown accepts one and silently discards
    /// the rest, leaving a player with an empty pouch and one grenade.
    pub r#throw: bool,
    /// Which grenade slot the throw uses. `-1` is no selection, and is what
    /// `Command::new` starts at — a zero would name the HE and make every
    /// command a request to throw one.
    pub nade: i32,
    /// Underhand: a short throw, for putting a smoke at your own feet.
    pub lob: bool,
    /// Zoom step: 0 unscoped, otherwise 1-based into the weapon's `zoomLevels`.
    ///
    /// **Client-owned, and clamped by the server rather than by the wire parser**
    /// — which cannot know which weapon this command lands on, and so cannot know
    /// whether step 2 exists. See `weapons.clamp_zoom`. The server reads it only
    /// to pick the shot's cone.
    pub scoped: i32,
    /// Server-clock ms this client was *rendering* when it fired, for lag
    /// compensation. `None` until B3 has a snapshot buffer to read a render time
    /// from — sending a made-up one would ask the server to rewind to a moment
    /// that never existed.
    #[serde(rename = "viewT", skip_serializing_if = "Option::is_none")]
    pub view_t: Option<f64>,
}

impl Command {
    pub fn new(seq: u64) -> Command {
        Command {
            seq,
            weapon: -1,
            nade: -1,
            ..Default::default()
        }
    }
}

#[derive(Debug, Serialize)]
pub struct InputBatch {
    pub commands: Vec<Command>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rtt: Option<f32>,
}

/// One body in a snapshot. Everything here is public information by design —
/// health included, because a wounded enemy is what makes a firefight a decision.
#[derive(Debug, Deserialize, Clone, Default)]
#[allow(dead_code)]
pub struct PlayerRow {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub team: i32,
    #[serde(default)]
    pub x: f32,
    #[serde(default)]
    pub y: f32,
    #[serde(default)]
    pub z: f32,
    #[serde(default)]
    pub yaw: f32,
    #[serde(default)]
    pub pitch: f32,
    #[serde(default)]
    pub hp: f32,
    #[serde(default)]
    pub alive: bool,
    #[serde(default)]
    pub bot: bool,
    #[serde(default)]
    pub crouch: f32,
    /// Whether the server has this body resting on the floor.
    ///
    /// Read by reconciliation, which used to pass a hardcoded `false`. That is
    /// not a neutral default: the first replayed step then converges on the wish
    /// direction at `AIR_RESPONSE` instead of `GROUND_RESPONSE` — five times
    /// slower — applies a frame of gravity, and refuses a jump. Every correction
    /// replayed a body that was falling when the server says it was standing.
    #[serde(default)]
    pub ground: bool,
    #[serde(default)]
    pub kills: i32,
    #[serde(default)]
    pub deaths: i32,
    #[serde(default)]
    pub weapon: i32,
    /// Wire keys this struct does not name. See `divergence::Extra` — the point
    /// is that adding a real field below makes the key disappear from here on
    /// its own, so the check cannot rot the way a hand-kept list would.
    #[serde(flatten)]
    pub extra: Extra,
}

/// The private half of a player's state — the part only they get to see.
///
/// Ammo lives here rather than in the shared rows for the same reason noise
/// carries a bearing and not a position: handing every client the whole truth is
/// a wall hack, whatever the UI chooses to draw.
///
/// **This is the HUD's entire data source**, which is why it grew: health, the
/// magazine, the reload clock and the respawn clock are all per-recipient, and
/// none of them can be derived from the shared rows.
#[derive(Debug, Deserialize, Clone, Default)]
#[allow(dead_code)]
pub struct SelfState {
    // **No `x`/`y`/`z` here, deliberately.** `private_view` does not send a
    // position — the authoritative one is in the shared row, which is where
    // reconciliation reads it and where the browser client reads it.
    //
    // They used to be declared anyway, with `#[serde(default)]`, which is how a
    // field that is never on the wire passes for one that is: every snapshot
    // deserialized them to 0.0 in silence. The respawn path then reset the body
    // to the world origin, which is inside the solid border every map has, where
    // `step` finds itself enclosed and holds still — so a respawn put the player
    // in rock, unable to move, until the next snapshot reconciled them out of it.
    #[serde(default)]
    pub hp: f32,
    /// Armour, 0..100. Absorbs half of every hit and spends itself doing it, so
    /// it is health that does not appear on the health bar.
    ///
    /// The server has sent this since armour became a real mechanic and the
    /// browser has read it since; **this client silently dropped it**, because
    /// an undeclared field in serde is not an error. The symptom was not a
    /// missing number — it was a body taking half damage for a reason nothing on
    /// screen explained.
    #[serde(default)]
    pub armour: f32,
    #[serde(default)]
    pub alive: bool,
    #[serde(default)]
    pub weapon: i32,
    /// Rounds in the magazine. `mag` of 0 means the weapon has no magazine at
    /// all (the knife), which the HUD draws as a dash rather than as "0 left".
    #[serde(default)]
    pub ammo: i32,
    /// Rounds in reserve; negative is the sidearm's bottomless supply.
    #[serde(default)]
    pub reserve: i32,
    #[serde(default)]
    pub mag: i32,
    #[serde(default)]
    pub reloading: bool,
    #[serde(rename = "reloadIn", default)]
    pub reload_in: f32,
    #[serde(rename = "respawnIn", default)]
    pub respawn_in: f32,
    #[serde(default)]
    pub protected: bool,
    #[serde(default)]
    pub kills: i32,
    #[serde(default)]
    pub deaths: i32,
    /// Damage the last landing cost, so the HUD can say the map did it.
    #[serde(default)]
    pub fell: f32,
    /// What we can hear. Resolved per recipient, so this list is *already* only
    /// the noises audible from where we are standing — the client's job is to
    /// pan and play them, never to decide what was loud enough.
    #[serde(default)]
    pub noise: Vec<NoiseEvent>,
    /// Hits **we** landed since the last snapshot. Drained server-side as this
    /// envelope is built, so every one arrives exactly once — which is what makes
    /// a hitmarker honest. A marker drawn from the local trigger instead would
    /// light up on every shot, including the ones that hit a wall.
    #[serde(default)]
    pub hits: Vec<HitMarker>,
    /// Damage **taken** since the last snapshot, as bearings. Drained
    /// server-side like `hits`, so each arrives exactly once.
    #[serde(default)]
    pub hurt: Vec<HurtMarker>,
    /// The momentum the prediction rebases on. See `MoveState`.
    ///
    /// `move` is a Rust keyword, hence the rename. `Option` because a server
    /// older than the field sends none, and replaying on the local velocity is
    /// the best guess available then — it is only *wrong* to prefer it when the
    /// authoritative number is right there.
    /// What we are carrying, keyed by grenade id. Private, like `ammo`.
    #[serde(default)]
    pub nades: std::collections::HashMap<String, i32>,
    /// How blind a flashbang has left **us**, 0..1.
    ///
    /// Resolved per player on the server, because it depends on where we were
    /// looking and whether a wall was in the way. A client that computed its own
    /// would make not being blinded a setting.
    #[serde(default)]
    pub flash: f32,
    /// Enemy ids our team can currently see, for the radar.
    ///
    /// Teammates are deliberately *not* in this list — they are always shown, so
    /// saying so every tick would be a per-player id list that never changes.
    /// Which enemies appear is `MatchRoom.spotted_by`, resolved on the server
    /// because only the server holds the two things the answer depends on: the
    /// level's geometry and the smoke standing in it. A client that decided for
    /// itself would be a wall hack with extra steps.
    #[serde(default)]
    pub spotted: Vec<String>,
    #[serde(rename = "move", default)]
    pub movement: Option<MoveState>,
    /// Wire keys this struct does not name. See `divergence::Extra` — the point
    /// is that adding a real field below makes the key disappear from here on
    /// its own, so the check cannot rot the way a hand-kept list would.
    #[serde(flatten)]
    pub extra: Extra,
}

/// The private half of a snapshot's momentum: what a client cannot derive from a
/// position and must not guess.
///
/// Movement here is velocity integrated against AC's friction constants, so the
/// velocity **is** the state — a position alone does not describe a body that is
/// still sliding. Replaying unacknowledged commands on top of the client's own
/// velocity runs the replay on the very number the correction exists to fix, and
/// the error compounds instead of settling: the prediction runs away from the
/// server, the next snapshot drags it back, and that is the elastic banding.
///
/// Private rather than in the shared rows because it is nobody else's business,
/// and would be sixteen more numbers per packet.
#[derive(Debug, Deserialize, Clone, Default)]
pub struct MoveState {
    /// `[x, y, z]` cubes per second.
    #[serde(default)]
    pub vel: [f32; 3],
    /// Seconds airborne, which is what the gravity ramp reads.
    #[serde(default)]
    pub air: f32,
    /// Crouch animation, 0 standing to 1 fully crouched.
    #[serde(default)]
    pub crouch: f32,
    #[serde(rename = "crouchedInAir", default)]
    pub crouched_in_air: bool,
    /// **A duration, not a timestamp.** The client's simulated clock and the
    /// server's are unrelated — only "how long ago" transfers — so this is
    /// converted against our own `t` at the moment it is applied.
    #[serde(rename = "sinceLanded", default)]
    pub since_landed: f32,
}

/// One sound, as ears give it: a bearing and a loudness, never an offset.
///
/// The wire deliberately carries no position. Broadcasting where a footstep
/// happened and letting each client decide whether it was audible would put an
/// enemy's coordinates in the packet — a wall hack made of sound, whatever the
/// UI chose to draw.
#[derive(Debug, Deserialize, Clone, Default)]
#[allow(dead_code)]
pub struct NoiseEvent {
    /// `step`, `land`, `jump`, `shot`, `reload`, `hurt`, `die`.
    #[serde(default)]
    pub kind: String,
    /// 0..1, after distance falloff and wall muffling.
    #[serde(default)]
    pub volume: f32,
    /// World bearing to the source, in radians.
    #[serde(default)]
    pub bearing: f32,
    /// -1 below, 0 level, 1 above.
    #[serde(default)]
    pub up: i32,
    /// Which weapon made it — shots only, and absent on every other kind.
    #[serde(default)]
    pub weapon: String,
}

/// One landed hit, from the shooter's side.
#[derive(Debug, Deserialize, Clone, Default)]
#[allow(dead_code)]
pub struct HitMarker {
    #[serde(default)]
    pub victim: String,
    #[serde(default)]
    pub damage: f32,
    #[serde(default)]
    pub head: bool,
    #[serde(default)]
    pub killed: bool,
}

/// One hit **taken**, from the victim's side: which way it came from.
///
/// A world bearing in radians and nothing else — the same shape `NoiseEvent`
/// carries, for the same reason. The server resolves it because only the server
/// holds both bodies, and it sends an angle rather than a position so the
/// indicator cannot be turned into a tracker for whoever is shooting at you.
#[derive(Debug, Deserialize, Clone, Default)]
pub struct HurtMarker {
    /// World bearing to the attacker, radians. Subtract the view yaw to draw it.
    #[serde(default)]
    pub bearing: f32,
    /// What the hit cost, health and armour together — the arrow's weight.
    #[serde(default)]
    pub amount: f32,
}

/// One thing that happened this tick, broadcast to everyone.
///
/// Internally tagged on `kind`, with an `Other` catch-all: the server adds effect
/// kinds, and a client that refuses to parse a snapshot because it grew one is a
/// client that stops rendering when the server gains a feature.
#[derive(Debug, Deserialize, Clone)]
#[serde(tag = "kind")]
#[allow(dead_code)]
pub enum Fx {
    /// Somebody fired. The **only** honest cue this client has for its own
    /// muzzle flash: the native client has no local trigger controller, so a
    /// flash driven by the fire key would light up on shots the server refused
    /// for rate limiting, an empty magazine, or being dead.
    #[serde(rename = "shot")]
    Shot {
        #[serde(default)]
        id: String,
        #[serde(default)]
        weapon: i32,
        #[serde(default)]
        hit: bool,
        /// The muzzle, in cubes.
        ///
        /// **On the wire since shots existed, and declared here only now.** The
        /// server resolves every ray and sends where it went; the native client
        /// parsed the effect, used the `id` to trigger a muzzle flash, and threw
        /// the geometry away. `#[serde(flatten)]` cannot reach inside an enum
        /// variant, so this one had to be found by reading `match.py` rather
        /// than by the divergence report — which is worth knowing about the
        /// limits of that report.
        #[serde(default)]
        origin: [f32; 3],
        /// One endpoint per pellet. A rifle sends one, a shotgun sends its
        /// whole pattern — which is why this is a list and not a point.
        #[serde(default)]
        ends: Vec<[f32; 3]>,
    },
    /// A grenade going off. A kind of its own rather than a flag on anything:
    /// it has a place and a radius, and until now this client had no variant
    /// for it at all, so every detonation became `Fx::Other` and vanished.
    #[serde(rename = "detonate")]
    Detonate {
        #[serde(default)]
        id: String,
        /// `he` | `flash` | `smoke` | `fire`.
        #[serde(default)]
        nade: String,
        #[serde(default)]
        at: [f32; 3],
        #[serde(default)]
        radius: f32,
    },
    #[serde(rename = "kill")]
    Kill {
        #[serde(default)]
        victim: String,
        #[serde(rename = "victimName", default)]
        victim_name: String,
        /// Empty when the map did it — a fall, which has no killer.
        #[serde(default)]
        killer: String,
        #[serde(rename = "killerName", default)]
        killer_name: String,
        #[serde(default)]
        weapon: String,
        #[serde(default)]
        head: bool,
    },
    #[serde(rename = "spawn")]
    Spawn {
        #[serde(default)]
        id: String,
    },
    /// An item left the map.
    ///
    /// Public, unlike what it gave: the item visibly disappears off a floor
    /// everybody can see. Who took it is deliberately not on the wire — the
    /// sound and the hole are the information, and a name would make an item a
    /// tracker.
    #[serde(rename = "pickup")]
    Pickup {
        #[serde(default)]
        item: i32,
        /// The item kind, so the effect can be coloured without a lookup.
        #[serde(default)]
        what: String,
    },
    #[serde(other)]
    Other,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
pub struct Snapshot {
    #[serde(default)]
    pub room: String,
    #[serde(default)]
    pub tick: u64,
    /// Server clock in ms.
    #[serde(default)]
    pub t: f64,
    /// The last command sequence the server has applied for us. **Prediction has
    /// nothing to replay without it** — B3's reconciliation is built on this
    /// field, which is why it is per-recipient rather than in the shared rows.
    #[serde(default)]
    pub ack: u64,
    #[serde(default)]
    pub players: Vec<PlayerRow>,
    #[serde(default)]
    pub you: SelfState,
    #[serde(default)]
    pub scores: Vec<i32>,
    /// This tick's effects — shots, kills, spawns. The kill feed is built from
    /// these and nothing else.
    #[serde(default, deserialize_with = "fx_noting_unknown_kinds")]
    pub fx: Vec<Fx>,
    /// Grenades in the air. **Public**, unlike the noise envelope: a grenade is
    /// a thing on everybody's screen, and hiding it would make the one cue that
    /// lets you leave a room a matter of who was looking.
    #[serde(default)]
    pub nades: Vec<NadeRow>,
    /// Smoke and fire standing in a place.
    #[serde(default)]
    pub zones: Vec<ZoneRow>,
    /// Ids of items currently **taken**, and the complement of the usual state on
    /// purpose: a map with sixty items normally has a handful missing, so this is
    /// a few numbers a tick rather than sixty.
    ///
    /// The placements themselves are not here — they are a property of the map
    /// and arrive with `MapInfo`. An **absent** field means "this server has no
    /// items", which is not the same as "every item is present": read the second
    /// way it would pop every taken item back into existence once a tick, so the
    /// app only applies this when the server actually sent it.
    #[serde(rename = "itemsOut", default)]
    pub items_out: Option<Vec<i32>>,
    /// Wire keys this struct does not name. See `divergence::Extra` — the point
    /// is that adding a real field below makes the key disappear from here on
    /// its own, so the check cannot rot the way a hand-kept list would.
    #[serde(flatten)]
    pub extra: Extra,
}

/// Read `fx`, naming any kind this build has no variant for.
///
/// `#[serde(other)]` on `Fx` is the right behaviour and the wrong diagnostic: it
/// keeps a snapshot parsing when the server grows an effect, which is what we
/// want, and it does so by **discarding the tag**, which means the one piece of
/// information worth having — *which* effect — is destroyed at exactly the
/// moment it is learned. Reading the array as JSON first costs one allocation a
/// tick and buys the name.
fn fx_noting_unknown_kinds<'de, D>(d: D) -> Result<Vec<Fx>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw: Vec<serde_json::Value> = Vec::deserialize(d)?;
    let mut out = Vec::with_capacity(raw.len());
    for value in raw {
        let kind = value
            .get("kind")
            .and_then(|k| k.as_str())
            .unwrap_or("")
            .to_string();
        match serde_json::from_value::<Fx>(value) {
            Ok(fx) => {
                if matches!(fx, Fx::Other) {
                    divergence::note_fx_kind(&kind);
                }
                out.push(fx);
            }
            // A malformed entry is not a reason to drop the whole tick: the
            // other effects in it are fine, and a snapshot that failed to parse
            // takes the positions down with it.
            Err(e) => divergence::note_fx_kind(&format!("{kind} (unreadable: {e})")),
        }
    }
    Ok(out)
}

/// A grenade in the air.
///
/// Every field here is the server's. Nothing in this client simulates the arc,
/// predicts the bounce or decides the fuse — the browser makes the same promise
/// in `nades.ts`, and for the same reason: a client-side arc is a second
/// implementation of the bounce whose only job is to occasionally disagree with
/// the first.
#[derive(Debug, Deserialize, Clone, Default)]
#[allow(dead_code)]
pub struct NadeRow {
    #[serde(default)]
    pub id: String,
    /// `he` | `flash` | `smoke` | `fire`.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub owner: String,
    #[serde(default)]
    pub team: i32,
    #[serde(default)]
    pub x: f32,
    #[serde(default)]
    pub y: f32,
    #[serde(default)]
    pub z: f32,
    /// Seconds of fuse left, for the tick that gets louder as it runs out.
    #[serde(default)]
    pub fuse: f32,
    /// Wire keys this struct does not name. See `divergence::Extra` — the point
    /// is that adding a real field below makes the key disappear from here on
    /// its own, so the check cannot rot the way a hand-kept list would.
    #[serde(flatten)]
    pub extra: Extra,
}

/// A smoke cloud or a patch of fire: an effect that persists in a place.
#[derive(Debug, Deserialize, Clone, Default)]
#[allow(dead_code)]
pub struct ZoneRow {
    #[serde(default)]
    pub id: String,
    /// `smoke` | `fire`.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub x: f32,
    #[serde(default)]
    pub y: f32,
    #[serde(default)]
    pub z: f32,
    #[serde(default)]
    pub r: f32,
    /// Seconds left, so a cloud can thin as it dies rather than vanishing.
    #[serde(default)]
    pub left: f32,
    #[serde(default)]
    pub duration: f32,
    /// Wire keys this struct does not name. See `divergence::Extra` — the point
    /// is that adding a real field below makes the key disappear from here on
    /// its own, so the check cannot rot the way a hand-kept list would.
    #[serde(flatten)]
    pub extra: Extra,
}

/// One console line on its way to the node.
///
/// The console rides the **same socket as the match**, not a second HTTP call,
/// and that is not a style preference: `channel.py` resolves the room and the
/// player from the connection itself (`match_server.player_for(conn)`), so a
/// command sent over this socket lands in the match this client is actually in.
/// The REST route the browser pane uses has to be told, and can be told wrong.
#[derive(Debug, Serialize)]
pub struct ConsoleExec<'a> {
    pub command: &'a str,
    /// Correlates the answer with the line that asked, because two commands can
    /// be in flight and the console prints them in the order they were typed.
    #[serde(rename = "reqId")]
    pub req_id: u64,
    /// Anything the client knows that the server does not. Empty today; declared
    /// because the backend reads `context` and a client that omitted the key
    /// entirely would have to grow it back the first time that mattered.
    pub context: serde_json::Value,
}

/// The node's answer to one console line.
///
/// **Two spellings, one meaning.** The websocket handler in `channel.py` writes
/// `affectedCvars` while the REST route's Pydantic model writes
/// `affected_cvars`; the browser pane only ever sees the second, so nothing has
/// forced them to agree. Accepting both here is not tolerance for sloppiness —
/// it is the cheapest way to make sure a native console never silently drops a
/// CVar update because it came down the other pipe.
#[derive(Debug, Deserialize, Default, Clone)]
pub struct ConsoleResponse {
    #[serde(rename = "reqId", default)]
    pub req_id: Option<u64>,
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub command: String,
    #[serde(default)]
    pub output: Vec<String>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(rename = "affectedCvars", alias = "affected_cvars", default)]
    pub affected_cvars: serde_json::Map<String, serde_json::Value>,
    #[serde(rename = "resultData", alias = "result_data", default)]
    pub result_data: serde_json::Value,
}

/// A friend asking you into a match on their node.
///
/// Broadcast on the shared channel by `fabric.py` the moment it arrives, so a
/// client that is *already in a game* is exactly the client this is aimed at —
/// which is why the native client ignoring it mattered: a friend inviting you
/// while you were playing got no answer and no way to know why.
///
/// Identity here is `host` (the node id the fabric authenticated). `host_name`
/// is a **label**, resolved by the backend from the person key, and is never
/// used to decide anything.
#[derive(Debug, Deserialize, Default, Clone)]
pub struct Invite {
    #[serde(default)]
    pub room: String,
    #[serde(default)]
    pub map: String,
    #[serde(default)]
    pub host: String,
    #[serde(rename = "hostName", default)]
    pub host_name: String,
    /// Which of their machines it came from. Secondary — an invite fans out to
    /// every device a person has online.
    #[serde(rename = "hostDevice", default)]
    pub host_device: String,
    #[serde(rename = "personId", default)]
    pub person_id: String,
    /// Unix seconds. The backend prunes on read, so a stale one can still be in
    /// a list this client is holding.
    #[serde(rename = "expiresAt", default)]
    pub expires_at: f64,
    #[serde(flatten)]
    pub extra: Extra,
}

/// The whole live invite list, in answer to an `invites` request.
#[derive(Debug, Deserialize, Default, Clone)]
pub struct Invites {
    #[serde(default)]
    pub invites: Vec<Invite>,
}

/// Somebody arrived. `{room, player}`.
///
/// Membership is re-derived from the next snapshot in both clients, so this
/// exists to react **within a frame** rather than up to 50 ms later.
#[derive(Debug, Deserialize, Default, Clone)]
pub struct Joined {
    #[serde(default)]
    pub room: String,
    #[serde(default)]
    pub player: PlayerRow,
    #[serde(flatten)]
    pub extra: Extra,
}

/// Somebody went. `{room, playerId}`.
///
/// Worth its own event rather than being left to the snapshot, because a
/// snapshot cannot say it: a body that disconnected and a body behind a wall
/// both simply stop appearing.
#[derive(Debug, Deserialize, Default, Clone)]
pub struct Left {
    #[serde(default)]
    pub room: String,
    #[serde(rename = "playerId", default)]
    pub player_id: String,
    #[serde(flatten)]
    pub extra: Extra,
}

/// Bots were fielded or kicked.
///
/// **Two shapes on one event**, and they are not symmetric: `added` is a list of
/// names and `removed` is a *count*. Modelled as the server actually sends it
/// rather than tidied into a pair of lists — a client that declared `removed` as
/// a list would silently parse nothing and report no bots leaving.
#[derive(Debug, Deserialize, Default, Clone)]
pub struct Roster {
    #[serde(default)]
    pub room: String,
    #[serde(default)]
    pub added: Vec<String>,
    #[serde(default)]
    pub removed: i32,
    #[serde(flatten)]
    pub extra: Extra,
}

/// The server's answer to a `ping`.
///
/// It echoes the client stamp back rather than the server measuring anything:
/// the round trip is `now - t` on *this* clock, and a difference of two clocks
/// is not a duration.
#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
pub struct Pong {
    #[serde(default)]
    pub t: f64,
    /// The server's own clock in ms when it answered. Unused today; kept because
    /// it is the field a future clock-sync would be built from.
    #[serde(rename = "serverT", default)]
    pub server_t: f64,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
pub struct Welcome {
    #[serde(default)]
    pub room: String,
    #[serde(default)]
    pub map: String,
    #[serde(rename = "playerId", default)]
    pub player_id: String,
    #[serde(rename = "snapshotHz", default)]
    pub snapshot_hz: f32,
    #[serde(default)]
    pub players: Vec<PlayerRow>,
    /// Wire keys this struct does not name. See `divergence::Extra` — the point
    /// is that adding a real field below makes the key disappear from here on
    /// its own, so the check cannot rot the way a hand-kept list would.
    #[serde(flatten)]
    pub extra: Extra,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
pub struct WireError {
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub code: String,
}

/// What the client loop actually cares about, once an envelope is classified.
#[derive(Debug)]
pub enum Event {
    Welcome(Welcome),
    Snapshot(Box<Snapshot>),
    Error(WireError),
    /// The answer to our own `ping`, which is the only measurement of the link
    /// this client has. Without it the round trip is unknown and unshowable —
    /// and "no ping displayed" is indistinguishable from "the ping is fine".
    Pong(Pong),
    /// The node's answer to a console line. See `ConsoleResponse`.
    ConsoleRes(ConsoleResponse),
    /// A friend wants you in their match.
    Invite(Box<Invite>),
    /// Every live invite, in answer to asking.
    Invites(Invites),
    /// Somebody arrived.
    Joined(Box<Joined>),
    /// Somebody left.
    Left(Left),
    /// Bots were fielded or kicked.
    Roster(Roster),
    /// A `hassault` event this build has no variant for (`invite`, `matches`,
    /// `roster`, …).
    ///
    /// Named rather than dropped silently, and **reported** on the way past —
    /// see `divergence::note_event`. This variant existing is not the same as
    /// the event being handled, and for a long time the app loop's `=> {}` made
    /// those indistinguishable.
    Other(String),
}

/// Classify one line off the socket.
///
/// Returns `None` for anything on another channel: the socket is **shared**, so
/// telemetry, agent traffic and the shell's own `system` greeting all arrive here
/// too. Treating those as malformed input would make the client fail on a healthy
/// backend.
pub fn classify(line: &str) -> Option<Event> {
    let env: Envelope = serde_json::from_str(line).ok()?;
    if env.channel != CHANNEL {
        return None;
    }
    Some(match env.event.as_str() {
        "welcome" => {
            let w: Welcome = serde_json::from_value(env.data).unwrap_or_default();
            report_welcome(&w);
            Event::Welcome(w)
        }
        "snapshot" => {
            let s: Snapshot = serde_json::from_value(env.data).unwrap_or_default();
            report_snapshot(&s);
            Event::Snapshot(Box::new(s))
        }
        "pong" => Event::Pong(serde_json::from_value(env.data).unwrap_or_default()),
        "error" => Event::Error(serde_json::from_value(env.data).unwrap_or_default()),
        "console_res" => Event::ConsoleRes(serde_json::from_value(env.data).unwrap_or_default()),
        "invite" => {
            let i: Invite = serde_json::from_value(env.data).unwrap_or_default();
            divergence::note_extra("invite", &i.extra);
            Event::Invite(Box::new(i))
        }
        "invites" => Event::Invites(serde_json::from_value(env.data).unwrap_or_default()),
        "joined" => {
            let j: Joined = serde_json::from_value(env.data).unwrap_or_default();
            divergence::note_extra("joined", &j.extra);
            Event::Joined(Box::new(j))
        }
        "left" => {
            let l: Left = serde_json::from_value(env.data).unwrap_or_default();
            divergence::note_extra("left", &l.extra);
            Event::Left(l)
        }
        "roster" => {
            let r: Roster = serde_json::from_value(env.data).unwrap_or_default();
            divergence::note_extra("roster", &r.extra);
            Event::Roster(r)
        }
        other => {
            divergence::note_event(other);
            Event::Other(other.to_string())
        }
    })
}

/// Name every wire key in a welcome this build declares no field for.
fn report_welcome(w: &Welcome) {
    divergence::note_extra("welcome", &w.extra);
    for p in &w.players {
        divergence::note_extra("welcome.players[]", &p.extra);
    }
}

/// The same walk for a snapshot, which is where the wire actually grows.
///
/// Walked rather than checked at the top level only: `spotted`, `nades` and
/// `flash` all arrived *inside* `you` and `players[]`, so a check that stopped
/// at the envelope would have reported none of them. Reporting is deduplicated
/// in `divergence`, so doing this on every one of the 20 snapshots a second
/// costs a `BTreeSet` lookup per key and prints once.
fn report_snapshot(s: &Snapshot) {
    divergence::note_extra("snapshot", &s.extra);
    divergence::note_extra("snapshot.you", &s.you.extra);
    for p in &s.players {
        divergence::note_extra("snapshot.players[]", &p.extra);
    }
    for n in &s.nades {
        divergence::note_extra("snapshot.nades[]", &n.extra);
    }
    for z in &s.zones {
        divergence::note_extra("snapshot.zones[]", &z.extra);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_throw_spells_itself_the_way_the_server_reads_it() {
        // `throw` is a Rust keyword, so the field is `r#throw` — and the whole
        // question is whether serde strips the `r#` on the way out. It does, but
        // if it ever did not, `match.py` reads `raw.get("throw")` and would find
        // nothing: the command would be accepted, the movement would be applied,
        // and the grenade would simply never leave the hand. No error anywhere.
        let mut cmd = Command::new(7);
        cmd.r#throw = true;
        cmd.nade = 2;
        cmd.lob = true;
        let wire: serde_json::Value = serde_json::from_str(&serde_json::to_string(&cmd).unwrap())
            .expect("a command serializes to an object");
        assert_eq!(wire["throw"], serde_json::json!(true));
        assert_eq!(wire["nade"], serde_json::json!(2));
        assert_eq!(wire["lob"], serde_json::json!(true));
    }

    #[test]
    fn a_command_that_is_not_a_throw_names_no_grenade() {
        // `-1`, not `0`: slot zero is the HE, so a default of zero would make
        // every movement command a request to throw one — refused by the
        // `throw` flag today, and a live grenade the day anything reads `nade`
        // without checking it.
        assert_eq!(Command::new(1).nade, -1);
        assert!(!Command::new(1).r#throw);
    }

    #[test]
    fn traffic_on_other_channels_is_not_ours() {
        // The shell greets every socket with this before anything else. A client
        // that treated it as a protocol violation would fail on connect.
        assert!(classify(r#"{"channel":"system","event":"hello","version":"1"}"#).is_none());
        assert!(classify(r#"{"channel":"telemetry","event":"tick","data":{}}"#).is_none());
        assert!(classify("not json at all").is_none());
    }

    #[test]
    fn a_welcome_carries_the_room_and_our_player_id() {
        let ev = classify(
            r#"{"channel":"hassault","event":"welcome","data":{
                "room":"r1","map":"hd_pit","playerId":"p9","snapshotHz":20,"players":[]}}"#,
        )
        .unwrap();
        match ev {
            Event::Welcome(w) => {
                assert_eq!(w.room, "r1");
                assert_eq!(w.player_id, "p9");
                assert_eq!(w.snapshot_hz, 20.0);
            }
            other => panic!("expected a welcome, got {other:?}"),
        }
    }

    #[test]
    fn a_snapshot_keeps_the_ack() {
        // Without `ack` there is nothing for prediction to replay from, so it is
        // worth a test of its own even before prediction exists.
        let ev = classify(
            r#"{"channel":"hassault","event":"snapshot","data":{
                "room":"r1","tick":7,"t":1234.0,"ack":42,
                "players":[{"id":"p9","name":"@rob","x":1.5,"y":2.5,"z":3.5,"alive":true}],
                "you":{"x":1.5,"y":2.5,"z":3.5,"hp":100,"alive":true,"weapon":2}}}"#,
        )
        .unwrap();
        match ev {
            Event::Snapshot(s) => {
                assert_eq!(s.ack, 42);
                assert_eq!(s.players.len(), 1);
                assert_eq!(s.players[0].name, "@rob");
                assert_eq!(s.you.weapon, 2);
            }
            other => panic!("expected a snapshot, got {other:?}"),
        }
    }

    #[test]
    fn a_refused_join_is_reported_with_its_code() {
        let ev = classify(
            r#"{"channel":"hassault","event":"error","data":{
                "message":"sign in and choose a username to play","code":"not_signed_in"}}"#,
        )
        .unwrap();
        match ev {
            Event::Error(e) => assert_eq!(e.code, "not_signed_in"),
            other => panic!("expected an error, got {other:?}"),
        }
    }

    #[test]
    fn a_snapshot_carries_what_we_can_hear_and_which_gun_made_it() {
        // The listener is told a bearing and a loudness. A weapon id rides along
        // on a shot — enough to tell a sniper round from a shotgun blast two
        // rooms away, and not enough to locate either.
        let ev = classify(
            r#"{"channel":"hassault","event":"snapshot","data":{"ack":1,
                "you":{"hp":100,"alive":true,"noise":[
                    {"kind":"shot","volume":0.8,"bearing":1.57,"up":0,"weapon":"sniper"},
                    {"kind":"step","volume":0.2,"bearing":-2.0,"up":-1}]}}}"#,
        )
        .unwrap();
        match ev {
            Event::Snapshot(s) => {
                assert_eq!(s.you.noise.len(), 2);
                assert_eq!(s.you.noise[0].weapon, "sniper");
                // Absent, not null: a footstep has no weapon, and the client
                // must read that as "no weapon" rather than fail to parse.
                assert_eq!(s.you.noise[1].weapon, "");
                assert_eq!(s.you.noise[1].up, -1);
            }
            other => panic!("expected a snapshot, got {other:?}"),
        }
    }

    #[test]
    fn a_command_omits_view_t_until_there_is_a_real_one() {
        // Sending a fabricated render time would ask the server to rewind its
        // position history to a moment that never existed.
        let json = serde_json::to_string(&Command::new(1)).unwrap();
        assert!(!json.contains("viewT"), "{json}");
        let mut fired = Command::new(2);
        fired.view_t = Some(1234.0);
        assert!(serde_json::to_string(&fired).unwrap().contains("viewT"));
    }

    #[test]
    fn a_join_omits_the_fields_that_mean_local() {
        // An empty `room`/`host` must be *absent*, not present-and-empty: the
        // server reads a present `host` as "this match is on a friend's node".
        let json = serde_json::to_string(&Outbound::new(
            "join",
            JoinRequest {
                map: "hd_pit".into(),
                name: "player".into(),
                ..Default::default()
            },
        ))
        .unwrap();
        assert!(!json.contains("room"), "{json}");
        assert!(!json.contains("host"), "{json}");
        assert!(json.contains(r#""channel":"hassault""#));
    }

    #[test]
    fn an_event_this_build_cannot_handle_is_named_rather_than_dropped() {
        // `roster`, `invite`, `invites` and `invite_sent` all reached this client
        // and vanished into a `=> {}` for as long as they existed. The variant
        // carrying the name is what turns that into something a person can see.
        //
        // This test used to *use* `roster` as its example, and started failing
        // the moment `roster` was handled — which is the report working: the
        // name it was written around stopped being unhandled. It now uses an
        // event nobody will ever implement, so it tests the mechanism rather
        // than the state of the backlog.
        let ev =
            classify(r#"{"channel":"hassault","event":"inventedEventForTest","data":{}}"#).unwrap();
        match ev {
            Event::Other(name) => assert_eq!(name, "inventedEventForTest"),
            other => panic!("expected an unhandled event, got {other:?}"),
        }
        assert!(
            divergence::seen().contains(&"event:inventedEventForTest".to_string()),
            "an unhandled event must leave a trace; silence is the bug"
        );
    }

    #[test]
    fn a_wire_field_with_no_rust_declaration_is_reported() {
        // The mechanism, exercised on a name invented for this test so it cannot
        // collide with a field somebody later declares for real.
        classify(
            r#"{"channel":"hassault","event":"snapshot","data":{
                "room":"r1","tick":1,"t":1.0,"ack":1,"players":[],
                "you":{"hp":100,"alive":true},
                "inventedFieldForTest":42}}"#,
        )
        .unwrap();
        assert!(
            divergence::seen().contains(&"field:snapshot.inventedFieldForTest".to_string()),
            "seen: {:?}",
            divergence::seen()
        );
    }

    #[test]
    fn a_declared_field_is_not_reported_as_extra() {
        // The self-maintaining half: `spotted` used to be exactly this kind of
        // silent arrival, and declaring it is what must remove it from the
        // report — not an edit to a list somewhere.
        classify(
            r#"{"channel":"hassault","event":"snapshot","data":{
                "room":"r1","tick":1,"t":1.0,"ack":1,"players":[],
                "you":{"hp":100,"alive":true,"spotted":["p2"],"flash":0.5}}}"#,
        )
        .unwrap();
        for key in ["field:snapshot.you.spotted", "field:snapshot.you.flash"] {
            assert!(
                !divergence::seen().contains(&key.to_string()),
                "{key} is declared and must not be reported"
            );
        }
    }

    #[test]
    fn a_snapshot_still_parses_around_an_undeclared_field() {
        // Reporting must not become refusing. A client that failed to read a
        // snapshot because the server grew a key would stop rendering the moment
        // the backend gained a feature — much worse than rendering a little less
        // of it.
        let ev = classify(
            r#"{"channel":"hassault","event":"snapshot","data":{
                "room":"r1","tick":7,"t":9.0,"ack":42,"somethingNew":{"a":1},
                "players":[{"id":"p9","x":1.5,"y":2.5,"z":3.5,"alive":true}],
                "you":{"hp":80,"alive":true}}}"#,
        )
        .unwrap();
        match ev {
            Event::Snapshot(s) => {
                assert_eq!(s.ack, 42);
                assert_eq!(s.you.hp, 80.0);
                assert_eq!(s.players.len(), 1);
            }
            other => panic!("expected a snapshot, got {other:?}"),
        }
    }

    #[test]
    fn an_fx_kind_this_build_cannot_draw_is_named() {
        // One level down from an undeclared field, and it used to be worse:
        // `#[serde(other)]` learns the tag and then throws it away, so the one
        // piece of information worth having was destroyed as it arrived.
        let ev = classify(
            r#"{"channel":"hassault","event":"snapshot","data":{
                "room":"r1","tick":1,"t":1.0,"ack":1,"players":[],
                "you":{"hp":100,"alive":true},
                "fx":[{"kind":"inventedFxForTest","id":"p1"},{"kind":"spawn","id":"p2"}]}}"#,
        )
        .unwrap();
        match ev {
            Event::Snapshot(s) => {
                assert_eq!(s.fx.len(), 2, "the unknown one is kept, just not drawn");
                assert!(
                    matches!(s.fx[1], Fx::Spawn { .. }),
                    "the known one still parses"
                );
            }
            other => panic!("expected a snapshot, got {other:?}"),
        }
        assert!(divergence::seen().contains(&"fx:inventedFxForTest".to_string()));
    }

    #[test]
    fn a_console_answer_is_a_first_class_event_now() {
        // `console_res` was in `Event::Other` for as long as the backend has
        // been able to answer a console line — which is why the native client
        // could not have a console at all.
        let ev = classify(
            r#"{"channel":"hassault","event":"console_res","data":{
                "reqId":3,"ok":true,"command":"net.graph 2","output":["ok"],
                "affectedCvars":{"net.graph":2}}}"#,
        )
        .unwrap();
        match ev {
            Event::ConsoleRes(res) => {
                assert_eq!(res.req_id, Some(3));
                assert!(res.ok);
                assert_eq!(res.output, vec!["ok".to_string()]);
                assert_eq!(res.affected_cvars.len(), 1);
            }
            other => panic!("expected a console answer, got {other:?}"),
        }
    }

    #[test]
    fn a_real_console_answer_off_the_running_node_parses() {
        // Captured verbatim from `ws://…/ws` against a live backend, keys and
        // all. Hand-written fixtures agree with whatever the person writing
        // them believed; this one agrees with the server. Two things it pins
        // that a tidier fixture would not: the key is `affectedCvars` (the REST
        // route spells the same field `affected_cvars`), and `error` arrives as
        // an explicit `null` rather than being absent — which `#[serde(default)]`
        // alone does not cover, since a present null is a value of the wrong
        // type unless the field is an `Option`.
        let ev = classify(
            r#"{"channel":"hassault","event":"console_res","data":{
                "reqId": 7, "ok": true, "command": "net.graph",
                "output": ["\"net.graph\" is \"0\" (default \"0\") - Draw in-game network graph"],
                "error": null, "affectedCvars": {}, "resultData": 0}}"#,
        )
        .unwrap();
        match ev {
            Event::ConsoleRes(res) => {
                assert_eq!(res.req_id, Some(7));
                assert!(res.ok);
                assert_eq!(res.error, None, "a present null is not an error message");
                assert_eq!(res.output.len(), 1);
                assert_eq!(res.result_data, serde_json::json!(0));
            }
            other => panic!("expected a console answer, got {other:?}"),
        }
    }

    #[test]
    fn an_invite_carries_who_and_which_room() {
        // The event that actually cost somebody something: `fabric.py`
        // broadcasts this the moment it arrives, so a client already in a game
        // is exactly who it is aimed at — and the native client dropped it.
        let ev = classify(
            r#"{"channel":"hassault","event":"invite","data":{
                "room":"r7","map":"hd_pit","host":"nodeabc","hostName":"@rob",
                "hostDevice":"desk","personId":"p1","ts":1.0,"expiresAt":61.0}}"#,
        )
        .unwrap();
        match ev {
            Event::Invite(i) => {
                assert_eq!(i.room, "r7");
                assert_eq!(i.host_name, "@rob");
                // Identity is the node id the fabric authenticated; the name is
                // a label and nothing decides anything with it.
                assert_eq!(i.host, "nodeabc");
            }
            other => panic!("expected an invite, got {other:?}"),
        }
    }

    #[test]
    fn joined_carries_a_body_and_left_carries_only_an_id() {
        // Two events, two shapes. A client that modelled them as one would read
        // an empty player row on every departure and announce that somebody
        // called "" had left.
        match classify(
            r#"{"channel":"hassault","event":"joined","data":{
                "room":"r1","player":{"id":"p2","name":"@sam","alive":true}}}"#,
        )
        .unwrap()
        {
            Event::Joined(j) => {
                assert_eq!(j.player.id, "p2");
                assert_eq!(j.player.name, "@sam");
            }
            other => panic!("expected a join, got {other:?}"),
        }
        match classify(
            r#"{"channel":"hassault","event":"left","data":{"room":"r1","playerId":"p2"}}"#,
        )
        .unwrap()
        {
            Event::Left(l) => assert_eq!(l.player_id, "p2"),
            other => panic!("expected a departure, got {other:?}"),
        }
    }

    #[test]
    fn a_roster_event_is_a_list_one_way_and_a_count_the_other() {
        // `added` is a list of names; `removed` is a **count**. Not symmetric,
        // and modelled as the server actually sends it: declaring `removed` as a
        // list parses nothing and reports no bots leaving, silently.
        match classify(
            r#"{"channel":"hassault","event":"roster","data":{"room":"r1","added":["BOT ONE","BOT TWO"]}}"#,
        )
        .unwrap()
        {
            Event::Roster(r) => {
                assert_eq!(r.added.len(), 2);
                assert_eq!(r.removed, 0);
            }
            other => panic!("expected a roster, got {other:?}"),
        }
        match classify(
            r#"{"channel":"hassault","event":"roster","data":{"room":"r1","removed":3}}"#,
        )
        .unwrap()
        {
            Event::Roster(r) => {
                assert!(r.added.is_empty());
                assert_eq!(r.removed, 3);
            }
            other => panic!("expected a roster, got {other:?}"),
        }
    }
}
