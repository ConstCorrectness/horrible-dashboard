import { spawn, spawnSync } from 'child_process';
import { existsSync } from 'fs';

// Load .env if it exists so we inherit any local credentials/config, exactly as
// scripts/dev.mjs does. Tauri's `beforeDevCommand` (Vite) and the backend
// supervisor both inherit this process's environment, so anything set here reaches
// all processes.
if (existsSync('.env')) {
  try {
    process.loadEnvFile('.env');
  } catch (e) {
    console.warn('⚠️ Failed to load .env file:', e);
  }
}

// Desktop dev launcher — selects the bind interface and desktop shell engine.
//
// Unlike `pnpm dev`, this DEFAULTS to 0.0.0.0 (what `pnpm dev:lan` opts into for
// the browser layout). The desktop node is the one people actually pair a phone
// with and run the peer fabric from — the Android companion, remote control and
// cross-node hassault matches all need the backend reachable off the loopback, and
// a desktop app already has a window of its own rather than a browser tab pointed
// at localhost. Opt back out with `--host 127.0.0.1` or HORRIBLE_DEV_HOST.
const hostArgIndex = process.argv.indexOf('--host');
const host =
  (hostArgIndex !== -1 ? process.argv[hostArgIndex + 1] : undefined) ||
  process.env.HORRIBLE_DEV_HOST ||
  '0.0.0.0';

const shellArgIndex = process.argv.indexOf('--shell');
const requestedShell =
  shellArgIndex !== -1 ? process.argv[shellArgIndex + 1]?.toLowerCase() : undefined;

// On Linux, default to the Electron shell to prevent WebKitGTK memory leaks and OOM crashes.
// On Windows / macOS, default to Tauri.
const useElectron =
  requestedShell === 'electron' ||
  (!requestedShell &&
    (process.platform === 'linux' || process.env.HORRIBLE_DESKTOP_SHELL === 'electron'));

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
    if (uvResult.error && uvResult.error.code !== 'ENOENT' && uvResult.error.code !== 'EACCES') {
      console.warn('⚠️ Failed to run uv sync:', uvResult.error);
    }
  } catch (e) {
    console.warn('⚠️ Could not run uv sync:', e);
  }
}

console.log(
  host === '127.0.0.1'
    ? `🖥️  Starting desktop layout (${useElectron ? 'Electron' : 'Tauri'}, local only)...`
    : `🖥️  Starting desktop layout on ${host} (${useElectron ? 'Electron' : 'Tauri'}, reachable from the LAN)...`,
);

if (useElectron) {
  // For Electron, run the Vite frontend dev server and the Electron shell concurrently.
  // The Electron main process supervises the Python FastAPI backend directly.
  const viteChild = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
    stdio: 'inherit',
    shell: useShell,
    env: { ...process.env, HORRIBLE_DEV_HOST: host },
  });

  const electronChild = spawn('pnpm', ['--filter', '@horrible-dashboard/desktop-electron', 'dev'], {
    stdio: 'inherit',
    shell: useShell,
    env: { ...process.env, HORRIBLE_DEV_HOST: host, ELECTRON_DISABLE_SANDBOX: '1' },
  });

  const cleanup = () => {
    try {
      viteChild.kill('SIGTERM');
      electronChild.kill('SIGTERM');
    } catch {
      // ignore
    }
  };

  process.on('SIGINT', () => {
    cleanup();
    process.exit(0);
  });
  process.on('SIGTERM', () => {
    cleanup();
    process.exit(0);
  });

  electronChild.on('exit', (code, signal) => {
    cleanup();
    process.exit(signal ? 1 : (code ?? 0));
  });

  viteChild.on('exit', (code, signal) => {
    if (code !== 0 && !electronChild.killed) {
      cleanup();
      process.exit(signal ? 1 : (code ?? 0));
    }
  });
} else {
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
}
