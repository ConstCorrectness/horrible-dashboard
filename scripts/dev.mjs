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

// Start the FastAPI backend.
const backend = spawn(
  'uv',
  ['run', 'uvicorn', 'backend.app:app', '--reload', '--reload-dir', 'backend', '--host', host, '--port', '8000'],
  { stdio: 'inherit', shell: useShell },
);

// Start the Vite frontend (its config reads HORRIBLE_DEV_HOST for the listen host).
const frontend = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env: { ...process.env, HORRIBLE_DEV_HOST: host },
});

let cleanedUp = false;
function cleanup() {
  if (cleanedUp) return;
  cleanedUp = true;
  console.log('\nStopping dev servers...');
  for (const child of [backend, frontend]) {
    if (child.exitCode !== null || child.pid == null) continue;
    if (process.platform === 'win32') {
      // `uv run` / pnpm wrap the real server in a shell, so a plain kill orphans
      // uvicorn (and leaves :8000 bound). Tree-kill the whole process group.
      spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill('SIGINT');
    }
  }
}

// If either server dies, tear the other down so we don't leave a half-stack up.
backend.on('exit', cleanup);
frontend.on('exit', cleanup);

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
process.on('exit', cleanup);
