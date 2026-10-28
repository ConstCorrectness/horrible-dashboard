import { spawn, spawnSync } from 'child_process';
import { existsSync } from 'fs';

// Load .env if it exists so we inherit any local credentials/config, exactly as
// scripts/dev.mjs does. Vite and the Electron shell (which supervises the backend)
// both inherit this process's environment, so anything set here reaches all three.
if (existsSync('.env')) {
  try {
    process.loadEnvFile('.env');
  } catch (e) {
    console.warn('⚠️ Failed to load .env file:', e);
  }
}

// Desktop dev launcher: the Vite dev server plus the Electron shell (apps/desktop),
// which loads that server and supervises the backend itself.
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

// Keep backend dependencies up-to-date with pyproject.toml.
//
// `--inexact` is load-bearing: a plain `uv sync` makes the venv match the core
// dependency set *exactly*, which uninstalls every optional extra — so each
// `pnpm dev:desktop` silently removed `--extra voice` (torch/transformers/
// edge-tts), `clip`, `llamacpp`, … and the features gated on them reported
// "not installed" right after the user had installed them.
if (existsSync('pyproject.toml')) {
  try {
    const uvResult = spawnSync('uv', ['sync', '--inexact'], {
      stdio: 'inherit',
      shell: useShell,
    });
    if (uvResult.error && uvResult.error.code !== 'ENOENT') {
      console.warn('⚠️ Failed to run uv sync:', uvResult.error);
    }
  } catch (e) {
    console.warn('⚠️ Could not run uv sync:', e);
  }
}

console.log(
  host === '127.0.0.1'
    ? '🖥️  Starting desktop layout (local only)...'
    : `🖥️  Starting desktop layout on ${host} (reachable from the LAN)...`,
);

// HORRIBLE_DEV_HOST is the single host knob the rest of the stack already reads:
// apps/web/vite.config.ts for the dev server the shell loads, and
// apps/desktop/src/backend.ts for the uvicorn it supervises.
const env = { ...process.env, HORRIBLE_DEV_HOST: host };

// Both at once: the shell keeps retrying the dev URL until Vite answers, so neither
// has to wait for the other, and the backend boots in parallel with both.
const vite = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env,
});
const shell = spawn('pnpm', ['--filter', '@horrible/desktop', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env,
});

let exiting = false;
function stopAll(code) {
  if (exiting) return;
  exiting = true;
  for (const child of [vite, shell]) {
    if (child.exitCode !== null || child.pid === undefined) continue;
    if (process.platform === 'win32') {
      // Through a shell, `kill` would only end cmd.exe and orphan Vite/Electron.
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill('SIGTERM');
    }
  }
  process.exit(code);
}

// Closing the window ends the session; so does Vite dying under it.
shell.on('exit', (code, signal) => stopAll(signal ? 1 : (code ?? 0)));
vite.on('exit', (code, signal) => stopAll(signal ? 1 : (code ?? 1)));
process.on('SIGINT', () => stopAll(130));
process.on('SIGTERM', () => stopAll(143));
