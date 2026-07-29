//! OS-level global shortcuts — chords that fire while the app is unfocused.
//!
//! The frontend owns the keymap, so this stays a dumb registrar: it is handed a
//! list of accelerators, registers exactly those, and emits a `global-shortcut`
//! event back to the webview when one fires. Deciding *which* command an
//! accelerator runs happens in `packages/core/src/keymap/global.ts`, so a
//! rebind never needs a Rust change.

use serde::Serialize;
use tauri::{AppHandle, Emitter, Runtime};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

#[derive(Clone, Serialize)]
struct Fired {
    accelerator: String,
}

/// Translate one of our key specs into a Tauri accelerator.
///
/// `mod` is ctrl-or-cmd and Tauri spells that `CmdOrCtrl`; `code:` specs are
/// positional and have no accelerator spelling at all, so they are rejected
/// rather than silently registered as the wrong key.
fn to_accelerator(spec: &str) -> Option<String> {
    let mut parts: Vec<String> = Vec::new();
    let mut tokens: Vec<&str> = spec.split('+').collect();
    let key = tokens.pop()?;
    if key.starts_with("code:") || key.is_empty() {
        return None;
    }
    for token in tokens {
        parts.push(match token.to_ascii_lowercase().as_str() {
            "mod" => "CmdOrCtrl".to_string(),
            "ctrl" | "control" => "Control".to_string(),
            "meta" | "cmd" | "command" | "super" => "Super".to_string(),
            "alt" | "option" => "Alt".to_string(),
            "shift" => "Shift".to_string(),
            _ => return None,
        });
    }
    parts.push(match key.to_ascii_lowercase().as_str() {
        "space" => "Space".to_string(),
        "esc" | "escape" => "Escape".to_string(),
        "left" | "arrowleft" => "Left".to_string(),
        "right" | "arrowright" => "Right".to_string(),
        "up" | "arrowup" => "Up".to_string(),
        "down" | "arrowdown" => "Down".to_string(),
        other => other.to_uppercase(),
    });
    Some(parts.join("+"))
}

/// Replace the registered set with exactly `accelerators`.
///
/// Whole-set rather than incremental: the OS registration is the source of truth,
/// and a diff that drifts leaves a chord bound to a command the user has since
/// rebound.
#[tauri::command]
pub fn shortcuts_register<R: Runtime>(
    app: AppHandle<R>,
    accelerators: Vec<String>,
) -> Result<Vec<String>, String> {
    let manager = app.global_shortcut();
    let _ = manager.unregister_all();

    let mut registered = Vec::new();
    for spec in accelerators {
        let Some(accelerator) = to_accelerator(&spec) else {
            continue;
        };
        let Ok(shortcut) = accelerator.parse::<Shortcut>() else {
            continue;
        };
        let app_handle = app.clone();
        let reported = spec.clone();
        // Fire on press only; the plugin reports both edges.
        let result = manager.on_shortcut(shortcut, move |_app, _shortcut, event| {
            if event.state() == ShortcutState::Pressed {
                let _ = app_handle.emit(
                    "global-shortcut",
                    Fired {
                        accelerator: reported.clone(),
                    },
                );
            }
        });
        if result.is_ok() {
            registered.push(spec);
        }
    }
    Ok(registered)
}

#[tauri::command]
pub fn shortcuts_unregister_all<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    app.global_shortcut()
        .unregister_all()
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::to_accelerator;

    #[test]
    fn maps_mod_to_cmd_or_ctrl() {
        assert_eq!(to_accelerator("mod+k").as_deref(), Some("CmdOrCtrl+K"));
        assert_eq!(
            to_accelerator("mod+shift+space").as_deref(),
            Some("CmdOrCtrl+Shift+Space")
        );
    }

    #[test]
    fn rejects_positional_specs() {
        // A `code:` spec names a physical key, which has no accelerator
        // spelling — registering its letter would bind the wrong key.
        assert_eq!(to_accelerator("mod+code:KeyW"), None);
    }

    #[test]
    fn rejects_an_unknown_modifier() {
        assert_eq!(to_accelerator("hyper+k"), None);
    }
}
