import { spawn } from 'child_process';

// Full-stack dev: one `pnpm dev` brings up the FastAPI backend AND the Vite UI.
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
    '8000',
  ],
  {
    stdio: 'inherit',
    shell: useShell,
    env: {
      ...process.env,
      GAMES_SERVER_URL: process.env.GAMES_SERVER_URL || 'ws://localhost:9200',
    },
  },
);

// Start the Vite frontend (its config reads HORRIBLE_DEV_HOST for the listen host).
const frontend = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env: { ...process.env, HORRIBLE_DEV_HOST: host },
});

// Optionally start the central game server (:9200) so the games module works out of
// the box — one `pnpm dev` and you can play. It's a *standalone/central* service
// (in production it's hosted, not per-node), so this is a dev convenience: opt out
// with `--no-gameserver` or HORRIBLE_DEV_NO_GAMESERVER=1 if you run it yourself.
const startGameServer =
  !process.argv.includes('--no-gameserver') && !process.env.HORRIBLE_DEV_NO_GAMESERVER;
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
        '9200',
      ],
      { stdio: 'inherit', shell: useShell },
    )
  : null;

let cleanedUp = false;
function cleanup() {
  if (cleanedUp) return;
  cleanedUp = true;
  console.log('\nStopping dev servers...');
  for (const child of [backend, frontend, gameserver]) {
    if (child == null || child.exitCode !== null || child.pid == null) continue;
    if (process.platform === 'win32') {
      // `uv run` / pnpm wrap the real server in a shell, so a plain kill orphans
      // uvicorn (and leaves the port bound). Tree-kill the whole process group.
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill('SIGINT');
    }
  }
}

// If a *core* server dies, tear the other down so we don't leave a half-stack up.
backend.on('exit', cleanup);
frontend.on('exit', cleanup);
// The game server is non-core: if it dies (commonly because :9200 is already in use
// from a game server you started yourself), warn but keep the rest of the stack up.
gameserver?.on('exit', (code) => {
  if (!cleanedUp) {
    console.warn(
      `⚠️  game server (:9200) exited (code ${code}); the games module won't work ` +
        'until one is running. The rest of the app is unaffected.',
    );
  }
});

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
process.on('exit', cleanup);
