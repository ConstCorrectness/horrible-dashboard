import { spawn, spawnSync } from 'child_process';
import { existsSync, readFileSync, statSync } from 'fs';
import { createRequire } from 'module';
import { createServer } from 'net';
import { setTimeout } from 'timers';
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

// The dev server's port, checked before anything starts. Vite would say "Port 5173 is
// already in use" and stop — after the shell had already been launched at a server
// that was never coming — and the holder is nearly always a dev server from an
// earlier session whose terminal is gone, which that message gives no way to find.
const devPort = Number(process.env.PORT) || 5173; // as apps/web/vite.config.ts reads it

function portFree(port, bindHost) {
  return new Promise((resolve) => {
    const probe = createServer();
    probe.once('error', () => resolve(false));
    probe.listen(port, bindHost, () => probe.close(() => resolve(true)));
  });
}

/** "pid 1234: node …/vite.js (up 07:39:45)", or null where it cannot be told. */
function portHolder(port) {
  if (process.platform === 'win32') return null;
  const found = spawnSync('lsof', ['-nP', `-iTCP:${port}`, '-sTCP:LISTEN', '-t'], {
    encoding: 'utf8',
  });
  const pid = found.stdout?.split('\n')[0]?.trim();
  if (!pid) return null;
  const ps = spawnSync('ps', ['-o', 'etime=,args=', '-p', pid], { encoding: 'utf8' });
  const [etime, ...args] = (ps.stdout ?? '').trim().split(/\s+/);
  return args.length ? `pid ${pid}: ${args.join(' ').slice(0, 120)} (up ${etime})` : `pid ${pid}`;
}

if (!(await portFree(devPort, host))) {
  const holder = portHolder(devPort);
  console.error(`❌ Port ${devPort} is already in use${holder ? ` — ${holder}` : ''}.`);
  console.error(
    holder
      ? '   Usually a dev server left over from an earlier session; stop it and run this again.'
      : '   Stop whatever is serving it (often an earlier dev server) and run this again.',
  );
  process.exit(1);
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

// The Electron binary itself, not `pnpm … dev` → `electron/cli.js` → Electron: stopping
// the shell has to reach its **main process alone** (see `stopAll`), and through two
// wrappers this script only knows the outermost pid.
const desktopDir = join(process.cwd(), 'apps', 'desktop');
const electronBinary = createRequire(join(desktopDir, 'package.json'))('electron');

let shell = null;
let exiting = false;

function alive(child) {
  return child && child.pid !== undefined && child.exitCode === null && child.signalCode === null;
}

function stopAll(code) {
  if (exiting) return;
  exiting = true;
  for (const child of [vite, shell]) {
    if (!alive(child)) continue;
    if (process.platform === 'win32') {
      // Through a shell, `kill` would only end cmd.exe and orphan Vite/Electron.
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else if (child === shell) {
      // Electron's main process only, never its group. SIGTERM there is a quit:
      // `will-quit` stops the backend, then the zygote and GPU process leave with it.
      // Signalled as a group they die in the same instant, Chromium aborts relaunching
      // the GPU process ("GPU process isn't usable. Goodbye."), and the backend is
      // orphaned on :8000 — which is what this used to do.
      try {
        process.kill(child.pid, 'SIGTERM');
      } catch {
        // Already gone.
      }
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
  // which crashes it before it gets there and orphans uvicorn on its port. Past the
  // deadline, its whole group goes.
  if (alive(shell)) {
    shell.once('exit', () => process.exit(code));
    setTimeout(() => {
      if (process.platform !== 'win32') {
        try {
          process.kill(-shell.pid, 'SIGKILL');
        } catch {
          // Already gone.
        }
      }
      process.exit(code);
    }, 10_000).unref();
  } else {
    process.exit(code);
  }
}

vite.on('exit', (code, signal) => stopAll(signal ? 1 : (code ?? 1)));
process.on('SIGINT', () => stopAll(130));
process.on('SIGTERM', () => stopAll(143));
// A closed terminal. Without this the launcher dies on the spot, and Vite and
// Electron — process groups of their own — run on headless, holding :5173.
process.on('SIGHUP', () => stopAll(129));

// Compile the shell (what its `dev` script does before `electron .`), then start it.
const build = spawn('pnpm', ['--filter', '@horrible/desktop', 'build'], {
  stdio: 'inherit',
  shell: useShell,
  env,
});
build.on('exit', (code) => {
  if (exiting) return;
  if (code !== 0) {
    stopAll(code ?? 1);
    return;
  }
  shell = spawn(electronBinary, ['.', ...shellArgs], {
    cwd: desktopDir,
    stdio: 'inherit',
    env,
    detached,
  });
  // Closing the window ends the session.
  shell.on('exit', (exitCode, signal) => stopAll(signal ? 1 : (exitCode ?? 0)));
});
