//! Microphone access for the app's own windows on Linux.
//!
//! WebKitGTK asks the embedder about every `getUserMedia` call through the web
//! view's `permission-request` signal, and a request **nobody answers is denied**.
//! wry connects no handler, so on Linux every microphone request failed with
//! `NotAllowedError` — lobby voice, the Plaza and Clubhouse voice all read
//! "permission was denied" there while the identical build worked on Windows
//! (WebView2 prompts) and macOS (wry's UI delegate answers).
//!
//! Granted only on windows that load the app itself (`main` and the `ws-*`
//! workspace windows). The embedded browser's child webviews and `browser-*`
//! windows show arbitrary sites and are deliberately left alone, so an open tab
//! can never listen to the room.

use tauri::{Runtime, WebviewWindow};

/// WebView2's browser arguments, for **every** webview this app creates.
///
/// `AudioServiceSandbox` is the point. On at least one Windows 11 machine the
/// sandboxed audio service crash-loops inside WebView2 (same 153.x build as
/// Edge, where it is fine; the dumps show it dying before any audio DLL loads),
/// and while it is down `enumerateDevices()` lists **no audio devices at all**, so
/// every `getUserMedia` fails with "Requested device not found". Verified live
/// over CDP: with the sandbox on, zero inputs and outputs; off, all 13 inputs and
/// the default mic opens. The audio service is the one that talks to WASAPI, and
/// running it unsandboxed is what Chrome did for years and what the enterprise
/// `AudioSandboxEnabled=false` policy still does — the renderer stays sandboxed.
///
/// The rest restates wry's defaults, because setting any arguments **replaces**
/// them: the mini-menu/PDF/SmartScreen features it disables, and the autoplay
/// policy wry adds when autoplay is on (its default) — without which the mixer's
/// playback would wait for a click.
///
/// Must be identical everywhere: WebView2 refuses to create a second webview on
/// the same user-data folder with different arguments. `tauri.conf.json`'s
/// `additionalBrowserArgs` for `main` is checked against this by a test.
pub const WEBVIEW2_ARGS: &str = "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection,AudioServiceSandbox --autoplay-policy=no-user-gesture-required";

/// Let `window`'s page open the microphone (and enumerate devices, which the
/// mixer's input picker needs to show names rather than blank entries).
pub fn allow_user_media<R: Runtime>(window: &WebviewWindow<R>) {
    #[cfg(target_os = "linux")]
    {
        let result = window.with_webview(|webview| {
            use webkit2gtk::glib::prelude::*;
            use webkit2gtk::{
                DeviceInfoPermissionRequest, PermissionRequestExt, SettingsExt,
                UserMediaPermissionRequest, WebViewExt,
            };

            let view = webview.inner();
            // Off by default in WebKitGTK: without these `getUserMedia` or
            // `RTCPeerConnection` is simply absent from the page.
            if let Some(settings) = WebViewExt::settings(&view) {
                settings.set_enable_media_stream(true);
                settings.set_enable_webrtc(true);
            }
            view.connect_permission_request(|_, request| {
                if request.is::<UserMediaPermissionRequest>()
                    || request.is::<DeviceInfoPermissionRequest>()
                {
                    request.allow();
                    true
                } else {
                    // Not ours to decide: fall through to WebKit's default.
                    false
                }
            });
        });
        if let Err(e) = result {
            eprintln!("media: could not enable microphone access: {e}");
        }
    }
    #[cfg(not(target_os = "linux"))]
    let _ = window;
}

#[cfg(test)]
mod tests {
    use super::WEBVIEW2_ARGS;

    /// The main window's arguments live in `tauri.conf.json`; every other webview
    /// takes them from `WEBVIEW2_ARGS`. WebView2 refuses to create a webview whose
    /// arguments differ from the first one on the same user-data folder, so a drift
    /// here is a pop-out window or embedded browser that silently never opens.
    #[test]
    fn conf_windows_use_the_same_webview2_args() {
        let conf: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).expect("tauri.conf.json");
        let windows = conf["app"]["windows"].as_array().expect("app.windows");
        assert!(!windows.is_empty());
        for window in windows {
            assert_eq!(
                window["additionalBrowserArgs"].as_str(),
                Some(WEBVIEW2_ARGS),
                "every window in tauri.conf.json must carry media::WEBVIEW2_ARGS"
            );
        }
    }

    /// Setting arguments replaces wry's defaults, so they must be restated.
    #[test]
    fn args_keep_wrys_defaults_and_disable_the_audio_sandbox() {
        for part in [
            "msWebOOUI",
            "msPdfOOUI",
            "msSmartScreenProtection",
            "AudioServiceSandbox",
            "--autoplay-policy=no-user-gesture-required",
        ] {
            assert!(WEBVIEW2_ARGS.contains(part), "missing {part}");
        }
        // One `--disable-features` only: a second occurrence overrides the first.
        assert_eq!(WEBVIEW2_ARGS.matches("--disable-features").count(), 1);
    }
}
