import { spawn } from 'child_process';

console.log('🚀 Starting backend and frontend dev servers...');

// Start the FastAPI backend
const backend = spawn('uv', ['run', 'uvicorn', 'backend.app:app', '--reload', '--reload-dir', 'backend', '--port', '8000'], {
  stdio: 'inherit',
});

// Start the Vite frontend
const frontend = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
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
