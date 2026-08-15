// The Tauri shell stays thin: app logic lives in the backend and packages/.
// Its one real job is supervising the backend process — see backend.rs.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod shortcuts;
mod updater;
mod webview;
mod window;

use std::sync::Arc;

use tauri::Manager;

fn main() {
    #[cfg(target_os = "linux")]
    {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        std::env::set_var("LIBGL_ALWAYS_SOFTWARE", "1");
        std::env::set_var("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1");
        std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
    }

    let supervisor = Arc::new(backend::BackendSupervisor::new());
    backend::start(Arc::clone(&supervisor));

    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build());

    // The updater plugin refuses to initialise without a public key, and a
    // checkout that has not had one generated yet would therefore fail to start
    // at all — which would make `pnpm dev:desktop` depend on a release-signing
    // step nobody needs in order to develop. So it is registered only once the
    // key is filled in; `updater_check` says so plainly in the meantime rather
    // than reporting "no update available", which would be a lie.
    if updater::is_configured() {
        // The plugin owns the signature check; the endpoint is chosen per call in
        // updater.rs, because the release channel is a runtime setting.
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    builder
        .manage(supervisor)
        .manage(webview::BrowserWebviews::default())
        .invoke_handler(tauri::generate_handler![
            backend::backend_status,
            updater::updater_check,
            updater::updater_install,
            shortcuts::shortcuts_register,
            shortcuts::shortcuts_unregister_all,
            window::window_is_fullscreen,
            window::window_set_fullscreen,
            window::window_toggle_fullscreen,
            window::window_minimize,
            window::window_is_maximized,
            window::window_toggle_maximize,
            window::window_close,
            window::window_start_resize_dragging,
            window::window_open_workspace,
            window::browser_open_url,
            window::open_external,
            window::open_path,
            webview::create_browser_webview,
            webview::update_browser_webview_bounds,
            webview::set_browser_webview_visible,
            webview::navigate_browser_webview,
            webview::close_browser_webview
        ])
        .build(tauri::generate_context!())
        .expect("error while building horrible-dashboard")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                // Idempotent: may fire for both events.
                app.state::<Arc<backend::BackendSupervisor>>().shutdown();
            }
        });
}

#[cfg(test)]
mod acl_coverage {
    //! Every command in `generate_handler!` must also be granted by the ACL.
    //!
    //! Registering a command is only half of making it callable: Tauri v2 also
    //! needs an `allow-*` permission defined in `permissions/` and listed in
    //! `capabilities/default.json`. Miss either half and `invoke` rejects at
    //! runtime — which is not a build error, not a type error, and not loud.
    //!
    //! `open_external` was added, registered, and shipped without its permission.
    //! The frontend's `openExternal` caught the rejection and returned `false`, the
    //! external-link bridge discarded that `false`, and the visible result was that
    //! OAuth sign-in and every external link in the desktop app did nothing at all,
    //! with no error anywhere. Three layers of correct-looking code, one missing
    //! line of TOML. This test is the line of defence that costs nothing.

    /// Command names listed in `main.rs`'s `generate_handler!`.
    fn registered_commands(source: &str) -> Vec<String> {
        let start = source
            .find("tauri::generate_handler![")
            .expect("generate_handler! not found");
        let rest = &source[start..];
        let end = rest.find(']').expect("unterminated generate_handler!");
        rest[..end]
            .split('\n')
            .skip(1)
            .filter_map(|line| {
                let line = line.trim().trim_end_matches(',').trim();
                if line.is_empty() {
                    return None;
                }
                Some(line.rsplit("::").next().unwrap_or(line).to_string())
            })
            .collect()
    }

    /// Every permission TOML. Explicitly listed rather than globbed: `include_str!`
    /// needs literal paths, so a *new* TOML file must be added here too.
    const PERMISSION_FILES: &[&str] = &[
        include_str!("../permissions/default.toml"),
        include_str!("../permissions/shortcuts.toml"),
        include_str!("../permissions/window.toml"),
        include_str!("../permissions/webview.toml"),
        include_str!("../permissions/updater.toml"),
    ];

    #[test]
    fn every_command_is_permitted_and_capable() {
        let commands = registered_commands(include_str!("main.rs"));
        assert!(
            commands.len() > 5,
            "parsed too few commands ({commands:?}) — the parser is probably broken"
        );

        let permissions: String = PERMISSION_FILES.concat();
        let capability = include_str!("../capabilities/default.json");

        for command in &commands {
            // Tauri's own convention, matching the identifiers already in use:
            // `browser_open_url` -> `allow-browser-open-url`.
            let identifier = format!("allow-{}", command.replace('_', "-"));

            assert!(
                permissions.contains(&format!("\"{command}\"")),
                "command `{command}` has no permission defining it in permissions/*.toml \
                 (expected an entry allowing \"{command}\"). Without it every \
                 invoke('{command}') is rejected by the ACL at runtime."
            );
            assert!(
                capability.contains(&format!("\"{identifier}\"")),
                "command `{command}` is registered but `{identifier}` is not listed in \
                 capabilities/default.json, so the ACL rejects every call to it."
            );
        }
    }
}
