import { spawn, spawnSync } from 'child_process';
import { existsSync, readFileSync, statSync } from 'fs';
import { createRequire } from 'module';
import { dirname, join } from 'path';

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
// Each child leads its own process group on POSIX, so stopping one stops what
// `pnpm` started under it too — a SIGTERM to pnpm alone leaves Vite running and
// holding :5173, and the next `pnpm dev:desktop` fails on the port.
const detached = process.platform !== 'win32';
const vite = spawn('pnpm', ['--filter', '@horrible/web', 'dev'], {
  stdio: 'inherit',
  shell: useShell,
  env,
  detached,
});

// Chromium's Linux sandbox needs either unprivileged user namespaces or a setuid-root
// `chrome-sandbox`. Ubuntu 24.04+ restricts the first by default (AppArmor), and the
// copy in node_modules is never setuid — so on such a machine Electron dies at
// launch with a sandbox FATAL. The Tauri shell hit the same wall with WebKit's
// sandbox and turned it off; here it is turned off only when it cannot work, and
// only for this dev launcher (an installed .deb ships a setuid helper).
function linuxSandboxUnavailable() {
  if (process.platform !== 'linux') return false;
  // Chromium refuses to start sandboxed as root at all (containers, CI).
  if (process.getuid?.() === 0) return true;
  const read = (p) => {
    try {
      return readFileSync(p, 'utf8').trim();
    } catch {
      return null;
    }
  };
  const usernsBlocked =
    read('/proc/sys/kernel/apparmor_restrict_unprivileged_userns') === '1' ||
    read('/proc/sys/kernel/unprivileged_userns_clone') === '0' ||
    // Containers commonly have none to give.
    read('/proc/sys/user/max_user_namespaces') === '0';
  if (!usernsBlocked) return false;
  try {
    const electronDir = dirname(
      createRequire(join(process.cwd(), 'apps', 'desktop', 'package.json')).resolve(
        'electron/package.json',
      ),
    );
    const helper = statSync(join(electronDir, 'dist', 'chrome-sandbox'));
    const setuidRoot = helper.uid === 0 && (helper.mode & 0o4000) !== 0;
    return !setuidRoot;
  } catch {
    return true;
  }
}
const shellArgs = linuxSandboxUnavailable() ? ['--no-sandbox'] : [];
if (shellArgs.length) {
  console.log(
    'ℹ️  Chromium sandbox unavailable here (root, or user namespaces restricted) — running with --no-sandbox.',
  );
}

const shell = spawn('pnpm', ['--filter', '@horrible/desktop', 'dev', ...shellArgs], {
  stdio: 'inherit',
  shell: useShell,
  env,
  detached,
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
      try {
        process.kill(-child.pid, 'SIGTERM');
      } catch {
        // Already gone.
      }
    }
  }
  // Give the shell its quit: it stops the backend on the way out, and leaving first
  // can pull the display out from under it (xvfb-run, a closing terminal session),
  // which crashes it before it gets there and orphans uvicorn on its port.
  if (shell.exitCode === null && shell.signalCode === null) {
    shell.once('exit', () => process.exit(code));
    setTimeout(() => process.exit(code), 8_000).unref();
  } else {
    process.exit(code);
  }
}

// Closing the window ends the session; so does Vite dying under it.
shell.on('exit', (code, signal) => stopAll(signal ? 1 : (code ?? 0)));
vite.on('exit', (code, signal) => stopAll(signal ? 1 : (code ?? 1)));
process.on('SIGINT', () => stopAll(130));
process.on('SIGTERM', () => stopAll(143));
