import { spawn } from 'child_process';
import { existsSync } from 'fs';

// Load .env if it exists so we inherit any local credentials/config, exactly as
// scripts/dev.mjs does. Tauri's `beforeDevCommand` (Vite) and the Rust backend
// supervisor both inherit this process's environment, so anything set here reaches
// all three.
if (existsSync('.env')) {
  try {
    process.loadEnvFile('.env');
  } catch (e) {
    console.warn('⚠️ Failed to load .env file:', e);
  }
}

// Desktop dev launcher — a thin wrapper around `tauri dev` whose only job is to
// pick the bind interface.
//
// Unlike `pnpm dev`, this DEFAULTS to 0.0.0.0 (what `pnpm dev:lan` opts into for
// the browser layout). The desktop node is the one people actually pair a phone
// with and run the peer fabric from — the Android companion, remote control and
// cross-node hassault matches all need the backend reachable off the loopback, and
// a desktop app already has a window of its own rather than a browser tab pointed
// at localhost. Opt back out with `--host 127.0.0.1` or HORRIBLE_DEV_HOST.
//
// This binds an unauthenticated API to the LAN, same as `pnpm dev:lan`: fine on a
// home/office network, not on untrusted wifi.
const hostArgIndex = process.argv.indexOf('--host');
const host =
  (hostArgIndex !== -1 ? process.argv[hostArgIndex + 1] : undefined) ||
  process.env.HORRIBLE_DEV_HOST ||
  '0.0.0.0';

// pnpm is a `.cmd` shim on Windows, which `spawn` can't exec directly — go through
// a shell there. (POSIX resolves it off PATH without one.)
const useShell = process.platform === 'win32';

console.log(
  host === '127.0.0.1'
    ? '🖥️  Starting desktop layout (local only)...'
    : `🖥️  Starting desktop layout on ${host} (reachable from the LAN)...`,
);

// HORRIBLE_DEV_HOST is the single host knob the rest of the stack already reads:
// apps/web/vite.config.ts for the dev server Tauri loads, and backend.rs for the
// uvicorn it supervises.
const child = spawn('pnpm', ['--filter', '@horrible/desktop', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env: { ...process.env, HORRIBLE_DEV_HOST: host },
});

child.on('exit', (code, signal) => {
  process.exit(signal ? 1 : (code ?? 0));
});
