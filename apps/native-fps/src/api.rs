//! The node's HTTP surface, as this client needs it.
//!
//! The native client talks to **the backend already running on this machine** —
//! the same one the browser pane uses — rather than to a match server directly.
//! That is not a shortcut: identity, the map catalogue and the peer fabric all
//! live there, and `channel.py` takes the player's name from the backend's
//! signed-in account and ignores anything the client sends. Pointing at the node
//! means the native client inherits the session for free and there is no second
//! sign-in to build.
//!
//! Only three reads are needed to stand a world up: what the map is, its cube
//! grid, and the weapon numbers. All three are **served**, never duplicated in
//! Rust — the same rule the TS client follows for `plane_order`, and for the same
//! reason: a hardcoded copy is a divergence that produces no error.

use serde::{Deserialize, Deserializer};
use std::io::Read;
use std::time::Duration;

/// Read a field that may arrive as `null`, falling back to `T::default()`.
///
/// **`#[serde(default)]` does not cover this**, and the difference is exactly the
/// kind of thing that only shows up against a real server: `default` applies when
/// a key is *absent*, while an explicit `null` is a present key with a value of
/// the wrong type, and serde rejects it. The node's `MapInfo` sends
/// `"yaw": null` on every `light` entity — optional in Python, absent in nothing
/// — so a client without this fails to parse **every real map** while passing
/// every hand-written fixture.
fn null_as_default<'de, D, T>(d: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

const TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Debug)]
pub enum ApiError {
    Http(String),
    Status(u16, String),
    Decode(String),
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ApiError::Http(e) => write!(f, "could not reach the node: {e}"),
            ApiError::Status(code, path) => write!(f, "{path} answered {code}"),
            ApiError::Decode(e) => write!(f, "could not read the node's answer: {e}"),
        }
    }
}

impl std::error::Error for ApiError {}

/// Fields this stage does not consume yet are kept rather than trimmed: they are
/// the **wire's shape**, and a struct that carries only what today's code reads
/// stops being a description of the protocol. B2 (the renderer) and B3
/// (prediction) consume the rest.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Default)]
pub struct Entity {
    #[serde(default, deserialize_with = "null_as_default")]
    pub name: String,
    #[serde(default, deserialize_with = "null_as_default")]
    pub x: f32,
    #[serde(default, deserialize_with = "null_as_default")]
    pub y: f32,
    #[serde(default, deserialize_with = "null_as_default")]
    pub z: f32,
    /// Null on every `light` — the map format has no facing for one.
    #[serde(default, deserialize_with = "null_as_default")]
    pub yaw: f32,
    #[serde(default, deserialize_with = "null_as_default")]
    pub attrs: Vec<i32>,
}

/// Mirrors `models.MapInfo`. Deliberately `#[serde(default)]` throughout and
/// non-exhaustive in spirit: the node adds fields to this model, and a client
/// that refuses to parse a response because it grew a key is a client that
/// breaks on every backend release.
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize, Default)]
pub struct MapInfo {
    #[serde(default)]
    pub name: String,
    #[serde(default, deserialize_with = "null_as_default")]
    pub title: String,
    pub ssize: i32,
    pub cubic_size: usize,
    #[serde(default, deserialize_with = "null_as_default")]
    pub waterlevel: f32,
    #[serde(default, deserialize_with = "null_as_default")]
    pub entities: Vec<Entity>,
    /// The order the nine cube planes arrive in. **Read, never assumed** — this
    /// field exists so the two sides cannot drift.
    pub plane_order: Vec<String>,
    #[serde(default)]
    pub truncated: bool,
}

#[allow(dead_code)] // The map picker lands with the renderer, in B2.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct MapSummary {
    pub name: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub size: u64,
}

/// Mirrors one row of `GET /api/hassault/weapons`.
///
/// Fetched rather than hardcoded for the same reason `plane_order` is: the client
/// predicts recoil and divides its sensitivity by the scope magnification, and a
/// stale local copy of either is an aim that is wrong only in the situation the
/// number applies to.
#[allow(dead_code)] // `damage` and friends land with a scoreboard, not here.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct WeaponSpec {
    /// `knife`, `pistol`, `assault`, `shotgun`, `sniper` — the id the view model
    /// builds a shape from. A weapon's **slot is its index in this list**: the
    /// wire's `you.weapon` is an index into the server's own `WEAPONS`, and the
    /// route serves them in that order.
    #[serde(default)]
    pub id: String,
    /// The name to put on the HUD.
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub interval: f32,
    #[serde(default)]
    pub mag: i32,
    /// Rounds held outside the magazine. **`-1` is unlimited** (the sidearm), and
    /// stays unlimited — a reload that decrements it turns bottomless into four
    /// billion, which is a bug only visible on the second reload.
    #[serde(default)]
    pub reserve: i32,
    /// Seconds a reload takes. `camelCase` on the wire, like `hipfireSpread`:
    /// named `reload_time` without the rename it silently reads as zero, and an
    /// instant reload is not an error anywhere.
    #[serde(rename = "reloadTime", default)]
    pub reload_time: f32,
    /// Damage multiplier for a hit in the head band. Served rather than assumed
    /// to be 2× — the knife's is not.
    #[serde(rename = "headMultiplier", default)]
    pub head_multiplier: f32,
    /// Whether holding the trigger keeps firing. Served, so the client cannot
    /// disagree with the server about which weapons are automatic — a local
    /// guess shows a rifle refusing to hold down, or a sniper that does.
    #[serde(default)]
    pub auto: bool,
    /// The three numbers a weapon's **voice** is derived from — see
    /// `audio::weapon_voice`. Read rather than tabulated so a balance change to
    /// the gun moves the sound with it.
    #[serde(default)]
    pub damage: f32,
    #[serde(default)]
    pub rpm: f32,
    #[serde(default)]
    pub pellets: i32,
    /// Range, which together with `kickback` is how a knife is told from a gun —
    /// exactly as `noise.shot_loudness` tells them apart.
    #[serde(default)]
    pub range: f32,
    #[serde(default)]
    pub kickback: f32,
    /// Cone half-angle while scoped, in radians.
    #[serde(default)]
    pub spread: f32,
    /// Cone half-angle while **not** scoped — 27× the scoped one on the sniper,
    /// and equal to `spread` on every weapon without a scope, so the crosshair
    /// can read it unconditionally.
    #[serde(rename = "hipfireSpread", default)]
    pub hipfire_spread: f32,
    /// Magnifications the scope steps through; empty means no scope.
    ///
    /// **`camelCase` on the wire.** Named `zoom_levels` here without the rename,
    /// this silently deserialized to an empty list on every weapon — which reads
    /// exactly like "no weapon in this game has a scope".
    #[serde(rename = "zoomLevels", default)]
    pub zoom_levels: Vec<f32>,
}

/// The body a shot is resolved against, as `GET /api/hassault/hitbox` serves it.
///
/// **Fetched, never held locally.** This client used to draw bodies from three
/// constants in `world.rs` and a fourth (`CROUCH_SCALE`) in `bodies.rs`, and the
/// fourth was wrong: it was 0.75, which is the scale applied to the *eye*, while
/// a crouched body's total height is `(eye × 0.75 + above_eye) / standing` —
/// 0.784. Four percent, and it is four percent at the top of the body, which is
/// where the head band is. Aiming at the drawn head of a crouching player and
/// missing is exactly what a client-owned copy of a served number buys you.
///
/// Every derived value is served computed for the same reason: two
/// implementations of `crouch_height` is two chances to round it differently.
#[derive(Debug, Clone, Deserialize)]
pub struct HitboxSpec {
    /// Content hash of the hit-deciding dimensions. Shown in the debug overlay,
    /// so what is being drawn can be checked against what the server simulates.
    #[serde(rename = "specId", default)]
    pub spec_id: String,
    /// Only `cylinder` exists today. Carried so this client can decline to draw
    /// a shape it does not understand rather than drawing the wrong one.
    #[serde(default)]
    pub shape: String,
    #[serde(default)]
    pub radius: f32,
    #[serde(rename = "eyeHeight", default)]
    pub eye_height: f32,
    #[serde(rename = "aboveEye", default)]
    pub above_eye: f32,
    #[serde(rename = "standingHeight", default)]
    pub standing_height: f32,
    #[serde(rename = "crouchHeight", default)]
    pub crouch_height: f32,
    /// Top band of the body that takes the weapon's head multiplier. Drawn as a
    /// separate ring in the debug overlay — it is the one part of a hitbox worth
    /// being able to see, because it is the only part that changes the damage.
    #[serde(rename = "headBand", default)]
    pub head_band: f32,
}

impl Default for HitboxSpec {
    /// AssaultCube's `entity.h` defaults — the same numbers `hitbox.py` ships.
    ///
    /// Used only when the route could not be reached. Drawing against the
    /// shipped body is wrong only if somebody has tuned it; drawing against
    /// nothing does not work at all.
    fn default() -> HitboxSpec {
        HitboxSpec {
            spec_id: String::new(),
            shape: "cylinder".into(),
            radius: 1.1,
            eye_height: 4.5,
            above_eye: 0.7,
            standing_height: 5.2,
            crouch_height: 4.075,
            head_band: 1.0,
        }
    }
}

impl HitboxSpec {
    /// Body height at a crouch fraction of 0..1 — the server's `height_at`.
    pub fn height_at(&self, crouch: f32) -> f32 {
        self.standing_height + (self.crouch_height - self.standing_height) * crouch.clamp(0.0, 1.0)
    }

    /// Whether this is a shape this client knows how to draw. A body drawn as
    /// the wrong shape is worse than one not drawn: it teaches an aim.
    pub fn drawable(&self) -> bool {
        self.shape == "cylinder"
    }
}

/// One skin definition, as `skins.py` serves it.
///
/// Trimmed to what a weapon made of boxes can express: two colours and a layout.
/// The economy also carries a rarity, a collection, a pattern seed and a name,
/// and none of those change what the gun in your hands looks like.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct SkinDefinition {
    #[serde(rename = "weaponId", default)]
    pub weapon_id: String,
    #[serde(rename = "baseColor", default)]
    pub base_color: String,
    #[serde(rename = "accentColor", default)]
    pub accent_color: String,
    #[serde(rename = "patternType", default)]
    pub pattern_type: String,
}

/// One item in the player's inventory.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct SkinInstance {
    #[serde(rename = "isEquipped", default)]
    pub is_equipped: bool,
    /// 0 Factory New … 1 Battle-Scarred. Visible on the model — see
    /// `viewmodel::palette_for`.
    #[serde(rename = "floatValue", default)]
    pub float_value: f32,
    /// Absent when the node could not resolve the definition, which is the one
    /// case that must be **skipped rather than guessed**: without a base colour
    /// there is no skin, and inventing one puts a colour on the weapon that the
    /// armoury never showed the player.
    #[serde(default)]
    pub definition: Option<SkinDefinition>,
}

/// The node's HTTP origin, e.g. `http://127.0.0.1:8000`.
pub struct NodeApi {
    base: String,
    agent: ureq::Agent,
}

impl NodeApi {
    pub fn new(base: &str) -> NodeApi {
        NodeApi {
            base: base.trim_end_matches('/').to_string(),
            agent: ureq::AgentBuilder::new()
                .timeout_read(TIMEOUT)
                .timeout_connect(TIMEOUT)
                .build(),
        }
    }

    /// The `ws://…/ws` address matching this origin.
    ///
    /// Derived rather than configured separately: two addresses that must agree
    /// is two addresses that can disagree, and the failure — an HTTP call that
    /// works and a socket that never connects — reads as a backend fault.
    pub fn ws_url(&self) -> String {
        let scheme = if self.base.starts_with("https://") {
            "wss://"
        } else {
            "ws://"
        };
        let host = self
            .base
            .trim_start_matches("https://")
            .trim_start_matches("http://");
        format!("{scheme}{host}/ws")
    }

    fn get(&self, path: &str) -> Result<ureq::Response, ApiError> {
        let url = format!("{}{}", self.base, path);
        match self.agent.get(&url).call() {
            Ok(res) => Ok(res),
            Err(ureq::Error::Status(code, _)) => Err(ApiError::Status(code, path.to_string())),
            Err(e) => Err(ApiError::Http(e.to_string())),
        }
    }

    #[allow(dead_code)] // The in-client map picker arrives with B2.
    pub fn maps(&self) -> Result<Vec<MapSummary>, ApiError> {
        self.get("/api/hassault/maps")?
            .into_json()
            .map_err(|e| ApiError::Decode(e.to_string()))
    }

    pub fn map_info(&self, name: &str) -> Result<MapInfo, ApiError> {
        self.get(&format!("/api/hassault/maps/{name}"))?
            .into_json()
            .map_err(|e| ApiError::Decode(e.to_string()))
    }

    /// The raw cube planes. Sized from `cubic_size * plane_order.len()` so a
    /// truncated body is caught by `World::new` rather than read past.
    pub fn map_cubes(&self, name: &str, expected: usize) -> Result<Vec<u8>, ApiError> {
        let res = self.get(&format!("/api/hassault/maps/{name}/cubes"))?;
        // A cap, not the buffer size: `expected` is what we asked for, and a
        // reader that trusted a Content-Length could be handed a much larger one.
        let mut buf = Vec::with_capacity(expected.min(1 << 22));
        res.into_reader()
            .take((expected as u64) + 1)
            .read_to_end(&mut buf)
            .map_err(|e| ApiError::Http(e.to_string()))?;
        Ok(buf)
    }

    /// The node's settings bag. `{"values": {...}}` — the same document the
    /// browser reads, so the two surfaces cannot hold different preferences.
    pub fn settings(&self) -> Result<serde_json::Value, ApiError> {
        let res = self.get("/api/settings")?;
        let doc: serde_json::Value = res
            .into_json()
            .map_err(|e| ApiError::Decode(e.to_string()))?;
        Ok(doc.get("values").cloned().unwrap_or(doc))
    }

    /// Persist one setting. Called from the settings writer thread only — it
    /// blocks, and a frame must never wait on it.
    pub fn put_setting(&self, key: &str, value: &serde_json::Value) -> Result<(), ApiError> {
        let url = format!("{}/api/settings/{}", self.base, key);
        match self
            .agent
            .put(&url)
            .send_json(ureq::json!({ "value": value }))
        {
            Ok(_) => Ok(()),
            Err(ureq::Error::Status(code, _)) => Err(ApiError::Status(code, key.to_string())),
            Err(e) => Err(ApiError::Http(e.to_string())),
        }
    }

    /// The served body. See `HitboxSpec`.
    pub fn hitbox(&self) -> Result<HitboxSpec, ApiError> {
        self.get("/api/hassault/hitbox")?
            .into_json()
            .map_err(|e| ApiError::Decode(e.to_string()))
    }

    pub fn weapons(&self) -> Result<Vec<WeaponSpec>, ApiError> {
        self.get("/api/hassault/weapons")?
            .into_json()
            .map_err(|e| ApiError::Decode(e.to_string()))
    }

    /// The player's inventory.
    ///
    /// There is no "what am I wearing" route and this client does not want one:
    /// asking for a second endpoint to save a four-line filter would be a second
    /// source of truth for the same fact. The browser reads the same list.
    pub fn skins(&self) -> Result<Vec<SkinInstance>, ApiError> {
        self.get("/api/hassault/skins/inventory")?
            .into_json()
            .map_err(|e| ApiError::Decode(e.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_socket_address_follows_the_http_one() {
        assert_eq!(
            NodeApi::new("http://127.0.0.1:8000").ws_url(),
            "ws://127.0.0.1:8000/ws"
        );
        assert_eq!(
            NodeApi::new("https://node.example/").ws_url(),
            "wss://node.example/ws"
        );
    }

    #[test]
    fn a_null_is_read_as_a_default_not_a_parse_failure() {
        // Every `light` entity in every real map carries `"yaw": null`, so this
        // is not a defensive nicety — without it the client cannot load a single
        // map the node actually serves, while every hand-written fixture passes.
        let json = r#"{
            "name":"hd_crossing","ssize":128,"cubic_size":16384,
            "waterlevel":null,"plane_order":["type"],
            "entities":[{"type":1,"name":"light","x":63,"y":63,"z":9,"yaw":null,
                         "attrs":[112,255,240,206,0,0,0]}]
        }"#;
        let info: MapInfo = serde_json::from_str(json).unwrap();
        assert_eq!(info.waterlevel, 0.0);
        assert_eq!(info.entities[0].yaw, 0.0);
        assert_eq!(info.entities[0].name, "light");
    }

    #[test]
    fn a_weapon_is_read_with_the_wire_s_own_spelling() {
        // The camelCase keys are the trap: read as `zoom_levels` and
        // `hipfire_spread`, both come back empty on every weapon, and the only
        // symptom is a sniper with no scope and a crosshair that never opens.
        let json = r#"[{"id":"sniper","name":"sniper rifle","interval":1.5,"mag":5,
            "kickback":9.0,"spread":0.001,"hipfireSpread":0.027,
            "zoomLevels":[2.0,4.0],"damage":90,"pellets":1}]"#;
        let weapons: Vec<WeaponSpec> = serde_json::from_str(json).unwrap();
        assert_eq!(weapons[0].id, "sniper");
        assert_eq!(weapons[0].zoom_levels, vec![2.0, 4.0]);
        assert!((weapons[0].hipfire_spread - 0.027).abs() < 1e-6);
    }

    #[test]
    fn an_inventory_is_read_with_the_wire_s_camel_case_and_survives_a_missing_definition() {
        // `r##` rather than `r#`: a colour literal contains `"#`, which ends a
        // single-hash raw string in the middle of the fixture.
        let json = r##"[
            {"instanceId":"a","skinId":"assault_slate","floatValue":0.0345,
             "isEquipped":true,"wearName":"Factory New",
             "definition":{"weaponId":"assault","baseColor":"#38bdf8",
                           "accentColor":"#f43f5e","patternType":"solid"}},
            {"instanceId":"b","skinId":"gone","floatValue":0.5,"isEquipped":true}
        ]"##;
        let items: Vec<SkinInstance> = serde_json::from_str(json).unwrap();
        assert!(items[0].is_equipped);
        assert_eq!(items[0].definition.as_ref().unwrap().weapon_id, "assault");
        // A definition the node could not resolve is `None`, not a default with
        // an empty colour that would render as black.
        assert!(items[1].definition.is_none());
    }

    #[test]
    fn map_info_tolerates_fields_this_build_does_not_know() {
        // The node's `MapInfo` carries a dozen keys this client has no use for,
        // and will grow more. Refusing to parse on an unknown key would make
        // every backend release a client release.
        let json = r#"{
            "name": "hd_pit", "title": "Pit", "ssize": 128, "cubic_size": 16384,
            "plane_order": ["type","floor"], "entities": [],
            "magic": "CUBE", "version": 10, "maprevision": 3, "some_new_field": 1
        }"#;
        let info: MapInfo = serde_json::from_str(json).unwrap();
        assert_eq!(info.ssize, 128);
        assert_eq!(info.plane_order.len(), 2);
    }
}
