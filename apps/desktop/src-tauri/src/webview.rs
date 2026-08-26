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

use serde::Deserialize;
use tauri::{LogicalPosition, LogicalSize, State, Webview, WebviewBuilder, WebviewUrl, Window};

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
    fn get(&self, id: &str) -> Result<Webview, String> {
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
fn parse_web_url(url: &str) -> Result<tauri::Url, String> {
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

    let builder = WebviewBuilder::new(webview_label(&id), WebviewUrl::External(parsed));
    let webview = window
        .add_child(builder, position, size)
        .map_err(|e| e.to_string())?;

    state
        .0
        .lock()
        .map_err(|_| "browser webview registry poisoned".to_string())?
        .insert(id, webview);
    Ok(())
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
    id: String,
) -> Result<(), String> {
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
pub async fn close_all_browser_webviews(state: State<'_, BrowserWebviews>) -> Result<(), String> {
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
