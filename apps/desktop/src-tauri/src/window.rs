//! Native OS-window controls exposed to the frontend (phase-2 native shell).
//!
//! Two capabilities ride these commands:
//! - `window.fullscreen` — borderless OS-window fullscreen, distinct from the
//!   desktop's in-window presentation (a pane filling the page).
//! - `chrome.workspaceTabs` — the app draws the window's own titlebar (the window
//!   is undecorated), so the frontend drives edge-resize and
//!   minimize/maximize/close itself. (Move-drag + double-click-maximize use the
//!   webview's native `data-tauri-drag-region`, not a command here.) Named for the
//!   workspace tab strip that used to be that titlebar.
//!
//! The frontend gates each behind its capability and drives them through the
//! core `WindowControl` seam; the shell stays a thin pass-through to tao's
//! window API.
//!
//! **Commands take `Window` (or `Webview`), never `WebviewWindow`.** Tauri only
//! resolves a `WebviewWindow` argument while the window holds a single webview;
//! a native browser pane (src/webview.rs) adds a child webview, after which every
//! such command is rejected with "current webview is not a WebviewWindow" — the
//! titlebar buttons and F11 went dead whenever a Browser pane was open.

use std::collections::HashSet;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use tauri::{AppHandle, Webview, WebviewUrl, WebviewWindowBuilder, Window};
use tauri_runtime::ResizeDirection;

/// Monotonic counter for unique `browser-<n>` pop-out window labels.
static BROWSER_WINDOW_SEQ: AtomicU64 = AtomicU64::new(0);

/// Labels of windows that were maximized when they went fullscreen, so leaving
/// fullscreen puts them back. See [`enter_fullscreen`].
static MAXIMIZED_BEFORE_FULLSCREEN: Mutex<Option<HashSet<String>>> = Mutex::new(None);

/// Enter OS-fullscreen, **un-maximizing first**.
///
/// This window is undecorated (`decorations: false`), and on Windows an
/// undecorated maximized window has its client area clipped to the monitor's
/// *work area* so the taskbar stays usable. That clip survives the switch to
/// fullscreen: the window frame grows to the whole monitor and covers the
/// taskbar, while the webview inside it stays work-area tall — which is exactly
/// the taskbar-shaped black band along the bottom of a fullscreen app.
///
/// Dropping the maximized state first means Windows has no work-area clip to
/// apply, so the webview gets the whole monitor. The previous state is
/// remembered per window and re-applied on the way out, so a user who
/// fullscreened a maximized window does not get a small one back.
fn enter_fullscreen(window: &Window) -> Result<(), String> {
    let was_maximized = window.is_maximized().map_err(|e| e.to_string())?;
    if was_maximized {
        window.unmaximize().map_err(|e| e.to_string())?;
        MAXIMIZED_BEFORE_FULLSCREEN
            .lock()
            .map_err(|e| e.to_string())?
            .get_or_insert_with(HashSet::new)
            .insert(window.label().to_string());
    }
    window.set_fullscreen(true).map_err(|e| e.to_string())
}

/// Leave OS-fullscreen, restoring the maximized state [`enter_fullscreen`] took away.
fn leave_fullscreen(window: &Window) -> Result<(), String> {
    window.set_fullscreen(false).map_err(|e| e.to_string())?;
    let restore = MAXIMIZED_BEFORE_FULLSCREEN
        .lock()
        .map_err(|e| e.to_string())?
        .as_mut()
        .is_some_and(|set| set.remove(window.label()));
    if restore {
        window.maximize().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Whether the calling window is currently OS-fullscreen.
#[tauri::command]
pub fn window_is_fullscreen(window: Window) -> Result<bool, String> {
    window.is_fullscreen().map_err(|e| e.to_string())
}

/// Set the calling window's OS-fullscreen state; returns the value applied.
#[tauri::command]
pub fn window_set_fullscreen(window: Window, value: bool) -> Result<bool, String> {
    if value {
        enter_fullscreen(&window)?;
    } else {
        leave_fullscreen(&window)?;
    }
    Ok(value)
}

/// Flip the calling window's OS-fullscreen state; returns the new state.
/// Done in one command so the read/write can't race a concurrent toggle.
#[tauri::command]
pub fn window_toggle_fullscreen(window: Window) -> Result<bool, String> {
    let next = !window.is_fullscreen().map_err(|e| e.to_string())?;
    if next {
        enter_fullscreen(&window)?;
    } else {
        leave_fullscreen(&window)?;
    }
    Ok(next)
}

// --- chrome.workspaceTabs: custom titlebar drives the window itself ---------

/// Minimize the calling window (titlebar minimize button).
#[tauri::command]
pub fn window_minimize(window: Window) -> Result<(), String> {
    window.minimize().map_err(|e| e.to_string())
}

/// Whether the calling window is currently maximized (for the restore icon).
#[tauri::command]
pub fn window_is_maximized(window: Window) -> Result<bool, String> {
    window.is_maximized().map_err(|e| e.to_string())
}

/// Toggle maximize/restore; returns the new maximized state. Backs both the
/// titlebar maximize button and a titlebar double-click.
///
/// **While fullscreen this leaves fullscreen instead of maximizing.** Maximizing
/// a fullscreen undecorated window is the second way into the black band
/// [`enter_fullscreen`] exists to prevent: Windows clips a maximized undecorated
/// window's client area to the monitor *work area*, the fullscreen frame still
/// covers the whole monitor, and the difference paints as a taskbar-shaped strip
/// along the bottom. `enter_fullscreen` un-maximizes on the way in; nothing
/// stopped the user from maximizing again once there.
///
/// Refusing outright would leave a dead button on the titlebar — which is still
/// drawn in fullscreen — so "restore" is what it does, the same thing the
/// gesture means on a maximized window.
#[tauri::command]
pub fn window_toggle_maximize(window: Window) -> Result<bool, String> {
    if window.is_fullscreen().map_err(|e| e.to_string())? {
        leave_fullscreen(&window)?;
        return window.is_maximized().map_err(|e| e.to_string());
    }
    let maximized = window.is_maximized().map_err(|e| e.to_string())?;
    if maximized {
        window.unmaximize().map_err(|e| e.to_string())?;
    } else {
        window.maximize().map_err(|e| e.to_string())?;
    }
    Ok(!maximized)
}

/// Page zoom for the calling window's own webview — the desktop's Ctrl+= / Ctrl+-
/// / Ctrl+0, which WebView2 does not provide by itself here (Tauri turns its zoom
/// hotkeys off by default). Clamped to the range Chromium offers; returns the factor
/// applied. Native browser-pane children are separate webviews and keep their own
/// zoom; the frontend scales their bounds by this factor (apps/web/src/tauriWindow.ts).
#[tauri::command]
pub fn window_set_zoom(webview: Webview, factor: f64) -> Result<f64, String> {
    if !factor.is_finite() {
        return Err("zoom factor must be a number".into());
    }
    let factor = factor.clamp(0.25, 5.0);
    webview.set_zoom(factor).map_err(|e| e.to_string())?;
    Ok(factor)
}

/// Close the calling window (titlebar close button) — exits the app.
#[tauri::command]
pub fn window_close(window: Window) -> Result<(), String> {
    window.close().map_err(|e| e.to_string())
}

/// Begin an OS resize-drag from a window edge/corner. `direction` is one of
/// east/west/north/south/north-east/north-west/south-east/south-west — the
/// undecorated window has no native resize borders, so the frontend supplies
/// invisible edge handles that call this.
#[tauri::command]
pub fn window_start_resize_dragging(window: Window, direction: String) -> Result<(), String> {
    // A fullscreen window has no edges to drag. Letting the gesture through
    // resizes the frame out of fullscreen while Windows keeps the work-area
    // clip on the client, which is the same black band a stray maximize gives —
    // see `window_toggle_maximize`.
    if window.is_fullscreen().map_err(|e| e.to_string())? {
        return Ok(());
    }
    let dir = match direction.as_str() {
        "east" => ResizeDirection::East,
        "west" => ResizeDirection::West,
        "north" => ResizeDirection::North,
        "south" => ResizeDirection::South,
        "north-east" => ResizeDirection::NorthEast,
        "north-west" => ResizeDirection::NorthWest,
        "south-east" => ResizeDirection::SouthEast,
        "south-west" => ResizeDirection::SouthWest,
        other => return Err(format!("unknown resize direction: {other}")),
    };
    window.start_resize_dragging(dir).map_err(|e| e.to_string())
}

// --- browser.nativeWindow: pop an embedded-browser page out to a real window ---

/// Open `url` in the user's **default system browser** — the external-link path
/// (OAuth consent pages, docs links, the sign-in card's fallback link). Distinct
/// from `browser_open_url` below, which opens an app-owned webview window: OAuth
/// must run in the real browser (existing sessions and password managers work,
/// and Google rejects embedded webviews outright — RFC 8252). Only `http`/`https`
/// URLs are accepted, so this can never be steered at local files or custom
/// schemes.
#[tauri::command]
pub fn open_external(url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|e| e.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(format!("refusing to open non-http(s) URL: {url}"));
    }
    open::that_detached(&url).map_err(|e| e.to_string())
}

/// Show a **directory** in the OS file manager — Explorer, Finder, the desktop's
/// file browser. Used by the Storage settings section for the roots reported by
/// `GET /api/paths`.
///
/// **It must be a directory, and that is the security boundary, not a nicety.**
/// `open::that_detached` asks the OS to open a path the way a double-click would,
/// which for an executable, a `.desktop` file or a script means *running* it. A
/// directory has no such interpretation on any of the three platforms. The check
/// is on the canonicalized path so `…/data/../../something.exe` cannot smuggle a
/// file past a textual test, and `canonicalize` also resolves the symlink whose
/// target — not whose name — is what the OS will actually act on.
#[tauri::command]
pub fn open_path(path: String) -> Result<(), String> {
    let resolved = std::fs::canonicalize(&path).map_err(|e| format!("{path}: {e}"))?;
    if !resolved.is_dir() {
        return Err(format!("refusing to open a non-directory path: {path}"));
    }
    open::that_detached(&resolved).map_err(|e| e.to_string())
}

/// Open `url` in a new **decorated** native browser window — the embedded
/// browser's escape hatch for sites that refuse iframing (`X-Frame-Options`/CSP).
/// A real webview window bypasses those headers entirely.
///
/// Only `http`/`https` URLs are accepted, so this can never be steered at
/// `file:`/custom-scheme local resources. Each call gets a fresh `browser-<n>`
/// label (external site, so no app permissions are needed on the new window).
///
/// The command is **async** on purpose: sync commands run on the main thread,
/// where `build()` (which needs the main thread to create the webview) deadlocks
/// the whole app. Async runs it on a worker thread, so `build()` can dispatch to
/// a free main thread and complete.
#[tauri::command]
pub async fn browser_open_url(app: AppHandle, url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|e| e.to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err(format!("refusing to open non-http(s) URL: {url}"));
    }
    let n = BROWSER_WINDOW_SEQ.fetch_add(1, Ordering::Relaxed);
    let label = format!("browser-{n}");
    WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(parsed))
        .title(&url)
        .inner_size(1024.0, 768.0)
        // Same arguments as every other webview: WebView2 shares one user-data
        // folder and refuses a second webview created with different ones.
        .additional_browser_args(crate::media::WEBVIEW2_ARGS)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}
