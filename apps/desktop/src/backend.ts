/**
 * Backend supervisor: the desktop shell owns the FastAPI backend's lifecycle.
 *
 * On launch the supervisor finds the repo checkout, picks a port (reusing an
 * already-running backend on :8000 if there is one *and it is ours*), spawns uvicorn
 * bound to `HORRIBLE_DEV_HOST` (127.0.0.1 unless the launcher says otherwise — see
 * `bindHost`), waits for the port to accept connections, and restarts the process
 * with backoff if it dies (e.g. the intermittent MinGW OpenSSL crash). The frontend
 * asks for the resulting origin via the `backend_status` command and uses absolute
 * http/ws URLs under the desktop shell.
 *
 * Two tiers, in this order:
 *
 * 1. **A repo checkout** — the developer loop. Runs `.venv`'s python (or `uv run`),
 *    with the checkout as the working directory.
 * 2. **A bundled runtime** — what a packaged install ships: a relocatable CPython
 *    with every dependency in its own `site-packages` and the `backend/` tree beside
 *    it, laid down in `apps/desktop/backend-runtime` by
 *    `scripts/build-backend-runtime.mjs` and shipped as an `extraResources` entry.
 *
 * The checkout wins deliberately. A developer running the packaged app from their own
 * tree wants their edits, and a bundled runtime is by definition the code as it was at
 * release time — silently preferring it is how you debug a change that is not running.
 *
 * This module imports nothing from `electron`, so its logic is unit-testable in plain
 * Node (test/backend.test.ts); main.ts hands it the resource directory.
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import * as fs from 'node:fs';
import * as net from 'node:net';
import * as path from 'node:path';

export interface BackendStatus {
  state: 'starting' | 'ready' | 'failed' | 'unavailable';
  origin: string | null;
  error: string | null;
}

const DEFAULT_PORT = 8000;
/**
 * How long one spawn gets to start listening before it is recycled.
 *
 * Generous because the expensive case is a *first* launch of a packaged build: the
 * bundled runtime is ~50,000 files written moments earlier, and on Windows they are
 * read once by Defender before uvicorn imports anything. The readiness check polls
 * every 250ms and returns the instant the port answers, so a high ceiling costs
 * nothing whenever the backend is quick.
 */
const READY_TIMEOUT_MS = 90_000;
const FAST_FAILURE_WINDOW_MS = 10_000;
const MAX_FAST_FAILURES = 3;
const STDERR_TAIL_LINES = 50;
/** How long a SIGTERM'd backend gets to run its lifespan shutdown before SIGKILL. */
const GRACEFUL_STOP_MS = 3_000;

/**
 * Name of the bundled runtime inside the app's resource directory. Must match the
 * `--out` default in `scripts/build-backend-runtime.mjs` and the `extraResources`
 * entry in `electron-builder.json` — three spellings of one path, and the only one
 * that reports anything if they drift is this one, as "no backend found".
 */
export const BUNDLED_RUNTIME_DIR = 'backend-runtime';

const isWindows = process.platform === 'win32';
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Where the backend's code and interpreter come from. */
export type Runtime =
  /** A repo checkout: `.venv`'s python if it has been synced, else `uv run`. */
  | { kind: 'checkout'; root: string }
  /** A bundled runtime: the root holding `backend/`, plus the interpreter inside it. */
  | { kind: 'bundled'; root: string; python: string };

/** Walk up from each start looking for the repo checkout (pyproject.toml + backend/). */
export function findRepoRoot(starts: string[]): string | null {
  for (const start of starts) {
    let dir = path.resolve(start);
    for (;;) {
      if (fs.existsSync(path.join(dir, 'pyproject.toml')) && isDir(path.join(dir, 'backend'))) {
        return dir;
      }
      const parent = path.dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  return null;
}

/**
 * The bundled runtime under a resource directory, if it is actually complete.
 *
 * Both halves are checked, not just the folder: an interrupted install, or a bundler
 * that dropped part of a 50,000-file tree, leaves a `backend-runtime` directory that
 * exists and cannot run. Failing here reports "no backend found", which is true;
 * spawning it would report a Python traceback about a missing module, which sends the
 * reader looking in entirely the wrong place.
 */
export function bundledRuntimeIn(resourceDir: string): Runtime | null {
  const root = path.join(resourceDir, BUNDLED_RUNTIME_DIR);
  if (!isFile(path.join(root, 'backend', 'app.py'))) return null;
  const python = isWindows
    ? path.join(root, 'python', 'python.exe')
    : path.join(root, 'python', 'bin', 'python3');
  return isFile(python) ? { kind: 'bundled', root, python } : null;
}

/** The checkout first, then the bundle. See the ordering note in the module docs. */
export function resolveRuntime(starts: string[], resourceDir: string | null): Runtime | null {
  const root = findRepoRoot(starts);
  if (root) return { kind: 'checkout', root };
  return resourceDir ? bundledRuntimeIn(resourceDir) : null;
}

/**
 * Parse a `.env` file's `KEY=value` lines (optional surrounding quotes stripped).
 * Same rule as `backend/__init__.py`'s `_load_dotenv`.
 */
export function parseEnvFile(text: string): [string, string][] {
  const out: [string, string][] = [];
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    if (
      val.length >= 2 &&
      ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'")))
    ) {
      val = val.slice(1, -1);
    }
    out.push([key, val]);
  }
  return out;
}

/**
 * Load `<root>/.env` into this process's environment. A real environment variable
 * always wins over the file, so a launcher that already set one
 * (scripts/dev-desktop.mjs sets HORRIBLE_DEV_HOST) is never second-guessed.
 */
function loadEnvFile(root: string): void {
  const file = path.join(root, '.env');
  if (!isFile(file)) return;
  try {
    for (const [key, val] of parseEnvFile(fs.readFileSync(file, 'utf8'))) {
      if (process.env[key] === undefined) process.env[key] = val;
    }
  } catch {
    // An unreadable .env is the same as none.
  }
}

/**
 * The interface uvicorn binds to. `scripts/dev-desktop.mjs` (the `pnpm dev:desktop`
 * script) sets `HORRIBLE_DEV_HOST=0.0.0.0` so the desktop node is reachable from the
 * LAN — the Android companion, remote control and cross-node hassault matches all
 * need that. The default stays loopback, so any launcher that does NOT ask for it
 * (including a packaged build) keeps the backend local-only.
 */
export function bindHost(env: NodeJS.ProcessEnv = process.env): string {
  const host = env.HORRIBLE_DEV_HOST?.trim();
  return host ? host : '127.0.0.1';
}

/**
 * Always loopback, whatever the bind host: this is the origin the *webview* uses,
 * and it runs on this machine. A server bound to 0.0.0.0 answers on it too.
 */
export function originFor(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export function portListening(port: number, timeoutMs = 300): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const done = (ok: boolean) => {
      socket.destroy();
      resolve(ok);
    };
    socket.setTimeout(timeoutMs, () => done(false));
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
  });
}

/**
 * The pure half of {@link isOurBackend}: a 200 whose body names the app
 * (`backend/app.py`'s `health`).
 */
export function isHealthResponse(response: string): boolean {
  const statusLine = response.split(/\r?\n/, 1)[0] ?? '';
  return statusLine.split(/\s+/)[1] === '200' && response.includes('"horrible-dashboard"');
}

/**
 * Whether the server on `port` is this app's backend: `GET /api/health` names the app.
 *
 * *Ours*, not merely something listening: 8000 is a popular port (Docker containers,
 * other dev servers), and adopting whatever holds it means spawning no backend at all
 * and pointing the whole UI at a stranger. Hand-rolled HTTP/1.0 — the server closes
 * when it is done, so reading to EOF is the whole protocol — and capped, because a
 * stranger is free to answer with anything, at any length.
 */
export function isOurBackend(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const chunks: Buffer[] = [];
    let size = 0;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(isHealthResponse(Buffer.concat(chunks).toString('utf8')));
    };
    socket.setTimeout(2_000, finish);
    socket.once('connect', () => {
      socket.write(`GET /api/health HTTP/1.0\r\nHost: 127.0.0.1:${port}\r\n\r\n`);
    });
    socket.on('data', (chunk: Buffer) => {
      chunks.push(chunk);
      size += chunk.length;
      if (size >= 16 * 1024) finish();
    });
    socket.once('end', finish);
    socket.once('close', finish);
    socket.once('error', finish);
  });
}

/** Whether `host:port` can be bound right now. */
function canBind(host: string, port: number): Promise<number | null> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(null));
    // `exclusive` so a cluster primary cannot hand us a shared handle.
    server.listen({ host, port, exclusive: true }, () => {
      const address = server.address();
      const bound = typeof address === 'object' && address ? address.port : null;
      server.close(() => resolve(bound));
    });
  });
}

/**
 * Prefer the configured default port (browser tabs / curl keep working); else
 * ephemeral. `null` skips straight to ephemeral — the default is held by something
 * that is not us.
 *
 * The probe binds the SAME host uvicorn will. Testing loopback while uvicorn takes
 * 0.0.0.0 is not equivalent — a port held by another process on a different
 * interface would pass the probe and then fail the real bind.
 */
export async function pickPort(host: string, targetPort: number | null): Promise<number> {
  if (targetPort !== null && (await canBind(host, targetPort)) !== null) return targetPort;
  const port = await canBind(host, 0);
  if (port === null) throw new Error(`cannot bind any port on ${host}`);
  return port;
}

/**
 * PATH without MinGW bin dirs: MSYS2/Git's libcrypto lacks the MSVC applink shim and
 * aborts uvicorn with "no OPENSSL_Applink" (same fix as scripts/dev-backend.ps1).
 */
export function sanitizedPath(
  pathValue: string | undefined,
  windows = isWindows,
): string | undefined {
  if (pathValue === undefined) return undefined;
  const sep = windows ? ';' : ':';
  return pathValue
    .split(sep)
    .filter((entry) => {
      const lower = entry.toLowerCase();
      return !lower.includes('mingw64\\bin') && !lower.includes('mingw64/bin');
    })
    .join(sep);
}

/** Look up a variable case-insensitively, as Windows does (`Path` vs `PATH`). */
function envKey(env: NodeJS.ProcessEnv, name: string): string {
  if (!isWindows) return name;
  return Object.keys(env).find((k) => k.toUpperCase() === name) ?? name;
}

/** The argv for one spawn of `runtime`. */
export function backendCommand(
  runtime: Runtime,
  host: string,
  port: number,
): { command: string; args: string[]; usesUv: boolean } {
  const uvicorn = ['backend.app:app', '--host', host, '--port', String(port)];
  // The bundled interpreter is the child itself — no `uv`, no wrapper, and nothing on
  // the machine's PATH is consulted. That is the point of shipping it.
  if (runtime.kind === 'bundled') {
    return { command: runtime.python, args: ['-m', 'uvicorn', ...uvicorn], usesUv: false };
  }
  const venvPython = isWindows
    ? path.join(runtime.root, '.venv', 'Scripts', 'python.exe')
    : path.join(runtime.root, '.venv', 'bin', 'python');
  // Prefer the venv python so the child IS the server (clean kill); `uv run` wraps it
  // in another process and needs a tree-kill.
  if (isFile(venvPython)) {
    return { command: venvPython, args: ['-m', 'uvicorn', ...uvicorn], usesUv: false };
  }
  return { command: 'uv', args: ['run', 'uvicorn', ...uvicorn], usesUv: true };
}

/**
 * Keeps the last {@link STDERR_TAIL_LINES} lines of the backend's stderr.
 *
 * Fed raw chunks, decoded leniently: Python writes a piped stderr in the locale
 * encoding (cp1252 on Windows), and the drain must **never stop reading** on a byte
 * that is not UTF-8 — a closed read end makes every later stderr write in the backend
 * raise `OSError: [Errno 22]`, and tqdm (which transformers draws while loading
 * weights) does not swallow that. PYTHONIOENCODING=utf-8 keeps it readable; this keeps
 * it alive.
 */
export class StderrTail {
  private lines: string[] = [];
  private partial = '';

  push(chunk: Buffer | string): void {
    const text = this.partial + (typeof chunk === 'string' ? chunk : chunk.toString('utf8'));
    const parts = text.split('\n');
    this.partial = parts.pop() ?? '';
    for (const line of parts) this.add(line);
  }

  /** Flush a trailing line with no newline (the process exited mid-line). */
  end(): void {
    if (this.partial) this.add(this.partial);
    this.partial = '';
  }

  private add(line: string): void {
    this.lines.push(line.replace(/\r$/, ''));
    if (this.lines.length > STDERR_TAIL_LINES) this.lines.shift();
  }

  text(): string {
    return this.lines.join('\n');
  }
}

export class BackendSupervisor {
  private status: BackendStatus = { state: 'starting', origin: null, error: null };
  private child: ChildProcess | null = null;
  private childExited = true;
  private shuttingDown = false;
  private readonly stderrTail = new StderrTail();
  private running: Promise<void> | null = null;

  /**
   * @param resourceDir where a packaged build's bundled runtime lives
   *   (`process.resourcesPath`), or null in a checkout, where nothing needs it.
   */
  constructor(private readonly resourceDir: string | null) {}

  getStatus(): BackendStatus {
    return { ...this.status };
  }

  /** Start supervising. Returns immediately; poll {@link getStatus}. */
  start(): void {
    this.running ??= this.run().catch((err: unknown) => {
      const error = `backend supervisor crashed: ${String(err)}`;
      console.error(`[desktop] ${error}`);
      this.setStatus('failed', null, error);
    });
  }

  /**
   * Stop the backend and stop restarting it. Idempotent; resolves once the child is
   * gone (or the kill was issued, on Windows).
   */
  async shutdown(): Promise<void> {
    this.shuttingDown = true;
    await this.killChild();
  }

  private setStatus(state: BackendStatus['state'], origin: string | null, error: string | null) {
    this.status = { state, origin, error };
  }

  private async run(): Promise<void> {
    const runtime = resolveRuntime([process.cwd(), process.execPath], this.resourceDir);
    if (!runtime) {
      this.setStatus(
        'unavailable',
        null,
        'no backend found: this build ships no runtime and is not running from a checkout',
      );
      return;
    }

    // Read from whichever root won. A bundled runtime normally has no `.env` — but one
    // placed beside it is a deliberate act by whoever installed the app.
    loadEnvFile(runtime.root);

    const parsedPort = Number.parseInt(process.env.HORRIBLE_DEV_BACKEND_PORT ?? '', 10);
    const targetPort = Number.isInteger(parsedPort) && parsedPort > 0 ? parsedPort : DEFAULT_PORT;

    // Dev coexistence: an already-running backend (scripts/dev-backend.ps1) wins;
    // don't spawn a duplicate. Ours, though — see `isOurBackend`.
    let avoidTarget = false;
    if (await portListening(targetPort)) {
      if (await isOurBackend(targetPort)) {
        this.setStatus('ready', originFor(targetPort), null);
        return;
      }
      console.warn(
        `[desktop] port ${targetPort} is held by something that is not this app's backend; ` +
          'starting ours on another port (set HORRIBLE_DEV_BACKEND_PORT to choose one)',
      );
      avoidTarget = true;
    }

    const host = bindHost();
    let port: number;
    try {
      port = await pickPort(host, avoidTarget ? null : targetPort);
    } catch (err) {
      this.setStatus('failed', null, `no free port: ${String(err)}`);
      return;
    }
    const origin = originFor(port);
    let fastFailures = 0;

    for (;;) {
      if (this.shuttingDown) return;
      this.setStatus('starting', null, null);

      try {
        this.spawnBackend(runtime, host, port);
      } catch (err) {
        // Said here as well as in the status: the status only reaches the page's
        // console, and the terminal is where `pnpm dev:desktop` is read.
        const error = `failed to spawn backend: ${String(err)}`;
        console.error(`[desktop] ${error}`);
        this.setStatus('failed', null, error);
        return;
      }
      const started = Date.now();

      // Readiness: wait for the port to accept connections.
      let ready = false;
      while (Date.now() - started < READY_TIMEOUT_MS) {
        if (this.shuttingDown) return;
        if (this.childExited) break;
        if (await portListening(port)) {
          ready = true;
          break;
        }
        await sleep(250);
      }

      if (ready) {
        this.setStatus('ready', origin, null);
        // Supervise until the process exits or the app shuts down.
        while (!this.shuttingDown && !this.childExited) await sleep(500);
      }

      if (this.shuttingDown) return;
      // Recycle whatever is left (a never-ready process is still running).
      await this.killChild();

      // Only reset the failure counter if the server was ACTUALLY ready and serving
      // for longer than FAST_FAILURE_WINDOW. Never ready, or died quickly: a failure.
      if (ready && Date.now() - started >= FAST_FAILURE_WINDOW_MS) {
        fastFailures = 1;
      } else {
        fastFailures += 1;
      }

      if (!ready && fastFailures === 1 && runtime.kind === 'checkout') {
        // The first start failed in a checkout: repair missing or out-of-date
        // dependencies in `.venv` before retrying. `--inexact` so the repair only
        // adds — a plain sync uninstalls every optional extra the user installed.
        console.warn(
          '[desktop] backend failed to become ready, running `uv sync --inexact` to ensure dependencies are installed...',
        );
        spawnSync('uv', ['sync', '--inexact'], {
          cwd: runtime.root,
          stdio: 'ignore',
          windowsHide: true,
        });
      }

      if (fastFailures >= MAX_FAST_FAILURES) {
        const error = this.stderrTail.text() || 'backend exited repeatedly without becoming ready';
        console.error(
          `[desktop] backend supervisor gave up after ${MAX_FAST_FAILURES} failed attempts:\n${error}`,
        );
        this.setStatus('failed', null, error);
        return;
      }

      // 1s / 2s / 4s; respawn on the SAME port so the frontend's origin stays valid.
      await sleep(1000 * 2 ** Math.min(fastFailures - 1, 2));
    }
  }

  private spawnBackend(runtime: Runtime, host: string, port: number): void {
    const { command, args } = backendCommand(runtime, host, port);

    const env: NodeJS.ProcessEnv = { ...process.env };
    const pathKey = envKey(env, 'PATH');
    const cleaned = sanitizedPath(env[pathKey]);
    if (cleaned !== undefined) env[pathKey] = cleaned;
    // A set SSLKEYLOGFILE (e.g. for Wireshark) makes CPython's ssl module use
    // OpenSSL's FILE*-based keylog API, which aborts python.exe with "no
    // OPENSSL_Applink" on the first SSL context init. The backend never needs it.
    delete env[envKey(env, 'SSLKEYLOGFILE')];
    // A piped stderr on Windows is cp1252 by default; the tail is for people to read.
    env.PYTHONIOENCODING = 'utf-8';
    // ELECTRON_RUN_AS_NODE and friends must not leak into a Python child that might
    // itself launch an Electron/Chromium binary.
    delete env.ELECTRON_RUN_AS_NODE;

    const child = spawn(command, args, {
      cwd: runtime.root,
      env,
      stdio: ['ignore', 'ignore', 'pipe'],
      // No console window flashing up beside a GUI app (CREATE_NO_WINDOW).
      windowsHide: true,
      // Its own process group on POSIX, so `uv run`'s child dies with it.
      detached: !isWindows,
    });
    this.child = child;
    this.childExited = false;
    child.stderr?.on('data', (chunk: Buffer) => this.stderrTail.push(chunk));
    child.once('error', (err) => {
      this.stderrTail.push(`${String(err)}\n`);
      if (this.child === child) this.childExited = true;
    });
    child.once('exit', () => {
      this.stderrTail.end();
      if (this.child === child) this.childExited = true;
    });
  }

  /** Kill the current child (if any). Idempotent. */
  private async killChild(): Promise<void> {
    const child = this.child;
    this.child = null;
    if (!child || child.exitCode !== null || child.signalCode !== null || child.pid === undefined) {
      return;
    }
    const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
    if (isWindows) {
      // Kill the whole tree: `uv run` wraps the real server, and uvicorn may have
      // spawned helpers of its own; a plain kill orphans them (and the port).
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
      await Promise.race([exited, sleep(GRACEFUL_STOP_MS)]);
      return;
    }
    // POSIX: the child leads its own process group. SIGTERM first, so the backend's
    // lifespan shutdown runs (it stops the interp worker, which holds VRAM); SIGKILL
    // if it has not gone by the deadline.
    const signalGroup = (signal: NodeJS.Signals) => {
      try {
        process.kill(-child.pid!, signal);
      } catch {
        try {
          child.kill(signal);
        } catch {
          // Already gone.
        }
      }
    };
    signalGroup('SIGTERM');
    const stopped = await Promise.race([
      exited.then(() => true),
      sleep(GRACEFUL_STOP_MS).then(() => false),
    ]);
    if (!stopped) {
      signalGroup('SIGKILL');
      await Promise.race([exited, sleep(1_000)]);
    }
  }
}

function isFile(p: string): boolean {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function isDir(p: string): boolean {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}
