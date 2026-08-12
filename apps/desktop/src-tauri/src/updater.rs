//! Remote updates: check a signed manifest, download, install.
//!
//! **Why these are custom commands and not the plugin's JS API.** The app has a
//! release *channel* (stable/beta), and the channel is a user setting rather than
//! a build-time fact — the same binary must be able to follow either. Tauri's JS
//! `check()` uses the endpoints baked into `tauri.conf.json` and offers no way to
//! pick one at runtime; only the Rust `UpdaterBuilder` does. So the channel is
//! resolved here, into an endpoint, on every check.
//!
//! **The signature is the whole security model.** The manifest and the archive
//! are fetched over HTTPS from GitHub Releases, but HTTPS only says the bytes came
//! from that host — anyone who can publish a release, or compromise the account,
//! can serve an installer. `pubkey` in `tauri.conf.json` is what makes an update
//! unforgeable without the private key, and the plugin refuses an unsigned or
//! badly-signed archive rather than asking. There is deliberately no "skip
//! verification" path here, not even behind a flag.
//!
//! **What an update must not touch.** Everything under `$HORRIBLE_DATA_DIR` is
//! versioned independently of the app: `llamacpp/bin/<tag>-<variant>/` is keyed by
//! an upstream llama.cpp tag, GGUFs are tens of gigabytes, traces are snapshots
//! tied to a build. None of it is reinstallable in a reasonable time and none of
//! it is invalidated by the app version changing, so the installer is never
//! pointed at that directory. This is a property of where the data lives, not
//! something the updater enforces — which is exactly why it is written down here.

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

/// Where the signed manifests live: a permanent, non-moving release tagged
/// `updater`, holding one manifest per channel.
///
/// Deliberately **not** `/releases/latest/download/`. GitHub resolves "latest" to
/// the newest *non-prerelease*, so a beta manifest published on a prerelease is
/// unreachable there, and a stable manifest would be shadowed the moment a beta
/// was promoted. A fixed tag has no such semantics: CI overwrites the two assets
/// on it after every build, and both channels are always at a URL that means the
/// same thing next month. The manifests point at assets on the *real* release —
/// only the index lives here.
const MANIFEST_BASE: &str =
    "https://github.com/horriblecpp/horrible-dashboard/releases/download/updater";

/// The placeholder a fresh checkout ships with. Replaced by the public half of
/// the key produced by `pnpm tauri signer generate` — see docs/architecture/releases.mdx.
const PUBKEY_PLACEHOLDER: &str = "REPLACE_WITH_TAURI_SIGNING_PUBLIC_KEY";

/// Whether this build has a signing public key configured.
///
/// Read from the config source at compile time rather than from the running
/// app's config, because the answer decides whether the plugin gets registered
/// at all — before there is an `AppHandle` to ask.
pub fn is_configured() -> bool {
    !include_str!("../tauri.conf.json").contains(PUBKEY_PLACEHOLDER)
}

#[derive(Serialize)]
pub struct UpdateInfo {
    /// True when the endpoint answered *and* offered something newer.
    pub available: bool,
    pub current_version: String,
    pub version: Option<String>,
    pub notes: Option<String>,
    pub date: Option<String>,
    /// Which channel was actually checked, echoed back so a UI can never show a
    /// beta build under a "stable" label.
    pub channel: String,
    /// Set when the check could not complete. Distinguished from
    /// `available: false`, which means we asked and there is nothing new.
    pub error: Option<String>,
}

fn manifest_url(channel: &str) -> String {
    let file = if channel == "beta" {
        "beta.json"
    } else {
        "latest.json"
    };
    format!("{MANIFEST_BASE}/{file}")
}

fn normalize(channel: &str) -> String {
    if channel == "beta" {
        "beta".into()
    } else {
        "stable".into()
    }
}

/// Ask the channel's manifest whether anything newer exists.
///
/// Never returns `Err` for a network problem: an offline laptop is not an error
/// the user needs a dialog about, and a caller that cannot tell "no update" from
/// "could not ask" will report the wrong one. Only a malformed configuration
/// produces `Err`.
#[tauri::command]
pub async fn updater_check(app: AppHandle, channel: String) -> Result<UpdateInfo, String> {
    let channel = normalize(&channel);
    let current = app.package_info().version.to_string();
    if !is_configured() {
        return Ok(UpdateInfo {
            available: false,
            current_version: current,
            version: None,
            notes: None,
            date: None,
            channel,
            error: Some(
                "this build has no update-signing public key, so it cannot verify \
                 an update and will not fetch one"
                    .into(),
            ),
        });
    }
    let url = manifest_url(&channel);

    let builder = app
        .updater_builder()
        .endpoints(vec![url.parse().map_err(|e| format!("bad endpoint: {e}"))?])
        .map_err(|e| e.to_string())?;
    let updater = match builder.build() {
        Ok(updater) => updater,
        Err(err) => return Err(err.to_string()),
    };

    match updater.check().await {
        Ok(Some(update)) => Ok(UpdateInfo {
            available: true,
            current_version: current,
            version: Some(update.version.clone()),
            notes: update.body.clone(),
            date: update.date.map(|d| d.to_string()),
            channel,
            error: None,
        }),
        Ok(None) => Ok(UpdateInfo {
            available: false,
            current_version: current,
            version: None,
            notes: None,
            date: None,
            channel,
            error: None,
        }),
        Err(err) => Ok(UpdateInfo {
            available: false,
            current_version: current,
            version: None,
            notes: None,
            date: None,
            channel,
            error: Some(err.to_string()),
        }),
    }
}

/// Download and install the update the same check would find, then restart.
///
/// Re-checks rather than taking a handle from `updater_check`: the two calls are
/// separated by however long the user spent reading the release notes, and an
/// `Update` carried across that gap can point at an asset a re-published release
/// has already replaced.
#[tauri::command]
pub async fn updater_install(app: AppHandle, channel: String) -> Result<bool, String> {
    if !is_configured() {
        return Err("this build has no update-signing public key".into());
    }
    let channel = normalize(&channel);
    let url = manifest_url(&channel);
    let updater = app
        .updater_builder()
        .endpoints(vec![url.parse().map_err(|e| format!("bad endpoint: {e}"))?])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;

    let Some(update) = updater.check().await.map_err(|e| e.to_string())? else {
        return Ok(false);
    };

    // The backend is a child process this shell supervises; the installer
    // replaces the executable underneath us, so the backend is stopped first
    // rather than being orphaned by the restart.
    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|e| e.to_string())?;

    app.state::<std::sync::Arc<crate::backend::BackendSupervisor>>()
        .shutdown();
    app.restart();
}
