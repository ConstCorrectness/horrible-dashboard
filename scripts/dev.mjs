import { spawn } from 'child_process';

// Interface the dev servers bind to. Defaults to 0.0.0.0 so the backend's
// /peer-ws and the Vite UI are reachable from other machines on the LAN (needed
// for the distributed peer fabric / collaboration). Set HORRIBLE_DEV_HOST=127.0.0.1
// to keep everything local.
const host = process.env.HORRIBLE_DEV_HOST || '0.0.0.0';

console.log(`🚀 Starting backend and frontend dev servers on ${host}...`);

// Start the FastAPI backend
const backend = spawn(
  'uv',
  ['run', 'uvicorn', 'backend.app:app', '--reload', '--reload-dir', 'backend', '--host', host, '--port', '8000'],
  {
    stdio: 'inherit',
  },
);

// Start the Vite frontend (its config reads HORRIBLE_DEV_HOST for the listen host).
const frontend = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
  env: { ...process.env, HORRIBLE_DEV_HOST: host },
});

function cleanup() {
  console.log('\nStopping dev servers...');
  backend.kill('SIGINT');
  frontend.kill('SIGINT');
  process.exit();
}

process.on('SIGINT', cleanup);
process.on('SIGTERM', cleanup);
process.on('exit', cleanup);
