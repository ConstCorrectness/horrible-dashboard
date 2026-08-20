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
    #[serde(default)]
    pub kills: i32,
    #[serde(default)]
    pub deaths: i32,
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
    #[serde(default)]
    pub x: f32,
    #[serde(default)]
    pub y: f32,
    #[serde(default)]
    pub z: f32,
    #[serde(default)]
    pub hp: f32,
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
    #[serde(default)]
    pub fx: Vec<Fx>,
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
    /// A `hassault` event this build has no use for (`invite`, `matches`, `pong`,
    /// …). Named rather than dropped silently so `--verbose` can show it.
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
        "welcome" => Event::Welcome(serde_json::from_value(env.data).unwrap_or_default()),
        "snapshot" => Event::Snapshot(Box::new(
            serde_json::from_value(env.data).unwrap_or_default(),
        )),
        "error" => Event::Error(serde_json::from_value(env.data).unwrap_or_default()),
        other => Event::Other(other.to_string()),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
