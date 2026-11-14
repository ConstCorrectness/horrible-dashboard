import { spawn, spawnSync } from 'child_process';
import { existsSync, readdirSync, rmSync, statSync, watch, writeFileSync } from 'fs';
import net from 'net';
import os from 'os';
import path from 'path';
import { clearTimeout, setTimeout } from 'timers';

// Load .env file if it exists so we inherit any local credentials/config (e.g. GAMES_GOOGLE_CLIENT_ID)
if (existsSync('.env')) {
  try {
    process.loadEnvFile('.env');
  } catch (e) {
    console.warn('⚠️ Failed to load .env file:', e);
  }
}

// Full-stack dev: one `pnpm dev` brings up the FastAPI backend, the Vite UI, and
// (unless opted out) the central game server.
//
// Interface the dev servers bind to. Defaults to 127.0.0.1 (local only). Pass
// `--host 0.0.0.0` (see the `dev:lan` script) or set HORRIBLE_DEV_HOST to expose
// the backend's /peer-ws and the Vite UI on the LAN — needed for the distributed
// peer fabric / collaboration, but off by default so a plain `pnpm dev` is local.
const hostArgIndex = process.argv.indexOf('--host');
const host =
  (hostArgIndex !== -1 ? process.argv[hostArgIndex + 1] : undefined) ||
  process.env.HORRIBLE_DEV_HOST ||
  '127.0.0.1';

// pnpm/uv are `.cmd` shims on Windows, which `spawn` can't exec directly — go
// through a shell there. (POSIX resolves them off PATH without one.)
const useShell = process.platform === 'win32';

// The central game server (:9090) comes up with the app so the games module works
// out of the box. It's a *standalone/central* service (hosted in production, not
// per-node), so this is a dev convenience: opt out with `--no-gameserver` or
// HORRIBLE_DEV_NO_GAMESERVER=1 if you run your own / point at the hosted one.
const startGameServer =
  !process.argv.includes('--no-gameserver') && !process.env.HORRIBLE_DEV_NO_GAMESERVER;

// Backend port. Overridable because Windows sometimes reserves 8000 inside a
// Hyper-V dynamic port exclusion range (bind fails with WinError 10013) — set
// HORRIBLE_DEV_BACKEND_PORT (e.g. in .env) to sidestep it. The Vite proxy reads
// the same variable, inherited from this process.
const backendPort = process.env.HORRIBLE_DEV_BACKEND_PORT || '8000';

// The ports this run owns. Used to self-heal a prior interrupted run and to backstop
// our own shutdown. 9090 is only ours when we start the bundled game server — never
// kill a game server the user runs themselves.
const ownedPorts = [5173, 5180, Number(backendPort), ...(startGameServer ? [9090] : [])];

// On Windows, `uv run`/pnpm wrap the servers in a shell, so an interrupted run can
// orphan one that keeps a port
// bound — which then makes the next `pnpm dev` fail with "port in use". Free our
// ports synchronously (so it completes before Node moves on), both before starting
// and on shutdown. No-op on POSIX, where child.kill('SIGINT') is enough.
function freeOwnedPorts() {
  if (process.platform !== 'win32') return;
  spawnSync(
    'powershell',
    [
      '-NoProfile',
      '-Command',
      `Get-NetTCPConnection -State Listen -LocalPort ${ownedPorts.join(',')} -ErrorAction SilentlyContinue | ` +
        `Select-Object -ExpandProperty OwningProcess -Unique | ` +
        `ForEach-Object { taskkill /PID $_ /T /F 2>$null }`,
    ],
    { stdio: 'ignore' },
  );
}

// Heal any orphans a previous interrupted run left on our ports, so a fresh
// `pnpm dev` always starts clean.
freeOwnedPorts();

console.log(`🚀 Starting backend and frontend dev servers on ${host}...`);

// Start the FastAPI backend.
//
// The node's games client is left on the shipped hosted default, the same as
// `pnpm dev:desktop` and a packaged node. This used to force
// GAMES_SERVER_URL=ws://localhost:9090 so `pnpm dev` was self-contained — but the
// bundled game server below is started with no OAuth client id or secret, and the
// env var beats the `games.serverUrl` setting, so the effect was that GitHub and
// Google sign-in went dead in the browser layout and worked on desktop. Two
// launchers, two different answers to "can I sign in", for a reason nothing on
// screen could explain.
//
// Self-contained is still one variable away: HORRIBLE_DEV_LOCAL_GAMESERVER=1 (and
// the bundled server still starts either way, so matches against it work as soon
// as you point at it).
const useLocalGameServer =
  process.argv.includes('--local-gameserver') || process.env.HORRIBLE_DEV_LOCAL_GAMESERVER === '1';
const gamesServerUrl =
  process.env.GAMES_SERVER_URL || (useLocalGameServer ? 'ws://localhost:9090' : undefined);

console.log(
  `🎮 Sign-in / games server: ${gamesServerUrl ?? 'hosted default'}` +
    (gamesServerUrl ? '' : ' (HORRIBLE_DEV_LOCAL_GAMESERVER=1 for the bundled one on :9090)'),
);

// Scrive's {r3f} scene runtime is a build artifact the backend serves
// (backend/modules/scrive/static/, gitignored). Build it once if it is missing, so a
// fresh checkout's scenes work without knowing the step exists.
if (!existsSync('backend/modules/scrive/static/scrive-runtime.js')) {
  console.log('🧊 Building the Scrive scene runtime (first run only)…');
  spawnSync('pnpm', ['--filter', '@horrible/scrive-runtime', 'build'], {
    stdio: 'inherit',
    shell: useShell,
  });
}
// Likewise the GGUF engine a published {webllm} page runs (packages/webml).
if (!existsSync('backend/modules/scrive/static/webml-gguf.worker.js')) {
  console.log('🧠 Building the GGUF engine for published pages (first run only)…');
  spawnSync('pnpm', ['--filter', '@horrible/webml', 'build:embed'], {
    stdio: 'inherit',
    shell: useShell,
  });
}

let cleanedUp = false;

// Tree-kill on Windows: `uv run` / pnpm wrap the real server in a shell, so a plain
// kill orphans it (and leaves the port bound). Synchronous, so it finishes before
// Node exits — an async spawn here can be cut off mid-kill.
function killTree(child) {
  if (child == null || child.exitCode !== null || child.pid == null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
  } else {
    child.kill('SIGINT');
  }
}

// How long a server gets to shut down cleanly before it is killed instead.
const STOP_GRACE_MS = 12000;
// Editors save in bursts (write, rename, touch); one restart per burst.
const RESTART_DEBOUNCE_MS = 300;
// A server that exits *after* it was serving has crashed: a save with a syntax or
// import error fails at import, before the port is ever bound. So a crash is
// restarted, with a growing delay, and only a run of quick crashes — each within
// CRASH_STREAK_MS of coming up — is left down until the next file change, so a
// server that dies on every start cannot loop forever.
const CRASH_STREAK_MS = 30000;
const MAX_CRASH_STREAK = 3;
const CRASH_RESTART_DELAY_MS = 2000;

// What a source file looks like now: its modification time and size, or null when
// it does not exist. Two signatures that match mean nothing was written.
function sourceSignature(file) {
  try {
    const st = statSync(file);
    return `${st.mtimeMs}:${st.size}`;
  } catch {
    return null;
  }
}

// Knows what every watched source file looked like, so a watch event can be checked
// against it before it costs a restart.
//
// `fs.watch` on Windows reports *any* metadata change as `change`, including a bare
// last-access-time update — and NTFS keeps last-access updates on by default
// ("System Managed"). So an indexer, a virus scan, another process importing the
// backend, or an editor merely opening a file restarted the backend with nothing
// edited. Measured 2026-11-14: `utimes(atime only)` fires `change`; read and stat
// do not, but the access time they leave behind is flushed to disk later and fires
// then.
function createSourceIndex(dirs, accept) {
  const known = new Map();
  for (const dir of dirs) {
    let entries = [];
    try {
      entries = readdirSync(dir, { recursive: true });
    } catch {
      continue;
    }
    for (const rel of entries) {
      if (!accept(rel)) continue;
      const file = path.join(dir, rel);
      known.set(file, sourceSignature(file));
    }
  }
  return {
    /** True when the file was really written, created or deleted since last seen. */
    changed(file) {
      const now = sourceSignature(file);
      const before = known.has(file) ? known.get(file) : null;
      known.set(file, now);
      return now !== before;
    },
  };
}

// Run one Python server and restart it when its source changes.
//
// This replaces `uvicorn --reload`, which cannot be used here on Windows: it
// restarts its worker with a console Ctrl+C, every process on the console gets
// that Ctrl+C, and so a saved backend file stopped this script, Vite and the
// other server along with the worker. scripts/dev_backend.py has the rest of the
// reasoning; the short version is that this process does the watching and asks
// the server to stop by creating a file only that server is looking for.
function superviseServer({ name, app, port, watchDirs, ignore = [], env, onFirstExit }) {
  let child = null;
  let restarting = false;
  // When the current process started accepting connections, or null if it has not.
  // "Never came up" is decided by this, not by how many times it was started: the
  // old `starts === 1` test read a crash hours into the first process as a failed
  // start and tore the whole stack down — Vite included — over one dead backend.
  let readyAt = null;
  let everReady = false;
  // Crashes in a row that came soon after the server came up.
  let crashStreak = 0;

  // Polls the port until the given process is serving, or has gone.
  const watchReadiness = (proc) => {
    const clientHost = host === '0.0.0.0' ? '127.0.0.1' : host;
    const probe = () => {
      if (child !== proc) return;
      const socket = net.connect({ port: Number(port), host: clientHost });
      socket.on('connect', () => {
        socket.end();
        if (child !== proc) return;
        readyAt = Date.now();
        everReady = true;
      });
      socket.on('error', () => {
        socket.destroy();
        setTimeout(probe, 250);
      });
    };
    probe();
  };

  // Creating this file is the request to stop. One per supervised server, and
  // per run of this script, so two checkouts running at once cannot stop each other.
  const stopFile = path.join(os.tmpdir(), `horrible-dev-stop-${process.pid}-${port}`);

  const start = () => {
    rmSync(stopFile, { force: true });
    const proc = spawn(
      'uv',
      [
        'run',
        'python',
        'scripts/dev_backend.py',
        app,
        host,
        String(port),
        // Quoted for the Windows shell: the temp dir is under the user's profile,
        // and a profile name can have a space in it.
        useShell ? `"${stopFile}"` : stopFile,
        String(process.pid),
      ],
      {
        // No stdin at all. The server starts child processes of its own, and a
        // pipe here would be inherited by every one of them.
        stdio: ['ignore', 'inherit', 'inherit'],
        shell: useShell,
        env,
      },
    );
    proc.on('exit', (code) => {
      if (child !== proc) return;
      child = null;
      const served = readyAt !== null;
      const servedFor = served ? Date.now() - readyAt : 0;
      readyAt = null;
      rmSync(stopFile, { force: true });
      if (cleanedUp) return;
      if (restarting) {
        restarting = false;
        crashStreak = 0;
        start();
      } else if (!everReady) {
        // Never came up at all: a bad port, a broken environment.
        onFirstExit(code);
      } else if (served) {
        // It was serving and then died — a native crash, an out-of-memory kill.
        crashStreak = servedFor < CRASH_STREAK_MS ? crashStreak + 1 : 1;
        const where =
          "A native crash leaves 'Fatal Python error' or 'Windows fatal exception' in " +
          'logs/backend.log.';
        if (crashStreak > MAX_CRASH_STREAK) {
          console.warn(
            `⚠️  ${name} crashed ${crashStreak} times in a row soon after starting. ` +
              `Waiting for a file change to restart it. ${where}`,
          );
          return;
        }
        const delay = CRASH_RESTART_DELAY_MS * crashStreak;
        console.warn(
          `⚠️  ${name} exited (code ${code}) after ${Math.round(servedFor / 1000)}s of serving. ` +
            `Restarting it in ${delay / 1000}s. ${where}`,
        );
        setTimeout(() => {
          if (!cleanedUp && child === null) start();
        }, delay);
      } else {
        // Came up before and fell over before serving again, almost always on a
        // syntax or import error in the file just saved. Stay up and wait for the
        // fix rather than looping on it or taking the stack down over a typo.
        console.warn(`⚠️  ${name} exited (code ${code}). Waiting for a file change to restart it.`);
      }
    });
    child = proc;
    watchReadiness(proc);
  };

  const restart = (file) => {
    if (cleanedUp || restarting) return;
    console.log(`\n♻️  ${file} changed — restarting ${name}...`);
    if (child === null) {
      start();
      return;
    }
    restarting = true;
    const stopping = child;
    try {
      writeFileSync(stopFile, '');
    } catch {
      // Falls through to the kill below.
    }
    const giveUp = setTimeout(() => killTree(stopping), STOP_GRACE_MS);
    stopping.once('exit', () => clearTimeout(giveUp));
  };

  const isSource = (rel) => {
    if (!rel.endsWith('.py')) return false;
    const parts = rel.split(path.sep);
    return !parts.includes('__pycache__') && !ignore.includes(parts[0]);
  };
  const sources = createSourceIndex(watchDirs, isSource);

  let debounce = null;
  for (const dir of watchDirs) {
    try {
      watch(dir, { recursive: true }, (_event, filename) => {
        if (!filename || !isSource(filename)) return;
        const file = path.join(dir, filename);
        // Checked at once, not when the debounce fires: by then a later event for
        // another file may have replaced this one, and this one's write be missed.
        if (!sources.changed(file)) return;
        clearTimeout(debounce);
        debounce = setTimeout(() => restart(file), RESTART_DEBOUNCE_MS);
      });
    } catch (err) {
      console.warn(`⚠️  Not watching ${dir} for changes (${err.message}); restart ${name} by hand.`);
    }
  }

  start();
  return {
    get child() {
      return child;
    },
    /** True once a process has exited without the server ever having come up. */
    get failedToStart() {
      return child === null && !everReady;
    },
  };
}

// The node backend. Restarted for any source change under backend/ except the
// tests and scratch files, which the app never imports: a test edit used to cost
// a full restart of a backend that takes seconds to come up.
let backendExitCode = null;
const backend = superviseServer({
  name: 'backend',
  app: 'backend.app:app',
  port: backendPort,
  watchDirs: ['backend'],
  ignore: ['tests', 'scratch'],
  env: {
    ...process.env,
    // Only set when asked: an undefined value would still register as a set
    // key on some platforms, and the resolver treats "set" as "env wins".
    ...(gamesServerUrl ? { GAMES_SERVER_URL: gamesServerUrl } : {}),
  },
  // If the core server never starts, tear the rest down so we don't leave a
  // half-stack up.
  onFirstExit: (code) => {
    backendExitCode = code;
    cleanup();
  },
});

function waitForPort(port, host, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const startTime = Date.now();
    const clientHost = host === '0.0.0.0' ? '127.0.0.1' : host;
    
    const tryConnect = () => {
      if (backend.failedToStart) {
        resolve(false);
        return;
      }
      
      const socket = net.connect({ port, host: clientHost });
      
      socket.on('connect', () => {
        socket.end();
        resolve(true);
      });
      
      socket.on('error', () => {
        socket.destroy();
        if (Date.now() - startTime > timeoutMs) {
          resolve(false);
        } else {
          setTimeout(tryConnect, 250);
        }
      });
    };
    
    tryConnect();
  });
}

const gameserver = startGameServer
  ? superviseServer({
      name: 'game server (:9090)',
      app: 'backend.games_server.app:app',
      port: 9090,
      watchDirs: ['backend/games_server', 'backend/games_engine'],
      env: { ...process.env, GAMES_ENABLE_CODE_EXEC: '1' },
      // Non-core: if it never starts (commonly because :9090 is already in use by
      // a game server you started yourself), warn but keep the rest of the stack up.
      onFirstExit: (code) => {
        console.warn(
          `⚠️  game server (:9090) exited (code ${code}); the games module won't work ` +
            'until one is running. The rest of the app is unaffected.',
        );
      },
    })
  : null;

let frontend;
let assaultWeb;
function cleanup() {
  if (cleanedUp) return;
  cleanedUp = true;
  console.log('\nStopping dev servers...');
  for (const child of [backend.child, frontend, gameserver?.child, assaultWeb]) {
    killTree(child);
  }
  // Backstop: kill anything still bound to one of our ports after the tree-kill,
  // so the next `pnpm dev` starts clean.
  freeOwnedPorts();
}

// Wait for the backend to start listening before launching the frontend
// to avoid Vite proxy ECONNREFUSED log spam.
console.log(`⏳ Waiting for backend to start on http://${host}:${backendPort}...`);
const backendReady = await waitForPort(Number(backendPort), host);
if (backendReady) {
  console.log(`✅ Backend is up and listening!`);
} else if (backend.failedToStart) {
  console.warn(`❌ Backend process exited early with code ${backendExitCode}.`);
} else {
  console.warn(`⚠️  Backend port did not respond within timeout, starting frontend anyway.`);
}

// Start the Vite frontend (its config reads HORRIBLE_DEV_HOST for the listen host
// and HORRIBLE_DEV_BACKEND_PORT for the /api and /ws proxy target).
frontend = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env: { ...process.env, HORRIBLE_DEV_HOST: host, HORRIBLE_DEV_BACKEND_PORT: backendPort },
});
frontend.on('exit', cleanup);

// Start HorribleAssault standalone browser client (:5180) unless opted out
if (!process.argv.includes('--no-assault')) {
  assaultWeb = spawn('pnpm', ['--filter', '@horrible/assault-web', 'dev'], {
    stdio: 'inherit',
    shell: useShell,
    env: {
      ...process.env,
      HORRIBLE_DEV_HOST: host,
      HORRIBLE_DEV_BACKEND_PORT: backendPort,
      HORRIBLE_GAME_SERVER_PORT: '9090',
    },
  });
  assaultWeb.on('exit', (code) => {
    if (!cleanedUp && code !== 0 && code !== null) {
      console.warn(`⚠️  assault-web (:5180) exited (code ${code}).`);
    }
  });
}

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
process.on('exit', cleanup);
