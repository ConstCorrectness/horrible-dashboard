//! Backend supervisor: the desktop shell owns the FastAPI backend's lifecycle.
//!
//! On launch a supervisor thread finds the repo checkout, picks a port (reusing
//! an already-running backend on :8000 if there is one), spawns uvicorn bound
//! to `HORRIBLE_DEV_HOST` (127.0.0.1 unless the launcher says otherwise — see
//! `bind_host`), waits for the port to accept connections, and restarts the
//! process with backoff if it dies (e.g. the intermittent MinGW OpenSSL
//! crash). The frontend asks for the resulting origin via the
//! `backend_status` command and uses absolute http/ws URLs under Tauri.
//!
//! Two tiers, in this order:
//!
//! 1. **A repo checkout** — the developer loop. Runs `.venv`'s python (or `uv run`),
//!    with the checkout as the working directory.
//! 2. **A bundled runtime** — what a packaged install ships: a relocatable CPython
//!    with every dependency in its own `site-packages` and the `backend/` tree beside
//!    it, laid down under the app's resource directory by
//!    `scripts/build-backend-runtime.mjs`. This used to be absent entirely, and the
//!    state was `unavailable` with "packaged builds don't bundle the backend yet" —
//!    which made a packaged install an app with no brain.
//!
//! The checkout wins deliberately. A developer running the packaged app from their own
//! tree wants their edits, and a bundled runtime is by definition the code as it was at
//! release time — silently preferring it is how you debug a change that is not running.

use std::collections::VecDeque;
use std::io::BufRead;
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const DEFAULT_PORT: u16 = 8000;
/// How long one spawn gets to start listening before it is recycled.
///
/// Generous because the expensive case is a *first* launch of a packaged build: the
/// bundled runtime is ~50,000 files written moments earlier, and on Windows they are
/// read once by Defender before uvicorn imports anything. The readiness check polls
/// every 250ms and returns the instant the port answers, so a high ceiling costs
/// nothing whenever the backend is quick — it only decides how long a genuinely stuck
/// start is given before being killed and retried.
const READY_TIMEOUT: Duration = Duration::from_secs(90);
const FAST_FAILURE_WINDOW: Duration = Duration::from_secs(10);
const MAX_FAST_FAILURES: u32 = 3;
const STDERR_TAIL_LINES: usize = 50;

/// Name of the bundled runtime inside the app's resource directory. Must match the
/// `--out` default in `scripts/build-backend-runtime.mjs` and the `bundle.resources`
/// entry in `tauri.conf.json` — three spellings of one path, and the only one that
/// reports anything if they drift is this one, as "no backend found".
const BUNDLED_RUNTIME_DIR: &str = "backend-runtime";

#[derive(Clone, serde::Serialize)]
pub struct BackendStatus {
    /// "starting" | "ready" | "failed" | "unavailable"
    pub state: &'static str,
    pub origin: Option<String>,
    pub error: Option<String>,
}

pub struct BackendSupervisor {
    status: Mutex<BackendStatus>,
    child: Mutex<Option<Child>>,
    used_uv_fallback: AtomicBool,
    shutting_down: AtomicBool,
}

impl BackendSupervisor {
    pub fn new() -> Self {
        Self {
            status: Mutex::new(BackendStatus {
                state: "starting",
                origin: None,
                error: None,
            }),
            child: Mutex::new(None),
            used_uv_fallback: AtomicBool::new(false),
            shutting_down: AtomicBool::new(false),
        }
    }

    fn set_status(&self, state: &'static str, origin: Option<String>, error: Option<String>) {
        *self.status.lock().unwrap() = BackendStatus {
            state,
            origin,
            error,
        };
    }

    /// Kill the current child (if any). Idempotent; used on app exit and when
    /// a never-ready process must be recycled.
    pub fn shutdown(&self) {
        self.shutting_down.store(true, Ordering::SeqCst);
        self.kill_child();
    }

    fn kill_child(&self) {
        let Some(mut child) = self.child.lock().unwrap().take() else {
            return;
        };
        // `uv run` wraps the real server, so kill the whole tree on Windows;
        // a plain child.kill() would orphan uvicorn (and its port).
        #[cfg(windows)]
        if self.used_uv_fallback.load(Ordering::SeqCst) {
            let _ = Command::new("taskkill")
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .status();
            let _ = child.wait();
            return;
        }
        let _ = child.kill();
        let _ = child.wait();
    }

    /// True when there is no live child process.
    fn child_exited(&self) -> bool {
        let mut guard = self.child.lock().unwrap();
        match guard.as_mut() {
            Some(child) => matches!(child.try_wait(), Ok(Some(_)) | Err(_)),
            None => true,
        }
    }
}

#[tauri::command]
pub fn backend_status(state: tauri::State<'_, Arc<BackendSupervisor>>) -> BackendStatus {
    state.status.lock().unwrap().clone()
}

/// Spawn the supervisor thread. Returns immediately; poll `backend_status`.
///
/// `resource_dir` is where a packaged build's bundled runtime lives, and it has to be
/// handed in rather than worked out here: the layout differs per platform and per
/// bundle format (beside the exe on Windows, `Contents/Resources` on macOS,
/// `/usr/lib/<app>` for a deb), so Tauri's own resolver is the only thing that gets it
/// right everywhere. `None` in a checkout, where nothing needs it.
pub fn start(supervisor: Arc<BackendSupervisor>, resource_dir: Option<PathBuf>) {
    std::thread::spawn(move || run(supervisor, resource_dir));
}

fn load_env_file(root: &Path) {
    let env_path = root.join(".env");
    if env_path.is_file() {
        if let Ok(file) = std::fs::File::open(env_path) {
            let reader = std::io::BufReader::new(file);
            for line in reader.lines().map_while(Result::ok) {
                let trimmed = line.trim();
                if trimmed.is_empty() || trimmed.starts_with('#') {
                    continue;
                }
                if let Some((key, val)) = trimmed.split_once('=') {
                    let key = key.trim();
                    let val = val.trim();
                    // Strip optional quotes around value
                    let val = if (val.starts_with('"') && val.ends_with('"'))
                        || (val.starts_with('\'') && val.ends_with('\''))
                    {
                        &val[1..val.len() - 1]
                    } else {
                        val
                    };
                    // A real environment variable always wins over the file, so a
                    // launcher that already set one (scripts/dev-desktop.mjs sets
                    // HORRIBLE_DEV_HOST) is never second-guessed. Same rule as
                    // backend/__init__.py's _load_dotenv.
                    if std::env::var_os(key).is_none() {
                        std::env::set_var(key, val);
                    }
                }
            }
        }
    }
}

fn run(sup: Arc<BackendSupervisor>, resource_dir: Option<PathBuf>) {
    let Some(runtime) = Runtime::resolve(resource_dir.as_deref()) else {
        sup.set_status(
            "unavailable",
            None,
            Some(
                "no backend found: this build ships no runtime and is not running from                  a checkout"
                    .to_string(),
            ),
        );
        return;
    };
    let root = runtime.root().to_path_buf();

    // Read from whichever root won. A bundled runtime normally has no `.env` — but one
    // placed beside it is a deliberate act by whoever installed the app, and the file's
    // own rule (a real environment variable always wins) still holds.
    load_env_file(&root);

    let target_port: u16 = std::env::var("HORRIBLE_DEV_BACKEND_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(DEFAULT_PORT);

    // Dev coexistence: an already-running backend (scripts/dev-backend.ps1)
    // wins; don't spawn a duplicate.
    if port_listening(target_port) {
        sup.set_status("ready", Some(origin_for(target_port)), None);
        return;
    }

    let host = bind_host();
    let port = match pick_port(&host, target_port) {
        Ok(port) => port,
        Err(err) => {
            sup.set_status("failed", None, Some(format!("no free port: {err}")));
            return;
        }
    };
    let origin = origin_for(port);
    let stderr_tail: Arc<Mutex<VecDeque<String>>> = Arc::new(Mutex::new(VecDeque::new()));
    let mut fast_failures: u32 = 0;

    loop {
        if sup.shutting_down.load(Ordering::SeqCst) {
            return;
        }
        sup.set_status("starting", None, None);

        let child = match spawn_backend(&runtime, &host, port, &sup.used_uv_fallback, &stderr_tail)
        {
            Ok(child) => child,
            Err(err) => {
                sup.set_status(
                    "failed",
                    None,
                    Some(format!("failed to spawn backend: {err}")),
                );
                return;
            }
        };
        *sup.child.lock().unwrap() = Some(child);
        let started = Instant::now();

        // Readiness: wait for the port to accept connections.
        let mut ready = false;
        while started.elapsed() < READY_TIMEOUT {
            if sup.shutting_down.load(Ordering::SeqCst) {
                return;
            }
            if sup.child_exited() {
                break;
            }
            if port_listening(port) {
                ready = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(250));
        }

        if ready {
            sup.set_status("ready", Some(origin.clone()), None);
            // Supervise until the process exits or the app shuts down.
            loop {
                if sup.shutting_down.load(Ordering::SeqCst) {
                    return;
                }
                if sup.child_exited() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(500));
            }
        }

        if sup.shutting_down.load(Ordering::SeqCst) {
            return;
        }
        // Recycle whatever is left (a never-ready process is still running).
        sup.kill_child();

        if started.elapsed() < FAST_FAILURE_WINDOW {
            fast_failures += 1;
        } else {
            fast_failures = 1;
        }
        if fast_failures >= MAX_FAST_FAILURES {
            let tail = stderr_tail
                .lock()
                .unwrap()
                .iter()
                .cloned()
                .collect::<Vec<_>>()
                .join("\n");
            let error = if tail.is_empty() {
                "backend exited repeatedly".to_string()
            } else {
                tail
            };
            sup.set_status("failed", None, Some(error));
            return;
        }

        // 1s / 2s / 4s; respawn on the SAME port so the frontend's origin stays valid.
        let backoff = Duration::from_secs(1 << (fast_failures - 1).min(2));
        std::thread::sleep(backoff);
    }
}

/// Where the backend's code and interpreter come from.
///
/// Two shapes with the same job, kept as one enum rather than two code paths because
/// everything downstream — the port probe, the readiness wait, the restart backoff, the
/// stderr tail — is identical and only the argv differs.
enum Runtime {
    /// A repo checkout: `.venv`'s python if it has been synced, else `uv run`.
    Checkout(PathBuf),
    /// A bundled runtime laid down by `scripts/build-backend-runtime.mjs`: the root
    /// holding `backend/`, plus the interpreter inside it.
    Bundled { root: PathBuf, python: PathBuf },
}

impl Runtime {
    /// The checkout first, then the bundle. See the ordering note in the module docs.
    fn resolve(resource_dir: Option<&Path>) -> Option<Self> {
        if let Some(root) = find_repo_root() {
            return Some(Runtime::Checkout(root));
        }
        resource_dir.and_then(Self::bundled_in)
    }

    /// The bundled runtime under a resource directory, if it is actually complete.
    ///
    /// Both halves are checked, not just the folder: an interrupted install, or a
    /// bundler that dropped part of a 50,000-file tree, leaves a `backend-runtime`
    /// directory that exists and cannot run. Failing here reports "no backend found",
    /// which is true; spawning it would report a Python traceback about a missing
    /// module, which sends the reader looking in entirely the wrong place.
    fn bundled_in(resource_dir: &Path) -> Option<Self> {
        let root = resource_dir.join(BUNDLED_RUNTIME_DIR);
        if !root.join("backend").join("app.py").is_file() {
            return None;
        }
        let python = if cfg!(windows) {
            root.join("python").join("python.exe")
        } else {
            root.join("python").join("bin").join("python3")
        };
        python.is_file().then_some(Runtime::Bundled { root, python })
    }

    /// The working directory the backend runs in — the directory holding `backend/`.
    ///
    /// Load-bearing rather than cosmetic: `uvicorn backend.app:app` imports by name, so
    /// this is what puts `backend` on the import path, and module code navigates
    /// outward from it (`Path(__file__).resolve().parents[3]`).
    fn root(&self) -> &Path {
        match self {
            Runtime::Checkout(root) => root,
            Runtime::Bundled { root, .. } => root,
        }
    }
}

/// Walk up from cwd and the executable looking for the repo checkout.
fn find_repo_root() -> Option<PathBuf> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    for start in starts {
        let mut dir: Option<&Path> = Some(start.as_path());
        while let Some(candidate) = dir {
            if candidate.join("pyproject.toml").is_file() && candidate.join("backend").is_dir() {
                return Some(candidate.to_path_buf());
            }
            dir = candidate.parent();
        }
    }
    None
}

/// The interface uvicorn binds to. `scripts/dev-desktop.mjs` (the `pnpm dev:desktop`
/// script) sets `HORRIBLE_DEV_HOST=0.0.0.0` so the desktop node is reachable from the
/// LAN — the Android companion, remote control and cross-node hassault matches all
/// need that. The default stays loopback, so any launcher that does NOT ask for it
/// (including a future packaged build) keeps the backend local-only.
fn bind_host() -> String {
    match std::env::var("HORRIBLE_DEV_HOST") {
        Ok(host) if !host.trim().is_empty() => host.trim().to_string(),
        _ => "127.0.0.1".to_string(),
    }
}

/// Always loopback, whatever the bind host: this is the origin the *webview* uses,
/// and it runs on this machine. A server bound to 0.0.0.0 answers on it too.
fn origin_for(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn port_listening(port: u16) -> bool {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// Prefer the configured default port (browser tabs / curl keep working); else ephemeral.
///
/// The probe binds the SAME host uvicorn will. Testing loopback while uvicorn takes
/// 0.0.0.0 is not equivalent — a port held by another process on a different
/// interface would pass the probe and then fail the real bind.
fn pick_port(host: &str, target_port: u16) -> std::io::Result<u16> {
    if let Ok(listener) = TcpListener::bind((host, target_port)) {
        drop(listener);
        return Ok(target_port);
    }
    let listener = TcpListener::bind((host, 0))?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

/// PATH without MinGW bin dirs: MSYS2/Git's libcrypto lacks the MSVC applink
/// shim and aborts uvicorn with "no OPENSSL_Applink" (same fix as
/// scripts/dev-backend.ps1).
fn sanitized_path() -> Option<String> {
    let path = std::env::var("PATH").ok()?;
    let sep = if cfg!(windows) { ';' } else { ':' };
    let cleaned: Vec<&str> = path
        .split(sep)
        .filter(|entry| {
            let lower = entry.to_ascii_lowercase();
            !lower.contains("mingw64\\bin") && !lower.contains("mingw64/bin")
        })
        .collect();
    Some(cleaned.join(&sep.to_string()))
}

fn spawn_backend(
    runtime: &Runtime,
    host: &str,
    port: u16,
    used_uv_fallback: &AtomicBool,
    stderr_tail: &Arc<Mutex<VecDeque<String>>>,
) -> std::io::Result<Child> {
    let root = runtime.root();

    let mut cmd = match runtime {
        // The bundled interpreter is the child itself — no `uv`, no wrapper, and
        // nothing on the machine's PATH is consulted. That is the point of shipping it:
        // a packaged install must not depend on the user having Python, uv, or a
        // matching version of either.
        Runtime::Bundled { python, .. } => {
            let mut cmd = Command::new(python);
            cmd.args(["-m", "uvicorn"]);
            cmd
        }
        Runtime::Checkout(root) => {
            let venv_python = if cfg!(windows) {
                root.join(".venv").join("Scripts").join("python.exe")
            } else {
                root.join(".venv").join("bin").join("python")
            };
            // Prefer the venv python so the child IS the server (clean kill); `uv run`
            // wraps it in another process and needs tree-kill on Windows.
            if venv_python.is_file() {
                let mut cmd = Command::new(venv_python);
                cmd.args(["-m", "uvicorn"]);
                cmd
            } else {
                used_uv_fallback.store(true, Ordering::SeqCst);
                let mut cmd = Command::new("uv");
                cmd.args(["run", "uvicorn"]);
                cmd
            }
        }
    };
    cmd.args([
        "backend.app:app",
        "--host",
        host,
        "--port",
        &port.to_string(),
    ])
    .current_dir(root)
    .stdout(Stdio::null())
    .stderr(Stdio::piped());
    if let Some(path) = sanitized_path() {
        cmd.env("PATH", path);
    }
    // A set SSLKEYLOGFILE (e.g. for Wireshark) makes CPython's ssl module use
    // OpenSSL's FILE*-based keylog API, which aborts python.exe with
    // "no OPENSSL_Applink" on the first SSL context init. The backend never
    // needs keylogging — drop it from the child env.
    cmd.env_remove("SSLKEYLOGFILE");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd.spawn()?;
    if let Some(stderr) = child.stderr.take() {
        let tail = Arc::clone(stderr_tail);
        std::thread::spawn(move || {
            let reader = std::io::BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let mut tail = tail.lock().unwrap();
                if tail.len() >= STDERR_TAIL_LINES {
                    tail.pop_front();
                }
                tail.push_back(line);
            }
        });
    }
    Ok(child)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a directory that looks like a finished bundled runtime.
    fn lay_down_runtime(resource_dir: &Path, with_backend: bool, with_python: bool) {
        let root = resource_dir.join(BUNDLED_RUNTIME_DIR);
        if with_backend {
            std::fs::create_dir_all(root.join("backend")).unwrap();
            std::fs::write(root.join("backend").join("app.py"), "").unwrap();
        }
        if with_python {
            let (dir, name) = if cfg!(windows) {
                (root.join("python"), "python.exe")
            } else {
                (root.join("python").join("bin"), "python3")
            };
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(dir.join(name), "").unwrap();
        }
        std::fs::create_dir_all(&root).unwrap();
    }

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("hd-backend-test-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_complete_runtime_resolves() {
        let dir = scratch("complete");
        lay_down_runtime(&dir, true, true);
        let runtime = Runtime::bundled_in(&dir).expect("a complete runtime should resolve");
        assert_eq!(runtime.root(), dir.join(BUNDLED_RUNTIME_DIR));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The half-shipped cases, which are the ones worth a test.
    ///
    /// A `backend-runtime` directory that exists and cannot run is what an interrupted
    /// install or a bundler that dropped part of a 50,000-file tree leaves behind.
    /// Reporting "no backend found" points at the install; spawning it would surface a
    /// Python `ModuleNotFoundError`, which sends the reader hunting through app code
    /// for a bug that is not there.
    #[test]
    fn an_incomplete_runtime_does_not_resolve() {
        let empty = scratch("empty");
        assert!(Runtime::bundled_in(&empty).is_none(), "no runtime directory at all");

        let no_python = scratch("no-python");
        lay_down_runtime(&no_python, true, false);
        assert!(
            Runtime::bundled_in(&no_python).is_none(),
            "source without an interpreter cannot run"
        );

        let no_source = scratch("no-source");
        lay_down_runtime(&no_source, false, true);
        assert!(
            Runtime::bundled_in(&no_source).is_none(),
            "an interpreter with nothing to serve is not a backend"
        );

        for dir in [empty, no_python, no_source] {
            let _ = std::fs::remove_dir_all(&dir);
        }
    }

    /// A checkout beats a bundle. See the ordering note in the module docs: silently
    /// preferring the shipped copy is how a developer debugs a change that never ran.
    #[test]
    fn the_checkout_wins_over_a_bundled_runtime() {
        let dir = scratch("both");
        lay_down_runtime(&dir, true, true);
        // `resolve` consults the real `find_repo_root`, and these tests run from inside
        // the checkout — so if a repo is found it must be the one that wins.
        match Runtime::resolve(Some(&dir)) {
            Some(Runtime::Checkout(root)) => {
                assert!(root.join("backend").is_dir());
                assert_ne!(root, dir.join(BUNDLED_RUNTIME_DIR));
            }
            // Running outside a checkout is a legitimate environment; the bundle is
            // then the only answer, and that is the other half of the contract.
            Some(Runtime::Bundled { root, .. }) => assert_eq!(root, dir.join(BUNDLED_RUNTIME_DIR)),
            None => panic!("a complete bundle was laid down; something must resolve"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_runtime_anywhere_is_none() {
        // `None` resource dir and no checkout is the packaged-build-without-a-runtime
        // case; from inside the checkout the first tier answers, so this only asserts
        // that a missing resource dir is not itself an error.
        let resolved = Runtime::resolve(None);
        assert!(matches!(resolved, Some(Runtime::Checkout(_)) | None));
    }
}
