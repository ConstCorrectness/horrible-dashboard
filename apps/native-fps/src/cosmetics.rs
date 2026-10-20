//! How each player looks: their agent and their weapon skins, per side.
//!
//! The bundle is the server's (`backend/modules/hassault/cosmetics.py`), built
//! from the account's own rows on the game server and sent in the welcome, in
//! `joined` and as a `cosmetics` event; never in a snapshot. Both sides travel
//! together and the half drawn is the one for the team a player is on now. The
//! browser's `cosmetics.ts` reads the same wire, and `cosmetics-vectors.json`
//! pins both against the Python.
//!
//! Read leniently: an older server sends no bundle, and a player without one is
//! drawn as before, in the team's wash with bare-metal guns.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::viewmodel::Skin;

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct AgentLook {
    #[serde(default)]
    pub id: String,
    /// `#rrggbb`: the wash over the operator's uniform.
    #[serde(default)]
    pub armor: String,
    /// `#rrggbb`: trim.
    #[serde(default)]
    pub trim: String,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct SkinEntry {
    #[serde(rename = "skinId", default)]
    pub skin_id: String,
    #[serde(default)]
    pub name: String,
    #[serde(rename = "baseColor", default)]
    pub base_color: String,
    #[serde(rename = "accentColor", default)]
    pub accent_color: String,
    #[serde(rename = "patternType", default)]
    pub pattern_type: String,
    #[serde(rename = "floatValue", default)]
    pub float_value: f32,
    #[serde(default)]
    pub seed: i64,
    #[serde(rename = "statTrak", default)]
    pub stat_trak: Option<i64>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct CosmeticsSide {
    #[serde(default)]
    pub agent: AgentLook,
    /// By weapon id: the gun's own id, not the model it borrows.
    #[serde(default)]
    pub weapons: HashMap<String, SkinEntry>,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct Cosmetics {
    #[serde(default)]
    pub t: CosmeticsSide,
    #[serde(default)]
    pub ct: CosmeticsSide,
}

/// The `cosmetics` event: `{playerId, cosmetics}`.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct CosmeticsChanged {
    #[serde(rename = "playerId", default)]
    pub player_id: String,
    #[serde(default)]
    pub cosmetics: Option<Cosmetics>,
}

/// `weapons.side_of`: ARC (team 0) plays T, HALON (team 1) CT.
pub fn side_of(team: i32) -> &'static str {
    if team == 1 {
        "ct"
    } else {
        "t"
    }
}

impl Cosmetics {
    pub fn side(&self, team: i32) -> &CosmeticsSide {
        if team == 1 {
            &self.ct
        } else {
            &self.t
        }
    }

    /// The skin on `weapon_id` for a player on `team`, as the drawers take it.
    pub fn skin_for(&self, team: i32, weapon_id: &str) -> Option<Skin> {
        let entry = self.side(team).weapons.get(weapon_id)?;
        Some(Skin {
            id: (!entry.skin_id.is_empty()).then(|| entry.skin_id.clone()),
            base_color: entry.base_color.clone(),
            accent_color: entry.accent_color.clone(),
            pattern_type: entry.pattern_type.clone(),
            float_value: entry.float_value,
        })
    }

    /// Every skin on one team's side, keyed by weapon id: the first-person view
    /// model's table.
    pub fn skins_for_team(&self, team: i32) -> HashMap<String, Skin> {
        self.side(team)
            .weapons
            .keys()
            .filter_map(|id| self.skin_for(team, id).map(|s| (id.clone(), s)))
            .collect()
    }

    /// The agent's wash for a player on `team`, as linear-ish 0..1 RGB, or
    /// `None` when the server sent an unreadable colour.
    pub fn armor(&self, team: i32) -> Option<[f32; 3]> {
        parse_hex(&self.side(team).agent.armor)
    }
}

/// `#rrggbb` to 0..1 floats, or `None`.
pub fn parse_hex(value: &str) -> Option<[f32; 3]> {
    let hex = value.trim().trim_start_matches('#');
    if hex.len() != 6 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let channel = |i: usize| {
        u8::from_str_radix(&hex[i..i + 2], 16)
            .ok()
            .map(|v| v as f32 / 255.0)
    };
    Some([channel(0)?, channel(2)?, channel(4)?])
}

/// The welcome's `{playerId: bundle}`.
pub type CosmeticsMap = HashMap<String, Cosmetics>;
