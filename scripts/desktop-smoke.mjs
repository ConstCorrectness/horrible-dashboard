#!/usr/bin/env node
/**
 * Drive the real desktop app (apps/desktop) end to end and check that the shell
 * does its job: the window opens, the backend comes up and is reachable from the
 * page (CORS + the `/ws` origin check), every command family answers, and the guards
 * refuse what they must. Playwright's Electron driver talks to the running app, so
 * this is the packaged binary doing real work, not a mock.
 *
 *   node scripts/desktop-smoke.mjs                 # the unpacked build in apps/desktop/release
 *   node scripts/desktop-smoke.mjs --exe <path>    # a specific executable
 *   node scripts/desktop-smoke.mjs --dev           # apps/desktop/dist under plain electron
 *   … --no-wm                                      # a display with no window manager
 *                                                  # (bare Xvfb): skip the maximize check
 *
 * The backend comes from this checkout (the app is started with the repo as its
 * working directory, which the supervisor prefers), on a free port, with a throwaway
 * HORRIBLE_DATA_DIR. Results land in desktop-smoke-results/smoke-<platform>.json; the
 * exit code is the number of failed checks.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DESKTOP = join(REPO, 'apps', 'desktop');
// Playwright is a devDependency of the desktop app, not of the repo root.
const { _electron: electron } = createRequire(join(DESKTOP, 'package.json'))('playwright');

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name) => {
  const i = argv.indexOf(name);
  return i === -1 ? undefined : argv[i + 1];
};

function defaultExecutable() {
  const release = join(DESKTOP, 'release');
  if (process.platform === 'win32') return join(release, 'win-unpacked', 'horrible-dashboard.exe');
  if (process.platform === 'darwin') {
    const dir = process.arch === 'arm64' ? 'mac-arm64' : 'mac';
    return join(release, dir, 'horrible-dashboard.app', 'Contents', 'MacOS', 'horrible-dashboard');
  }
  return join(release, 'linux-unpacked', 'horrible-dashboard');
}

function freePort() {
  return new Promise((res, rej) => {
    const server = createServer();
    server.once('error', rej);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => res(port));
    });
  });
}

const results = [];
async function check(name, fn) {
  const started = Date.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, ms: Date.now() - started, detail: detail ?? null });
    console.log(
      `  ✅ ${name}${detail ? ` — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`,
    );
  } catch (err) {
    results.push({ name, ok: false, ms: Date.now() - started, error: String(err?.message ?? err) });
    console.log(`  ❌ ${name} — ${String(err?.message ?? err)}`);
  }
}

function assert(cond, message) {
  if (!cond) throw new Error(message);
}

async function main() {
  const dev = flag('--dev');
  const backendPort = await freePort();
  const dataDir = mkdtempSync(join(tmpdir(), 'hd-smoke-data-'));
  const env = {
    ...process.env,
    HORRIBLE_DEV_BACKEND_PORT: String(backendPort),
    HORRIBLE_DEV_HOST: '127.0.0.1',
    HORRIBLE_DATA_DIR: dataDir,
  };
  // Containers and CI runners rarely allow the setuid/namespace sandbox on Linux.
  const linuxArgs = process.platform === 'linux' ? ['--no-sandbox'] : [];

  const launchStarted = Date.now();
  const app = dev
    ? await electron.launch({ args: [DESKTOP, ...linuxArgs], cwd: REPO, env })
    : await electron.launch({
        executablePath: option('--exe') ?? defaultExecutable(),
        args: linuxArgs,
        cwd: REPO,
        env,
      });
  const timings = {};
  const consoleErrors = [];

  try {
    const page = await app.firstWindow();
    timings.windowMs = Date.now() - launchStarted;
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));

    const invoke = (command, args) =>
      page.evaluate(
        async ([c, a]) => {
          try {
            return { ok: true, value: await window.horribleDesktop.invoke(c, a) };
          } catch (error) {
            return { ok: false, error: String(error) };
          }
        },
        [command, args ?? {}],
      );
    const mustInvoke = async (command, args) => {
      const r = await invoke(command, args);
      if (!r.ok) throw new Error(`${command} failed: ${r.error}`);
      return r.value;
    };
    const mustRefuse = async (command, args, pattern) => {
      const r = await invoke(command, args);
      assert(!r.ok, `${command} should have been refused`);
      if (pattern)
        assert(pattern.test(r.error), `${command} refused for the wrong reason: ${r.error}`);
      return r.error;
    };

    console.log(`\nDesktop smoke (${process.platform}, ${dev ? 'dev' : 'packaged'})`);

    await check('page is served from the app origin', async () => {
      // firstWindow() resolves when the BrowserWindow is created, while it still
      // shows about:blank — and that page has already "loaded", so waitForLoadState
      // returns at once. Wait for the UI's own document instead; until then every
      // later check would run against a page with no bridge and a `null` origin.
      await page
        .waitForURL((url) => url.protocol !== 'about:', { timeout: 60_000 })
        .catch(() => {});
      await page.waitForLoadState('domcontentloaded');
      const origin = await page.evaluate(() => location.origin);
      assert(
        dev ? origin.startsWith('http://localhost') : origin === 'app://horrible',
        `origin ${origin}`,
      );
      return origin;
    });

    await check('shell bridge is exposed, and nothing else', async () => {
      const shape = await page.evaluate(() => ({
        bridge: typeof window.horribleDesktop?.invoke,
        platform: window.horribleDesktop?.platform,
        node: typeof window.require,
        process: typeof window.process,
      }));
      assert(shape.bridge === 'function', 'no invoke');
      assert(
        shape.node === 'undefined' && shape.process === 'undefined',
        'Node leaked into the page',
      );
      return shape.platform;
    });

    let origin = null;
    await check('backend becomes ready', async () => {
      const deadline = Date.now() + 180_000;
      for (;;) {
        const status = await mustInvoke('backend_status');
        if (status.state === 'ready') {
          origin = status.origin;
          break;
        }
        assert(status.state === 'starting', `backend ${status.state}: ${status.error}`);
        assert(Date.now() < deadline, 'backend still starting after 180s');
        await new Promise((r) => setTimeout(r, 250));
      }
      timings.backendReadyMs = Date.now() - launchStarted;
      assert(origin === `http://127.0.0.1:${backendPort}`, `unexpected origin ${origin}`);
      return origin;
    });

    await check('UI boots into the shell', async () => {
      await page.waitForSelector('.shell--frame', { timeout: 120_000 });
      timings.uiReadyMs = Date.now() - launchStarted;
      return `${timings.uiReadyMs} ms after launch`;
    });

    await check('page reaches the backend over HTTP (CORS)', async () => {
      const body = await page.evaluate(async (o) => {
        const r = await fetch(`${o}/api/health`);
        return { status: r.status, json: await r.json() };
      }, origin);
      assert(body.status === 200 && body.json.app === 'horrible-dashboard', JSON.stringify(body));
      return body.json.version;
    });

    await check('page opens the backend WebSocket (origin check)', async () => {
      const opened = await page.evaluate(
        (o) =>
          new Promise((res) => {
            const ws = new WebSocket(`${o.replace(/^http/, 'ws')}/ws`);
            const timer = setTimeout(() => res('timeout'), 10_000);
            ws.onopen = () => {
              clearTimeout(timer);
              ws.close();
              res('open');
            };
            ws.onclose = (e) => {
              clearTimeout(timer);
              res(`closed ${e.code}`);
            };
          }),
        origin,
      );
      assert(opened === 'open', opened);
    });

    await check('window controls answer', async () => {
      const fullscreen = await mustInvoke('window_is_fullscreen');
      assert(fullscreen === false, 'starts fullscreen');
      // Maximizing is the window manager's job, and some are asynchronous about it:
      // poll for the state to land. `--no-wm` (a bare Xvfb) has nobody to ask.
      const settle = async (want) => {
        const deadline = Date.now() + 3_000;
        while (Date.now() < deadline) {
          if ((await mustInvoke('window_is_maximized')) === want) return true;
          await new Promise((r) => setTimeout(r, 100));
        }
        return false;
      };
      const before = await mustInvoke('window_is_maximized');
      await mustInvoke('window_toggle_maximize');
      const toggled = await settle(!before);
      await mustInvoke('window_toggle_maximize');
      const restored = await settle(before);
      if (!flag('--no-wm')) assert(toggled && restored, 'maximize did not toggle and restore');
      const zoom = await mustInvoke('window_set_zoom', { factor: 9 });
      assert(zoom === 5, `zoom not clamped: ${zoom}`);
      await mustInvoke('window_set_zoom', { factor: 1 });
      await mustInvoke('window_start_resize_dragging', { direction: 'east' });
      return flag('--no-wm') ? 'maximize not checked (no window manager)' : null;
    });

    await check('fullscreen round-trips', async () => {
      assert((await mustInvoke('window_set_fullscreen', { value: true })) === true, 'enter');
      await new Promise((r) => setTimeout(r, 500));
      assert((await mustInvoke('window_set_fullscreen', { value: false })) === false, 'leave');
    });

    await check('global shortcuts register', async () => {
      const registered = await mustInvoke('shortcuts_register', {
        accelerators: ['mod+shift+f9', 'mod+code:KeyW', 'hyper+k'],
      });
      await mustInvoke('shortcuts_unregister_all');
      // Positional and unknown-modifier specs are dropped, never mis-bound.
      assert(
        !registered.includes('mod+code:KeyW') && !registered.includes('hyper+k'),
        'bad spec bound',
      );
      return registered;
    });

    await check('native browser view loads, reports, and is drivable over CDP', async () => {
      const events = await page.evaluate(() => {
        window.__smokeEvents = [];
        window.horribleDesktop.listen('browser-webview', (e) => window.__smokeEvents.push(e));
        return true;
      });
      assert(events, 'listen');
      const url = `${origin}/api/health`;
      await mustInvoke('create_browser_webview', {
        id: 'smoke',
        url,
        bounds: { x: 40, y: 80, width: 400, height: 300 },
      });
      const deadline = Date.now() + 30_000;
      let loaded = false;
      while (!loaded && Date.now() < deadline) {
        loaded = await page.evaluate(() =>
          window.__smokeEvents.some((e) => e.id === 'smoke' && e.kind === 'load' && !e.loading),
        );
        if (!loaded) await new Promise((r) => setTimeout(r, 200));
      }
      assert(loaded, 'no load event');
      await mustInvoke('update_browser_webview_bounds', {
        id: 'smoke',
        bounds: { x: 0, y: 0, width: 0, height: 0 },
      });
      await mustInvoke('set_browser_webview_visible', { id: 'smoke', visible: false });
      await mustInvoke('set_browser_webview_visible', { id: 'smoke', visible: true });
      const evaluated = await mustInvoke('cdp_browser_webview', {
        id: 'smoke',
        method: 'Runtime.evaluate',
        params: { expression: 'document.body.innerText', returnByValue: true },
      });
      const text = String(evaluated?.result?.value ?? '');
      assert(text.includes('horrible-dashboard'), `page text: ${text.slice(0, 80)}`);
      const ua = await mustInvoke('cdp_browser_webview', {
        id: 'smoke',
        method: 'Runtime.evaluate',
        params: { expression: 'navigator.userAgent', returnByValue: true },
      });
      assert(
        !/Electron/.test(ua.result.value),
        `browser views must send a plain Chrome UA: ${ua.result.value}`,
      );
      await mustInvoke('subscribe_browser_webview_cdp', {
        id: 'smoke',
        event: 'Page.loadEventFired',
      });
      await mustRefuse(
        'cdp_browser_webview',
        { id: 'smoke', method: 'Browser.close' },
        /not allowed/,
      );
      await mustRefuse(
        'cdp_browser_webview',
        { id: 'smoke', method: 'Page.navigate', params: { url: 'file:///etc/passwd' } },
        /non-http/,
      );
      await mustRefuse(
        'subscribe_browser_webview_cdp',
        { id: 'smoke', event: 'Target.attachedToTarget' },
        /not allowed/,
      );
      await mustRefuse(
        'navigate_browser_webview',
        { id: 'smoke', url: 'javascript:alert(1)' },
        /non-http/,
      );
      await mustInvoke('close_browser_webview', { id: 'smoke' });
      await mustRefuse(
        'set_browser_webview_visible',
        { id: 'smoke', visible: true },
        /no native browser webview/,
      );
      await mustInvoke('close_all_browser_webviews');
      return ua.result.value.replace(/^.*(Chrome\/\S+).*$/, '$1');
    });

    await check('guards refuse what they must', async () => {
      await mustRefuse('open_external', { url: 'file:///etc/passwd' }, /non-http/);
      await mustRefuse('browser_open_url', { url: 'app://horrible/index.html' }, /non-http/);
      await mustRefuse('open_path', { path: join(REPO, 'package.json') }, /non-directory/);
      await mustRefuse('no_such_command', {}, /unknown command/);
      await mustRefuse('window_set_fullscreen', { value: 'yes' }, /must be a boolean/);
    });

    await check('updater answers without throwing', async () => {
      const info = await mustInvoke('updater_check', { channel: 'stable' });
      assert(
        typeof info.available === 'boolean' && info.channel === 'stable',
        JSON.stringify(info),
      );
      return info.error ?? (info.available ? `update ${info.version}` : 'up to date');
    });

    await check('app main process is healthy', async () => {
      const metrics = await app.evaluate(({ app: a }) =>
        a
          .getAppMetrics()
          .map((m) => ({ type: m.type, mb: Math.round(m.memory.workingSetSize / 1024) })),
      );
      const total = metrics.reduce((sum, m) => sum + m.mb, 0);
      timings.shellMemoryMb = total;
      return { processes: metrics.length, totalMb: total };
    });

    const relevantErrors = consoleErrors.filter(
      // A plugin or optional module failing to reach a service the runner does not
      // have is not the shell's business; a CSP, CORS or origin refusal is.
      (e) => /CORS|Content Security|origin|refused|horribleDesktop/i.test(e),
    );
    await check('no CORS/origin errors in the page console', async () => {
      assert(relevantErrors.length === 0, relevantErrors.slice(0, 5).join(' | '));
      return `${consoleErrors.length} console errors in total`;
    });

    await check('closing the window shuts the backend down', async () => {
      const closed = app.waitForEvent('close', { timeout: 30_000 });
      await page.evaluate(() => window.horribleDesktop.invoke('window_close'));
      await closed;
      const deadline = Date.now() + 15_000;
      for (;;) {
        const up = await fetch(`${origin}/api/health`).then(
          () => true,
          () => false,
        );
        if (!up) break;
        assert(Date.now() < deadline, 'backend still answering 15s after the app quit (orphaned)');
        await new Promise((r) => setTimeout(r, 250));
      }
      timings.shutdownMs = Date.now() - launchStarted;
    });
  } finally {
    await app.close().catch(() => undefined);
    rmSync(dataDir, { recursive: true, force: true, maxRetries: 5 });
  }

  const failed = results.filter((r) => !r.ok);
  const outDir = join(REPO, 'desktop-smoke-results');
  mkdirSync(outDir, { recursive: true });
  const report = {
    platform: process.platform,
    arch: process.arch,
    timings,
    results,
    consoleErrors,
  };
  writeFileSync(join(outDir, `smoke-${process.platform}.json`), JSON.stringify(report, null, 2));
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`, timings);
  process.exit(failed.length);
}

main().catch((err) => {
  console.error(err);
  process.exit(100);
});
