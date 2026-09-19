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
