//! `browser.nativeCdp` — the agent's hands inside the **native** browser pane.
//!
//! The native child webview (see webview.rs) is the browser a human wants: real
//! compositing, real input, real clipboard, a real Edge fingerprint. Until this
//! module it was also opaque — the agent's tools could only drive the backend's
//! headless Chromium, so the pane defaulted to streaming that instead and the human
//! got JPEG frames. This bridge lets the agent drive the surface the human sees.
//!
//! ## Why in-process CDP, and not `--remote-debugging-port`
//!
//! WebView2 exposes the Chrome DevTools Protocol two ways. A debug port is the
//! familiar one, and the wrong one here: it is a local TCP listener that hands
//! **every** webview in the process — including the app's own UI, its origin and the
//! tokens it holds — to any program on the machine that connects. The in-process
//! API (`ICoreWebView2::CallDevToolsProtocolMethod` plus
//! `GetDevToolsProtocolEventReceiver`) reaches exactly one webview and nothing
//! outside this process can call it.
//!
//! ## What the frontend may ask for
//!
//! The frontend is trusted, but a compromised frontend should not inherit the whole
//! protocol, so methods are allowlisted (see [`method_allowed`]): page, DOM, input,
//! script and read-only network — enough for snapshot/click/type/read and the
//! network strip. `Browser.*` (the whole browser process), `Target.*` (attach to
//! other targets, including the app), `Storage.*`, `Security.*`, `Fetch.*`
//! (request interception) and `Emulation.*` (fingerprint changes that read as
//! automation to bot walls) stay out.
//!
//! ## Threading
//!
//! WebView2 only honours these calls on the webview's own UI thread, and only
//! delivers the completion there. `Webview::with_webview` dispatches to that thread;
//! the completion handler sends the reply back over a channel, which the async
//! command awaits on a blocking-pool thread so the main thread is never parked.
//!
//! Windows-only: WKWebView (macOS) and WebKitGTK have no CDP, so the capability is
//! only granted on Windows and these commands say so plainly elsewhere.

use std::collections::HashSet;
use std::sync::Mutex;

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, State};

use crate::webview::BrowserWebviews;

/// The Tauri event every forwarded CDP event is emitted under.
pub const CDP_EVENT: &str = "browser-cdp";

/// How long a CDP call may take before the command gives up. `Page.navigate` returns
/// once the navigation *commits*, not when the page loads, so this is generous.
const CALL_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// One forwarded CDP event, tagged with the pane/tab id that owns the webview.
#[derive(Serialize, Clone)]
pub struct CdpEvent {
    pub id: String,
    pub method: String,
    pub params: Value,
}

/// `(webview id, event name)` pairs already subscribed. WebView2 happily registers
/// the same handler twice, which would deliver every event twice — and a React
/// StrictMode remount subscribes twice by construction.
#[derive(Default)]
pub struct CdpSubscriptions(Mutex<HashSet<(String, String)>>);

impl CdpSubscriptions {
    /// Drop every subscription for webview `id` (it was closed; a re-created webview
    /// with the same id starts with no receivers and must be allowed to re-register).
    pub fn forget(&self, id: &str) {
        if let Ok(mut set) = self.0.lock() {
            set.retain(|(owner, _)| owner != id);
        }
    }

    pub fn forget_all(&self) {
        if let Ok(mut set) = self.0.lock() {
            set.clear();
        }
    }
}

/// Domains callable wholesale, plus single methods from domains that are otherwise
/// off-limits. See the module docs for what is excluded and why.
fn method_allowed(method: &str) -> bool {
    const DOMAINS: &[&str] = &["Page", "Runtime", "DOM", "Input", "Accessibility"];
    const METHODS: &[&str] = &[
        "Network.enable",
        "Network.disable",
        "Network.getResponseBody",
    ];
    match method.split_once('.') {
        Some((domain, name)) if !name.is_empty() => {
            DOMAINS.contains(&domain) || METHODS.contains(&method)
        }
        _ => false,
    }
}

/// Events are read-only, so the bar is lower than for methods: anything from the
/// domains the pane observes (navigation, console, DOM changes, network traffic).
fn event_allowed(event: &str) -> bool {
    const DOMAINS: &[&str] = &["Page", "Runtime", "DOM", "Network"];
    match event.split_once('.') {
        Some((domain, name)) if !name.is_empty() => DOMAINS.contains(&domain),
        _ => false,
    }
}

/// `Page.navigate` gets the same scheme guard as `navigate_browser_webview`: CDP
/// would otherwise happily point the pane at `file:` or `javascript:`.
fn check_params(method: &str, params: &Value) -> Result<(), String> {
    if method == "Page.navigate" {
        let url = params.get("url").and_then(Value::as_str).unwrap_or("");
        crate::webview::parse_web_url(url)?;
    }
    Ok(())
}

/// Run one CDP method on the native webview `id`; resolves with its result object.
#[tauri::command]
pub async fn cdp_browser_webview(
    state: State<'_, BrowserWebviews>,
    id: String,
    method: String,
    params: Option<Value>,
) -> Result<Value, String> {
    if !method_allowed(&method) {
        return Err(format!("CDP method `{method}` is not allowed"));
    }
    let params = params.unwrap_or_else(|| Value::Object(Default::default()));
    check_params(&method, &params)?;
    let webview = state.get(&id)?;
    let rx = imp::call(&webview, method.clone(), params.to_string())?;
    let reply = tauri::async_runtime::spawn_blocking(move || rx.recv_timeout(CALL_TIMEOUT))
        .await
        .map_err(|e| e.to_string())?
        .map_err(|_| format!("CDP `{method}` timed out"))??;
    serde_json::from_str(&reply).map_err(|e| format!("CDP `{method}` returned bad JSON: {e}"))
}

/// Forward CDP event `event` from webview `id` as [`CDP_EVENT`]. Idempotent.
///
/// Only the *receiver* is registered here; the domain still has to be enabled
/// (`Page.enable`, `Network.enable`) through [`cdp_browser_webview`] before
/// Chromium emits anything.
#[tauri::command]
pub async fn subscribe_browser_webview_cdp(
    app: AppHandle,
    state: State<'_, BrowserWebviews>,
    subscriptions: State<'_, CdpSubscriptions>,
    id: String,
    event: String,
) -> Result<(), String> {
    if !event_allowed(&event) {
        return Err(format!("CDP event `{event}` is not allowed"));
    }
    let webview = state.get(&id)?;
    {
        let mut set = subscriptions
            .0
            .lock()
            .map_err(|_| "CDP subscription registry poisoned".to_string())?;
        if !set.insert((id.clone(), event.clone())) {
            return Ok(());
        }
    }
    imp::subscribe(&webview, app, id, event)
}

/// Open the DevTools window for webview `id` (F12 in the pane).
#[tauri::command]
pub async fn open_browser_webview_devtools(
    state: State<'_, BrowserWebviews>,
    id: String,
) -> Result<(), String> {
    let webview = state.get(&id)?;
    imp::open_devtools(&webview)
}

/// Settings a browser (as opposed to an app surface) wants: devtools reachable from
/// the pane in release builds too, and the password manager and form autofill on.
/// Applied once, right after the webview is created.
pub fn configure_browser_settings(webview: &tauri::Webview) {
    imp::configure(webview);
}

#[cfg(windows)]
mod imp {
    use std::sync::mpsc::{channel, Receiver};

    use tauri::{AppHandle, Emitter, Webview};
    use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Settings4;
    use webview2_com::{
        take_pwstr, CallDevToolsProtocolMethodCompletedHandler,
        DevToolsProtocolEventReceivedEventHandler,
    };
    use windows::core::{Interface, HSTRING, PWSTR};

    use super::{CdpEvent, CDP_EVENT};

    pub type Reply = Result<String, String>;

    pub fn call(
        webview: &Webview,
        method: String,
        params: String,
    ) -> Result<Receiver<Reply>, String> {
        let (tx, rx) = channel::<Reply>();
        webview
            .with_webview(move |platform| {
                let failed = tx.clone();
                let result = (|| -> windows::core::Result<()> {
                    let core = unsafe { platform.controller().CoreWebView2()? };
                    let handler = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
                        move |status, json| {
                            // A failed method still returns its CDP error object as
                            // JSON — that is the useful message, not the HRESULT.
                            let reply = match status {
                                Ok(()) => Ok(json),
                                Err(e) if json.is_empty() => Err(e.message()),
                                Err(_) => Err(json),
                            };
                            let _ = tx.send(reply);
                            Ok(())
                        },
                    ));
                    unsafe {
                        core.CallDevToolsProtocolMethod(
                            &HSTRING::from(method.as_str()),
                            &HSTRING::from(params.as_str()),
                            &handler,
                        )
                    }
                })();
                if let Err(e) = result {
                    let _ = failed.send(Err(e.message()));
                }
            })
            .map_err(|e| e.to_string())?;
        Ok(rx)
    }

    pub fn subscribe(
        webview: &Webview,
        app: AppHandle,
        id: String,
        event: String,
    ) -> Result<(), String> {
        webview
            .with_webview(move |platform| {
                let result = (|| -> windows::core::Result<()> {
                    let core = unsafe { platform.controller().CoreWebView2()? };
                    let receiver = unsafe {
                        core.GetDevToolsProtocolEventReceiver(&HSTRING::from(event.as_str()))?
                    };
                    let method = event.clone();
                    let handler = DevToolsProtocolEventReceivedEventHandler::create(Box::new(
                        move |_, args| {
                            let Some(args) = args else { return Ok(()) };
                            let mut raw = PWSTR::null();
                            unsafe { args.ParameterObjectAsJson(&mut raw)? };
                            let params = serde_json::from_str(&take_pwstr(raw))
                                .unwrap_or(serde_json::Value::Null);
                            let _ = app.emit(
                                CDP_EVENT,
                                CdpEvent {
                                    id: id.clone(),
                                    method: method.clone(),
                                    params,
                                },
                            );
                            Ok(())
                        },
                    ));
                    let mut token = 0i64;
                    unsafe { receiver.add_DevToolsProtocolEventReceived(&handler, &mut token) }
                })();
                if let Err(e) = result {
                    eprintln!("[browser-cdp] subscribe {event} failed: {}", e.message());
                }
            })
            .map_err(|e| e.to_string())
    }

    pub fn open_devtools(webview: &Webview) -> Result<(), String> {
        webview
            .with_webview(|platform| {
                let _ = (|| -> windows::core::Result<()> {
                    let core = unsafe { platform.controller().CoreWebView2()? };
                    unsafe { core.OpenDevToolsWindow() }
                })();
            })
            .map_err(|e| e.to_string())
    }

    pub fn configure(webview: &Webview) {
        let _ = webview.with_webview(|platform| {
            let result = (|| -> windows::core::Result<()> {
                let core = unsafe { platform.controller().CoreWebView2()? };
                let settings = unsafe { core.Settings()? };
                unsafe { settings.SetAreDevToolsEnabled(true)? };
                if let Ok(settings4) = settings.cast::<ICoreWebView2Settings4>() {
                    unsafe {
                        settings4.SetIsPasswordAutosaveEnabled(true)?;
                        settings4.SetIsGeneralAutofillEnabled(true)?;
                    }
                }
                Ok(())
            })();
            if let Err(e) = result {
                eprintln!(
                    "[browser-cdp] configuring browser settings failed: {}",
                    e.message()
                );
            }
        });
    }
}

#[cfg(not(windows))]
mod imp {
    use std::sync::mpsc::Receiver;

    use tauri::{AppHandle, Webview};

    const UNSUPPORTED: &str = "the native browser's CDP bridge is only available on Windows";

    pub fn call(
        _: &Webview,
        _: String,
        _: String,
    ) -> Result<Receiver<Result<String, String>>, String> {
        Err(UNSUPPORTED.into())
    }

    pub fn subscribe(_: &Webview, _: AppHandle, _: String, _: String) -> Result<(), String> {
        Err(UNSUPPORTED.into())
    }

    pub fn open_devtools(webview: &Webview) -> Result<(), String> {
        // WKWebView/WebKitGTK inspectors are reachable through Tauri itself, but
        // `Webview::open_devtools` only exists in debug builds or with Tauri's
        // `devtools` feature, which this crate does not enable. Calling it
        // unconditionally broke every macOS and Linux release build of v0.4.0.
        #[cfg(debug_assertions)]
        {
            webview.open_devtools();
            Ok(())
        }
        #[cfg(not(debug_assertions))]
        {
            let _ = webview;
            Err("the browser inspector is only available in debug builds on this platform".into())
        }
    }

    pub fn configure(_: &Webview) {}
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_dom_input_and_script_domains_are_callable() {
        for m in [
            "Page.navigate",
            "Page.captureScreenshot",
            "Runtime.evaluate",
            "DOM.getDocument",
            "Input.dispatchMouseEvent",
            "Input.insertText",
            "Accessibility.getFullAXTree",
            "Network.enable",
        ] {
            assert!(method_allowed(m), "{m} should be allowed");
        }
    }

    #[test]
    fn process_wide_and_fingerprint_domains_are_refused() {
        for m in [
            "Browser.close",
            "Target.attachToTarget",
            "Target.getTargets",
            "Storage.clearDataForOrigin",
            "Fetch.enable",
            "Security.setIgnoreCertificateErrors",
            "Emulation.setUserAgentOverride",
            "Network.setCookie",
            "Network.getAllCookies",
            "SystemInfo.getInfo",
            "Page",
            "Page.",
            "",
        ] {
            assert!(!method_allowed(m), "{m} should be refused");
        }
    }

    #[test]
    fn network_events_are_observable_but_network_writes_are_not_callable() {
        assert!(event_allowed("Network.requestWillBeSent"));
        assert!(event_allowed("Page.loadEventFired"));
        assert!(!method_allowed("Network.requestWillBeSent"));
        assert!(!event_allowed("Target.attachedToTarget"));
        assert!(!event_allowed("Network"));
    }

    #[test]
    fn navigate_keeps_the_scheme_guard() {
        let ok = serde_json::json!({ "url": "https://example.com" });
        assert!(check_params("Page.navigate", &ok).is_ok());
        for bad in [
            "file:///C:/Windows/win.ini",
            "javascript:alert(1)",
            "tauri://localhost",
            "",
        ] {
            let params = serde_json::json!({ "url": bad });
            assert!(
                check_params("Page.navigate", &params).is_err(),
                "{bad} should be refused"
            );
        }
        // Other methods carry no URL to check.
        assert!(check_params("Runtime.evaluate", &serde_json::json!({})).is_ok());
    }

    #[test]
    fn subscriptions_forget_by_owner() {
        let subs = CdpSubscriptions::default();
        {
            let mut set = subs.0.lock().unwrap();
            set.insert(("a".into(), "Page.loadEventFired".into()));
            set.insert(("b".into(), "Page.loadEventFired".into()));
        }
        subs.forget("a");
        let set = subs.0.lock().unwrap();
        assert_eq!(set.len(), 1);
        assert!(set.contains(&("b".to_string(), "Page.loadEventFired".to_string())));
    }
}
