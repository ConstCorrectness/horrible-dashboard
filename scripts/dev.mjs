import { spawn, spawnSync } from 'child_process';
import { existsSync } from 'fs';
import net from 'net';
import { setTimeout } from 'timers';

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
const ownedPorts = [5173, Number(backendPort), ...(startGameServer ? [9090] : [])];

// On Windows, `uv run`/pnpm wrap uvicorn/vite in a shell and `uvicorn --reload`
// respawns workers, so an interrupted run can orphan a worker that keeps a port
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

// Start the FastAPI backend. Point the node's games client at the local game server
// (below) rather than the shipped hosted default, unless the user set GAMES_SERVER_URL
// themselves. So `pnpm dev` is self-contained; a packaged node still defaults to hosted.
const backend = spawn(
  'uv',
  [
    'run',
    'uvicorn',
    'backend.app:app',
    '--reload',
    '--reload-dir',
    'backend',
    '--reload-exclude',
    'logs/*',
    '--host',
    host,
    '--port',
    backendPort,
  ],
  {
    stdio: 'inherit',
    shell: useShell,
    env: {
      ...process.env,
      GAMES_SERVER_URL: process.env.GAMES_SERVER_URL || 'ws://localhost:9090',
    },
  },
);

function waitForPort(port, host, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const startTime = Date.now();
    const clientHost = host === '0.0.0.0' ? '127.0.0.1' : host;
    
    const tryConnect = () => {
      if (backend.exitCode !== null) {
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
  ? spawn(
      'uv',
      [
        'run',
        'uvicorn',
        'backend.games_server.app:app',
        '--reload',
        '--reload-dir',
        'backend/games_server',
        '--reload-dir',
        'backend/games_engine',
        '--host',
        host,
        '--port',
        '9090',
      ],
      {
        stdio: 'inherit',
        shell: useShell,
        env: { ...process.env, GAMES_ENABLE_CODE_EXEC: '1' },
      },
    )
  : null;

let frontend;
let cleanedUp = false;
function cleanup() {
  if (cleanedUp) return;
  cleanedUp = true;
  console.log('\nStopping dev servers...');
  for (const child of [backend, frontend, gameserver]) {
    if (child == null || child.exitCode !== null || child.pid == null) continue;
    if (process.platform === 'win32') {
      // `uv run` / pnpm wrap the real server in a shell, so a plain kill orphans
      // uvicorn (and leaves the port bound). Tree-kill the whole process group —
      // synchronously, so it finishes before Node exits (an async spawn here can be
      // cut off mid-kill and orphan the worker).
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill('SIGINT');
    }
  }
  // Backstop: kill any reloader worker that respawned onto a port after (or during)
  // the tree-kill, so the next `pnpm dev` starts clean.
  freeOwnedPorts();
}

// If a *core* server dies, tear the other down so we don't leave a half-stack up.
backend.on('exit', cleanup);

// Wait for the backend to start listening before launching the frontend
// to avoid Vite proxy ECONNREFUSED log spam.
console.log(`⏳ Waiting for backend to start on http://${host}:${backendPort}...`);
const backendReady = await waitForPort(Number(backendPort), host);
if (backendReady) {
  console.log(`✅ Backend is up and listening!`);
} else if (backend.exitCode !== null) {
  console.warn(`❌ Backend process exited early with code ${backend.exitCode}.`);
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
// The game server is non-core: if it dies (commonly because :9090 is already in use
// from a game server you started yourself), warn but keep the rest of the stack up.
gameserver?.on('exit', (code) => {
  if (!cleanedUp) {
    console.warn(
      `⚠️  game server (:9090) exited (code ${code}); the games module won't work ` +
        'until one is running. The rest of the app is unaffected.',
    );
  }
});

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
process.on('exit', cleanup);
