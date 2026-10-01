//! `browser.nativeWebview` — a **real webview** overlaid on the embedded-browser pane.
//!
//! The browser module's other two modes both compromise: the `<iframe>` is refused by
//! any site sending `X-Frame-Options`/CSP `frame-ancestors`, and the server-rendered
//! Chromium engine streams JPEG frames, so it costs a decode per frame and can never
//! be as smooth as native compositing. On the desktop there's a third option: ask the
//! shell for an actual child webview and park it over the pane's placeholder.
//!
//! ## The catch, and why these commands look the way they do
//!
//! A native child webview is **not** part of the HTML layer. It is a sibling surface
//! composited by the OS *above* everything the frontend draws — the command palette,
//! dropdowns, dialogs, the workspace tab strip, drag previews. There is no z-index
//! that reaches it. That is not a bug to be tested away; it is the defining property
//! of the approach, and the reason `set_browser_webview_visible` exists alongside the
//! four positioning commands. The frontend is responsible for hiding the child
//! whenever something must render on top of it (see NativeBrowserView.tsx).
//!
//! Every command is `async` on purpose. `Window::add_child` dispatches to the main
//! thread and **blocks** on the reply; a sync Tauri command already runs on the main
//! thread, so it would deadlock the whole app. Async runs it on a worker thread,
//! leaving the main thread free to service the request.
//!
//! Gated by `tauri`'s `unstable` feature (multi-webview is not yet stable API).

use std::collections::HashMap;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::webview::{DownloadEvent, NewWindowResponse, PageLoadEvent};
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, Webview, WebviewBuilder,
    WebviewUrl, Window,
};

use crate::browser_cdp::{configure_browser_settings, CdpSubscriptions};

/// The Tauri event carrying what a native page does on its own — load progress,
/// title changes, links that want a new tab, downloads — which the pane needs to
/// behave like a browser (URL bar, tab labels, tab strip, download status).
pub const WEBVIEW_EVENT: &str = "browser-webview";

/// One [`WEBVIEW_EVENT`], tagged with the pane/tab id that owns the webview.
#[derive(Serialize, Clone)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum WebviewEvent {
    /// A navigation started (`loading`) or finished. The URL bar follows this, so
    /// links clicked *inside* the page show up in the pane.
    Load {
        id: String,
        url: String,
        loading: bool,
    },
    Title {
        id: String,
        title: String,
    },
    /// A `target=_blank` link or a plain `window.open(url)`: the pane opens a tab.
    NewTab {
        id: String,
        url: String,
    },
    Download {
        id: String,
        url: String,
        path: Option<String>,
        state: DownloadState,
    },
}

#[derive(Serialize, Clone, Copy)]
#[serde(rename_all = "camelCase")]
pub enum DownloadState {
    Started,
    Done,
    Failed,
}

fn emit(app: &AppHandle, event: WebviewEvent) {
    let _ = app.emit(WEBVIEW_EVENT, event);
}

/// Pane rectangle in **logical** (CSS) pixels, as the frontend measures it with
/// `getBoundingClientRect()`. Logical rather than physical on purpose: Tauri applies
/// the window's scale factor itself, so passing physical pixels would double-scale
/// the overlay on any HiDPI display.
#[derive(Deserialize, Clone, Copy, Debug)]
pub struct Bounds {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl Bounds {
    /// Clamp to something a webview can actually occupy. A pane can legitimately
    /// measure 0×0 (collapsed, or on a workspace that isn't showing), and some
    /// platforms treat a zero-sized surface as an error rather than an empty one.
    fn sanitized(self) -> (LogicalPosition<f64>, LogicalSize<f64>) {
        (
            LogicalPosition::new(self.x, self.y),
            LogicalSize::new(self.width.max(1.0), self.height.max(1.0)),
        )
    }
}

/// Live child webviews, keyed by the pane instance id that owns each one.
#[derive(Default)]
pub struct BrowserWebviews(Mutex<HashMap<String, Webview>>);

impl BrowserWebviews {
    pub(crate) fn get(&self, id: &str) -> Result<Webview, String> {
        self.0
            .lock()
            .map_err(|_| "browser webview registry poisoned".to_string())?
            .get(id)
            .cloned()
            .ok_or_else(|| format!("no native browser webview with id `{id}`"))
    }
}

/// Only `http`/`https` may be loaded, so a compromised or buggy frontend can never
/// steer a child webview at `file:`/`tauri:` local resources — where it would run
/// with app privileges rather than as a foreign page.
pub(crate) fn parse_web_url(url: &str) -> Result<tauri::Url, String> {
    let parsed = tauri::Url::parse(url).map_err(|e| e.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(format!("refusing to load non-http(s) URL: {url}"));
    }
    Ok(parsed)
}

/// Tauri labels allow only `[a-zA-Z0-9-/:_]`; pane instance ids are uuids in
/// practice, but anything else collapses to `_`.
fn webview_label(id: &str) -> String {
    let safe: String = id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("browser-view-{safe}")
}

/// Create (or re-point) the child webview for pane `id` at `bounds`.
///
/// Idempotent: a pane that remounts — a workspace switch, a React StrictMode double
/// mount — must not spawn a second surface. An existing child is repositioned and
/// navigated instead, which is also what makes the overlay survive a remount without
/// reloading the page the user was on.
#[tauri::command]
pub async fn create_browser_webview(
    window: Window,
    state: State<'_, BrowserWebviews>,
    id: String,
    url: String,
    bounds: Bounds,
) -> Result<(), String> {
    let parsed = parse_web_url(&url)?;
    let (position, size) = bounds.sanitized();

    if let Ok(existing) = state.get(&id) {
        existing.set_position(position).map_err(|e| e.to_string())?;
        existing.set_size(size).map_err(|e| e.to_string())?;
        existing.navigate(parsed).map_err(|e| e.to_string())?;
        return existing.show().map_err(|e| e.to_string());
    }

    // Same arguments as the window it lives in — see `media::WEBVIEW2_ARGS`.
    let builder = browser_hooks(
        window.app_handle(),
        &id,
        WebviewBuilder::new(webview_label(&id), WebviewUrl::External(parsed))
            .additional_browser_args(crate::media::WEBVIEW2_ARGS)
            .general_autofill_enabled(true),
    );
    let webview = window
        .add_child(builder, position, size)
        .map_err(|e| e.to_string())?;
    configure_browser_settings(&webview);

    state
        .0
        .lock()
        .map_err(|_| "browser webview registry poisoned".to_string())?
        .insert(id, webview);
    Ok(())
}

/// What makes the child a *browser* rather than a page: each hook reports something
/// the pane has to hear about to keep its chrome honest.
fn browser_hooks(
    app: &AppHandle,
    id: &str,
    builder: WebviewBuilder<tauri::Wry>,
) -> WebviewBuilder<tauri::Wry> {
    let (load_app, load_id) = (app.clone(), id.to_string());
    let (title_app, title_id) = (app.clone(), id.to_string());
    let (tab_app, tab_id) = (app.clone(), id.to_string());
    let (dl_app, dl_id) = (app.clone(), id.to_string());
    builder
        .on_page_load(move |_, payload| {
            emit(
                &load_app,
                WebviewEvent::Load {
                    id: load_id.clone(),
                    url: payload.url().to_string(),
                    loading: matches!(payload.event(), PageLoadEvent::Started),
                },
            );
        })
        .on_document_title_changed(move |_, title| {
            emit(
                &title_app,
                WebviewEvent::Title {
                    id: title_id.clone(),
                    title,
                },
            );
        })
        .on_new_window(move |url, features| {
            // A popup that asked for a size or position is a *window* its opener
            // still talks to: OAuth and payment flows post their result back
            // through `window.opener`. Let WebView2 open it as one. Everything
            // else (`target=_blank`, a bare `window.open(url)`) becomes a tab.
            if features.size().is_some() || features.position().is_some() {
                return NewWindowResponse::Allow;
            }
            if parse_web_url(url.as_str()).is_ok() {
                emit(
                    &tab_app,
                    WebviewEvent::NewTab {
                        id: tab_id.clone(),
                        url: url.to_string(),
                    },
                );
            }
            NewWindowResponse::Deny
        })
        .on_download(move |_, event| {
            // WebView2 already defaults the destination to the user's Downloads
            // folder and shows its own progress flyout; the pane only reports it.
            let (url, path, state) = match event {
                DownloadEvent::Requested { url, destination } => (
                    url,
                    Some(destination.display().to_string()).filter(|p| !p.is_empty()),
                    DownloadState::Started,
                ),
                DownloadEvent::Finished { url, path, success } => (
                    url,
                    path.map(|p| p.display().to_string()),
                    if success {
                        DownloadState::Done
                    } else {
                        DownloadState::Failed
                    },
                ),
                _ => return true,
            };
            emit(
                &dl_app,
                WebviewEvent::Download {
                    id: dl_id.clone(),
                    url: url.to_string(),
                    path,
                    state,
                },
            );
            true
        })
}

/// Follow the pane: called on every resize, split drag, scroll and dock change.
#[tauri::command]
pub async fn update_browser_webview_bounds(
    state: State<'_, BrowserWebviews>,
    id: String,
    bounds: Bounds,
) -> Result<(), String> {
    let webview = state.get(&id)?;
    let (position, size) = bounds.sanitized();
    webview.set_position(position).map_err(|e| e.to_string())?;
    webview.set_size(size).map_err(|e| e.to_string())
}

/// Show or hide the overlay without destroying it.
///
/// This is the occlusion control. Because a native child composites above the HTML
/// layer, the frontend hides it whenever the app needs to draw over that region —
/// the command palette, a modal, a pane drag, or simply the pane's workspace not
/// being the visible one. Hiding preserves the page (and its scroll position and JS
/// state), which closing would not.
#[tauri::command]
pub async fn set_browser_webview_visible(
    state: State<'_, BrowserWebviews>,
    id: String,
    visible: bool,
) -> Result<(), String> {
    let webview = state.get(&id)?;
    if visible {
        webview.show().map_err(|e| e.to_string())
    } else {
        webview.hide().map_err(|e| e.to_string())
    }
}

/// Point the overlay at a new URL (URL bar, bookmark, history, back/forward/home).
#[tauri::command]
pub async fn navigate_browser_webview(
    state: State<'_, BrowserWebviews>,
    id: String,
    url: String,
) -> Result<(), String> {
    let webview = state.get(&id)?;
    webview
        .navigate(parse_web_url(&url)?)
        .map_err(|e| e.to_string())
}

/// Destroy the overlay for pane `id`.
///
/// Dropping the registry entry before closing: a close that fails (the window is
/// already gone during shutdown) must still forget the handle, or the pane can never
/// create a replacement — `create_browser_webview` would keep finding the dead one.
#[tauri::command]
pub async fn close_browser_webview(
    state: State<'_, BrowserWebviews>,
    subscriptions: State<'_, CdpSubscriptions>,
    id: String,
) -> Result<(), String> {
    subscriptions.forget(&id);
    let webview = state
        .0
        .lock()
        .map_err(|_| "browser webview registry poisoned".to_string())?
        .remove(&id);
    match webview {
        Some(webview) => webview.close().map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

/// Destroy every overlay this process is holding.
///
/// The frontend owns each surface through a pane session, and a **page reload wipes
/// every one of those owners** while the OS window — and its child webviews — live
/// on. What is left is a webview composited over the app that no pane will ever
/// claim, hide or close again: a page frozen in mid-air, unreachable by any UI.
/// The frontend calls this once at boot, before any pane mounts. Nothing of value
/// is lost: a pane that is still in the restored layout re-creates its surface on
/// mount, and one that isn't should never have had a surface at all.
#[tauri::command]
pub async fn close_all_browser_webviews(
    state: State<'_, BrowserWebviews>,
    subscriptions: State<'_, CdpSubscriptions>,
) -> Result<(), String> {
    subscriptions.forget_all();
    let webviews: Vec<Webview> = {
        let mut registry = state
            .0
            .lock()
            .map_err(|_| "browser webview registry poisoned".to_string())?;
        registry.drain().map(|(_, webview)| webview).collect()
    };
    // Best effort per surface: one that is already gone must not stop the rest from
    // being swept, or a single stale handle leaves live overlays on screen.
    for webview in webviews {
        let _ = webview.close();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_is_tauri_safe() {
        assert_eq!(webview_label("abc-123_x"), "browser-view-abc-123_x");
        assert_eq!(webview_label("a b/c.d"), "browser-view-a_b_c_d");
    }

    #[test]
    fn only_http_urls_load() {
        assert!(parse_web_url("https://example.com").is_ok());
        assert!(parse_web_url("http://example.com").is_ok());
        // The whole point of the check: local/privileged schemes stay unreachable.
        assert!(parse_web_url("file:///etc/passwd").is_err());
        assert!(parse_web_url("tauri://localhost").is_err());
        assert!(parse_web_url("javascript:alert(1)").is_err());
        assert!(parse_web_url("not a url").is_err());
    }

    #[test]
    fn zero_sized_bounds_are_clamped() {
        let (_, size) = Bounds {
            x: 10.0,
            y: 20.0,
            width: 0.0,
            height: 0.0,
        }
        .sanitized();
        assert_eq!(size.width, 1.0);
        assert_eq!(size.height, 1.0);
    }
}
