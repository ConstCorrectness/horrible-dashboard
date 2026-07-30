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
//! Dev-spawn tier only: without a repo checkout (a packaged build) the state
//! is `unavailable` — the hook for a future bundled/downloaded runtime.

use std::collections::VecDeque;
use std::io::BufRead;
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

const DEFAULT_PORT: u16 = 8000;
const READY_TIMEOUT: Duration = Duration::from_secs(30);
const FAST_FAILURE_WINDOW: Duration = Duration::from_secs(10);
const MAX_FAST_FAILURES: u32 = 3;
const STDERR_TAIL_LINES: usize = 50;

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
pub fn start(supervisor: Arc<BackendSupervisor>) {
    std::thread::spawn(move || run(supervisor));
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

fn run(sup: Arc<BackendSupervisor>) {
    let Some(root) = find_repo_root() else {
        sup.set_status(
            "unavailable",
            None,
            Some(
                "repo checkout not found — packaged builds don't bundle the backend yet"
                    .to_string(),
            ),
        );
        return;
    };

    // Load .env file if it exists at the repo root so we get configured ports/vars
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

        let child = match spawn_backend(&root, &host, port, &sup.used_uv_fallback, &stderr_tail) {
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
    root: &Path,
    host: &str,
    port: u16,
    used_uv_fallback: &AtomicBool,
    stderr_tail: &Arc<Mutex<VecDeque<String>>>,
) -> std::io::Result<Child> {
    let venv_python = if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    };

    // Prefer the venv python so the child IS the server (clean kill); `uv run`
    // wraps it in another process and needs tree-kill on Windows.
    let mut cmd = if venv_python.is_file() {
        let mut cmd = Command::new(venv_python);
        cmd.args(["-m", "uvicorn"]);
        cmd
    } else {
        used_uv_fallback.store(true, Ordering::SeqCst);
        let mut cmd = Command::new("uv");
        cmd.args(["run", "uvicorn"]);
        cmd
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
