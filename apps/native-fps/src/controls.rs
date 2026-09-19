//! The game's key map — the same table as the browser's `controls.ts`.
//!
//! These keys used to be a `match` on `KeyCode` in `app.rs`, so the Controls
//! screen rebound keys for the pane only: the native client, which is where the
//! game is played, ignored every change. Now both read the one setting,
//! `hassault.controls`, stored as a **diff** against the defaults (see
//! `parseControls`) — so a rebind in either place is a rebind in both, and a
//! default added later still reaches someone who customized something else.
//!
//! Codes are W3C `KeyboardEvent.code` strings ("KeyW", "Digit7", "ShiftLeft").
//! winit's `KeyCode` variants are named after exactly those values, so a key's
//! `Debug` name *is* its code and no translation table exists to drift.
//!
//! The defaults and action names here are pinned against `controls.ts` by
//! `tests/browser_parity.rs`, which reads the TypeScript at test time.

use std::collections::HashMap;

use winit::keyboard::KeyCode;

/// Everything the keyboard can be told to do — `GameAction` in `controls.ts`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Action {
    Forward,
    Back,
    Left,
    Right,
    Jump,
    Crouch,
    Sprint,
    Quickswitch,
    Reload,
    Inspect,
    Ping,
    Scores,
    Noclip,
    Weapon1,
    Weapon2,
    Weapon3,
    Weapon4,
    Weapon5,
    NadeHe,
    NadeFlash,
    NadeSmoke,
    NadeMolotov,
    Throw,
    Lob,
    Use,
    Buy,
    Drop,
    Voice,
    ChatAll,
    ChatTeam,
}

/// `(action, its name in controls.ts, the default keys)`, in the TS order.
pub const DEFAULTS: &[(Action, &str, &[&str])] = &[
    (Action::Forward, "forward", &["KeyW", "ArrowUp"]),
    (Action::Back, "back", &["KeyS", "ArrowDown"]),
    (Action::Left, "left", &["KeyA", "ArrowLeft"]),
    (Action::Right, "right", &["KeyD", "ArrowRight"]),
    (Action::Jump, "jump", &["Space"]),
    (Action::Crouch, "crouch", &["ControlLeft", "KeyC"]),
    (Action::Sprint, "sprint", &["ShiftLeft", "ShiftRight"]),
    (Action::Quickswitch, "quickswitch", &["KeyQ"]),
    (Action::Reload, "reload", &["KeyR"]),
    (Action::Inspect, "inspect", &["KeyF"]),
    (Action::Ping, "ping", &["KeyZ"]),
    (Action::Scores, "scores", &["Tab"]),
    (Action::Noclip, "noclip", &["KeyN"]),
    (Action::Weapon1, "weapon1", &["Digit1"]),
    (Action::Weapon2, "weapon2", &["Digit2"]),
    (Action::Weapon3, "weapon3", &["Digit3"]),
    (Action::Weapon4, "weapon4", &["Digit4"]),
    (Action::Weapon5, "weapon5", &["Digit5"]),
    (Action::NadeHe, "nadeHe", &["Digit6"]),
    (Action::NadeFlash, "nadeFlash", &["Digit7"]),
    (Action::NadeSmoke, "nadeSmoke", &["Digit8"]),
    (Action::NadeMolotov, "nadeMolotov", &["Digit9"]),
    (Action::Throw, "throw", &["KeyG"]),
    (Action::Lob, "lob", &["KeyH"]),
    (Action::Use, "use", &["KeyE"]),
    (Action::Buy, "buy", &["KeyB"]),
    (Action::Drop, "drop", &["KeyX"]),
    (Action::Voice, "voice", &["KeyV"]),
    (Action::ChatAll, "chatAll", &["KeyY"]),
    (Action::ChatTeam, "chatTeam", &["KeyU"]),
];

/// `RESERVED_CODES` in `controls.ts`: Escape is the way back to this table, and
/// the F-keys are the browser's.
pub const RESERVED: &[&str] = &["Escape", "F5", "F11", "F12"];
/// Keys per action, as in `controls.ts`.
pub const SLOTS: usize = 2;

/// The live map: a code to the action it performs.
#[derive(Debug, Clone)]
pub struct Controls {
    by_code: HashMap<String, Action>,
    /// The bindings in action order, primary key first — for labels.
    table: Vec<(Action, Vec<String>)>,
}

impl Default for Controls {
    fn default() -> Controls {
        Controls::parse(None)
    }
}

impl Controls {
    /// Read the stored setting — a JSON *string* holding `{action: [codes]}` for
    /// the actions that differ from the defaults. Anything unrecognizable is
    /// dropped rather than failing the whole table: a bad entry must not cost a
    /// player their controls.
    pub fn parse(raw: Option<&str>) -> Controls {
        let mut table: Vec<(Action, Vec<String>)> = DEFAULTS
            .iter()
            .map(|(a, _, keys)| (*a, keys.iter().map(|k| k.to_string()).collect()))
            .collect();
        let overrides = raw
            .and_then(|s| serde_json::from_str::<serde_json::Value>(s).ok())
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_default();
        for (name, value) in overrides {
            let Some(index) = DEFAULTS.iter().position(|(_, n, _)| *n == name) else {
                continue;
            };
            let Some(list) = value.as_array() else {
                continue;
            };
            table[index].1 = list
                .iter()
                .filter_map(|c| c.as_str())
                .filter(|c| !c.is_empty() && !RESERVED.contains(c))
                .take(SLOTS)
                .map(str::to_string)
                .collect();
        }
        // First binding wins, in action order — the same rule as `codeMap`, so a
        // duplicate left in hand-edited storage resolves identically in both.
        let mut by_code = HashMap::new();
        for (action, codes) in &table {
            for code in codes {
                by_code.entry(code.clone()).or_insert(*action);
            }
        }
        Controls { by_code, table }
    }

    /// Read the table out of the node's settings bag. Kept beside `Settings`
    /// rather than inside it because `Settings` is `Copy` and a map is not.
    pub fn from_values(values: &serde_json::Value) -> Controls {
        // Stored as a JSON *string* by the pane (`serializeControls`), not as an
        // object — read it as the pane wrote it.
        Controls::parse(
            values
                .get(crate::settings::KEY_CONTROLS)
                .and_then(|v| v.as_str()),
        )
    }

    /// What `key` is bound to, if anything.
    pub fn action(&self, key: KeyCode) -> Option<Action> {
        self.by_code.get(&code_name(key)).copied()
    }

    /// Whether any action holds this key. Used to keep a fixed fallback (the
    /// arrow keys' look) only for arrows the player has not bound to anything.
    pub fn is_bound(&self, key: KeyCode) -> bool {
        self.by_code.contains_key(&code_name(key))
    }

    /// The primary key bound to `action`, as a label — for on-screen hints.
    pub fn label(&self, action: Action) -> String {
        self.table
            .iter()
            .find(|(a, _)| *a == action)
            .and_then(|(_, codes)| codes.first())
            .map(|c| key_label(c))
            .unwrap_or_else(|| "-".to_string())
    }
}

/// A key's `KeyboardEvent.code`. winit names its variants after those values.
pub fn code_name(key: KeyCode) -> String {
    format!("{key:?}")
}

/// A code as something to print on a key cap, like `keyLabel` in `controls.ts`.
pub fn key_label(code: &str) -> String {
    if let Some(rest) = code.strip_prefix("Key") {
        return rest.to_string();
    }
    if let Some(rest) = code.strip_prefix("Digit") {
        return rest.to_string();
    }
    match code {
        "ShiftLeft" => "L SHIFT".into(),
        "ShiftRight" => "R SHIFT".into(),
        "ControlLeft" => "L CTRL".into(),
        "ControlRight" => "R CTRL".into(),
        "AltLeft" => "L ALT".into(),
        "AltRight" => "R ALT".into(),
        other => other.to_ascii_uppercase(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_map_the_grenades_voice_and_chat() {
        let c = Controls::default();
        assert_eq!(c.action(KeyCode::Digit7), Some(Action::NadeFlash));
        assert_eq!(c.action(KeyCode::Digit8), Some(Action::NadeSmoke));
        assert_eq!(c.action(KeyCode::Digit9), Some(Action::NadeMolotov));
        assert_eq!(c.action(KeyCode::KeyV), Some(Action::Voice));
        assert_eq!(c.action(KeyCode::KeyY), Some(Action::ChatAll));
        assert_eq!(c.action(KeyCode::KeyU), Some(Action::ChatTeam));
    }

    #[test]
    fn a_stored_diff_rebinds_only_what_it_names() {
        let c = Controls::parse(Some(r#"{"nadeFlash":["KeyT"],"voice":["CapsLock"]}"#));
        assert_eq!(c.action(KeyCode::KeyT), Some(Action::NadeFlash));
        assert_eq!(c.action(KeyCode::Digit7), None);
        assert_eq!(c.action(KeyCode::CapsLock), Some(Action::Voice));
        assert_eq!(c.action(KeyCode::KeyV), None);
        // Untouched actions keep their defaults.
        assert_eq!(c.action(KeyCode::Digit8), Some(Action::NadeSmoke));
    }

    #[test]
    fn garbage_is_dropped_not_fatal() {
        let c = Controls::parse(Some(
            r#"{"nope":["KeyT"],"jump":["Escape","F12"],"forward":5}"#,
        ));
        assert_eq!(c.action(KeyCode::KeyW), Some(Action::Forward));
        assert_eq!(c.action(KeyCode::Escape), None);
        assert_eq!(
            c.action(KeyCode::Space),
            None,
            "jump was rebound to nothing usable"
        );
        assert!(Controls::parse(Some("not json"))
            .action(KeyCode::KeyW)
            .is_some());
    }

    #[test]
    fn a_winit_key_name_is_its_w3c_code() {
        for (key, code) in [
            (KeyCode::KeyW, "KeyW"),
            (KeyCode::Digit7, "Digit7"),
            (KeyCode::ShiftLeft, "ShiftLeft"),
            (KeyCode::Space, "Space"),
            (KeyCode::ArrowUp, "ArrowUp"),
            (KeyCode::Backquote, "Backquote"),
            (KeyCode::ControlLeft, "ControlLeft"),
        ] {
            assert_eq!(code_name(key), code);
        }
    }
}
