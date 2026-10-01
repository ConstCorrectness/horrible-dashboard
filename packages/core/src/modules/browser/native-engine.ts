/**
 * The agent's browser engine for the **native** pane (desktop, `browser.nativeCdp`).
 *
 * The same `BrowserEngine` the headless backend Chromium implements, but driving the
 * real WebView2 surface the human is looking at, over the shell's in-process CDP
 * bridge (apps/desktop/src-tauri/src/browser_cdp.rs). That is the point of it: when
 * the agent clicks, the human sees the click, and can take over mid-task.
 *
 * Page understanding is shared, not reimplemented: `snapshot` and `media` evaluate
 * the backend's own page scripts (fetched once from `/api/browser/scripts`), and
 * `content` posts the live DOM to the same article extractor reader mode uses. So a
 * ref from a native snapshot is numbered exactly as the backend would number it.
 *
 * Input goes through `Input.dispatch*`, not `element.click()`: CDP input events are
 * trusted (`isTrusted === true`), which is what sites that guard against scripted
 * clicks check for.
 *
 * ## Which pane the agent drives
 *
 * Every native tab registers itself here while it has a live webview. The agent
 * drives the most recently *used* one — the tab the human last touched, or the one
 * the agent itself last opened — which is the answer to "the page I'm looking at".
 */
import { windowControl, type BrowserCdpControl, type BrowserCdpEvent } from '../../window';
import { extractArticle, pageScripts } from './api';
import type { BrowserEngine, PageCapture, PageContent, PageMedia, PageSnapshot } from './session';

/** A navigation that never fires `load` (a stuck tracker, a streaming page) must not
 * hang the agent's turn; DOM-ready content is good enough after this long. */
const LOAD_TIMEOUT_MS = 15_000;

// --- target registry --------------------------------------------------------

const targets = new Map<string, number>();
/** Which pane each webview lives in, so the agent can bring it forward. */
const owners = new Map<string, string>();
const targetListeners = new Set<() => void>();
let clock = 0;

/**
 * Make webview `id` (living in pane `paneId`) drivable by the agent until the
 * returned release is called.
 */
export function registerNativeTarget(id: string, paneId: string): () => void {
  targets.set(id, ++clock);
  owners.set(id, paneId);
  targetListeners.forEach((fn) => fn());
  return () => {
    targets.delete(id);
    owners.delete(id);
  };
}

/** The pane instance holding webview `id`. */
export function nativeTargetPane(id: string): string | null {
  return owners.get(id) ?? null;
}

/** Mark webview `id` as the one in use (focused, switched to, opened by the agent). */
export function touchNativeTarget(id: string): void {
  if (targets.has(id)) targets.set(id, ++clock);
}

/** The most recently used native webview, or null when no native tab is open. */
export function activeNativeTarget(): string | null {
  let best: string | null = null;
  let bestAt = -1;
  for (const [id, at] of targets) {
    if (at > bestAt) {
      best = id;
      bestAt = at;
    }
  }
  return best;
}

/**
 * Resolve with the next webview that registers (or the active one, if `fresh` is
 * false and one exists), or null after `timeoutMs`. `browser.open` uses it to hand
 * the agent the pane it just opened rather than racing its mount.
 */
export function waitForNativeTarget(timeoutMs: number, fresh = true): Promise<string | null> {
  const existing = activeNativeTarget();
  if (!fresh && existing) return Promise.resolve(existing);
  const startedAt = clock;
  return new Promise((resolve) => {
    const check = () => {
      const id = activeNativeTarget();
      if (id && (targets.get(id) ?? 0) > startedAt) finish(id);
    };
    const finish = (id: string | null) => {
      clearTimeout(timer);
      targetListeners.delete(check);
      resolve(id);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    targetListeners.add(check);
  });
}

// --- CDP plumbing -----------------------------------------------------------

function cdpControl(): BrowserCdpControl {
  const cdp = windowControl()?.browserWebview?.cdp;
  if (!cdp) throw new Error('This host cannot drive the native browser (no CDP bridge).');
  return cdp;
}

/** Wait for CDP event `method` from webview `id` (subscribing first), or time out. */
async function nextEvent(
  id: string,
  method: string,
  timeoutMs: number,
): Promise<BrowserCdpEvent | null> {
  const cdp = cdpControl();
  await cdp.subscribe(id, method);
  return new Promise((resolve) => {
    const off = cdp.onEvent((event) => {
      if (event.id === id && event.method === method) {
        clearTimeout(timer);
        off();
        resolve(event);
      }
    });
    const timer = setTimeout(() => {
      off();
      resolve(null);
    }, timeoutMs);
  });
}

interface EvaluateResult {
  result?: { value?: unknown };
  exceptionDetails?: { text?: string; exception?: { description?: string } };
}

/** Evaluate `expression` in the page and return its JSON value. */
async function evaluate<T>(id: string, expression: string): Promise<T> {
  const reply = await cdpControl().call<EvaluateResult>(id, 'Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (reply.exceptionDetails) {
    const d = reply.exceptionDetails;
    throw new Error(d.exception?.description || d.text || 'page script failed');
  }
  return reply.result?.value as T;
}

let scripts: Promise<Record<string, string>> | null = null;
function pageScript(name: 'snapshot' | 'media'): Promise<string> {
  if (!scripts) {
    scripts = pageScripts()
      .then((r) => r.scripts)
      .catch((e) => {
        scripts = null; // a backend that was still booting must not poison the cache
        throw e;
      });
  }
  return scripts.then((all) => {
    const src = all[name];
    if (!src) throw new Error(`backend has no "${name}" page script`);
    return src;
  });
}

/** Call one of the shared page scripts (each is a single arrow function). */
async function runScript<T>(id: string, name: 'snapshot' | 'media'): Promise<T> {
  const src = (await pageScript(name)).trim().replace(/;$/, '');
  return evaluate<T>(id, `(${src})()`);
}

/** Viewport centre of the element tagged with `ref`, scrolled into view first. */
async function refPoint(id: string, ref: number): Promise<{ x: number; y: number }> {
  const point = await evaluate<{ x: number; y: number } | null>(
    id,
    `(() => {
      const el = document.querySelector('[data-agent-ref="${Math.trunc(ref)}"]');
      if (!el) return null;
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    })()`,
  );
  if (!point) {
    throw new Error(`no element with ref ${ref} — the page changed; call browser.snapshot again`);
  }
  return point;
}

async function click(id: string, x: number, y: number): Promise<void> {
  const cdp = cdpControl();
  await cdp.call(id, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  for (const type of ['mousePressed', 'mouseReleased']) {
    await cdp.call(id, 'Input.dispatchMouseEvent', {
      type,
      x,
      y,
      button: 'left',
      buttons: type === 'mousePressed' ? 1 : 0,
      clickCount: 1,
    });
  }
}

/**
 * Key names the agent uses (Playwright's) → what `Input.dispatchKeyEvent` needs.
 * `text` matters for Enter: without it the keydown carries no character and a form
 * never submits.
 */
const KEYS: Record<string, { code: string; keyCode: number; text?: string }> = {
  Enter: { code: 'Enter', keyCode: 13, text: '\r' },
  Tab: { code: 'Tab', keyCode: 9 },
  Escape: { code: 'Escape', keyCode: 27 },
  Backspace: { code: 'Backspace', keyCode: 8 },
  Delete: { code: 'Delete', keyCode: 46 },
  Space: { code: 'Space', keyCode: 32, text: ' ' },
  ArrowUp: { code: 'ArrowUp', keyCode: 38 },
  ArrowDown: { code: 'ArrowDown', keyCode: 40 },
  ArrowLeft: { code: 'ArrowLeft', keyCode: 37 },
  ArrowRight: { code: 'ArrowRight', keyCode: 39 },
  PageUp: { code: 'PageUp', keyCode: 33 },
  PageDown: { code: 'PageDown', keyCode: 34 },
  Home: { code: 'Home', keyCode: 36 },
  End: { code: 'End', keyCode: 35 },
};

export async function pressKey(id: string, key: string): Promise<void> {
  const named = KEYS[key] ?? (key === ' ' ? KEYS.Space : undefined);
  const single = !named && key.length === 1;
  if (!named && !single) throw new Error(`unknown key "${key}"`);
  const base = named
    ? { key: key === ' ' ? ' ' : key, code: named.code, windowsVirtualKeyCode: named.keyCode }
    : { key, code: '', windowsVirtualKeyCode: key.toUpperCase().charCodeAt(0) };
  const text = named ? named.text : key;
  const cdp = cdpControl();
  await cdp.call(id, 'Input.dispatchKeyEvent', {
    ...base,
    type: text ? 'keyDown' : 'rawKeyDown',
    ...(text ? { text, unmodifiedText: text } : {}),
  });
  await cdp.call(id, 'Input.dispatchKeyEvent', { ...base, type: 'keyUp' });
}

interface NavigationHistory {
  currentIndex: number;
  entries: { id: number; url: string; title: string }[];
}

/** The tab's real back/forward list — the native pane's toolbar reads this. */
export function navigationHistory(id: string): Promise<NavigationHistory> {
  return cdpControl().call<NavigationHistory>(id, 'Page.getNavigationHistory');
}

/** Step `delta` entries through the tab's history (-1 back, +1 forward). */
export async function goHistory(id: string, delta: number): Promise<void> {
  const history = await navigationHistory(id);
  const entry = history.entries[history.currentIndex + delta];
  if (!entry) return;
  await cdpControl().call(id, 'Page.navigateToHistoryEntry', { entryId: entry.id });
}

/** A JPEG of what the tab shows right now, as a data URI. */
export async function captureFrame(id: string, quality = 70): Promise<string> {
  const shot = await cdpControl().call<{ data: string }>(id, 'Page.captureScreenshot', {
    format: 'jpeg',
    quality,
  });
  return `data:image/jpeg;base64,${shot.data}`;
}

// --- the engine -------------------------------------------------------------

/** A `BrowserEngine` bound to native webview `id`. */
export function nativeEngine(id: string): BrowserEngine {
  return {
    kind: 'native',

    async navigate(url) {
      const cdp = cdpControl();
      await cdp.call(id, 'Page.enable');
      const loaded = nextEvent(id, 'Page.loadEventFired', LOAD_TIMEOUT_MS);
      const nav = await cdp.call<{ errorText?: string }>(id, 'Page.navigate', { url });
      if (nav.errorText) throw new Error(`navigation failed: ${nav.errorText}`);
      await loaded;
      touchNativeTarget(id);
      return null;
    },

    async content(): Promise<PageContent> {
      const page = await evaluate<{ url: string; title: string; html: string }>(
        id,
        `({ url: location.href, title: document.title, html: document.documentElement.outerHTML })`,
      );
      const article = await extractArticle(page.url, page.html, page.title);
      return { url: page.url, title: article.title, author: article.author, text: article.text };
    },

    capture(): Promise<PageCapture> {
      // The archive op re-fetches every subresource with the *backend* session's
      // cookies — it has nothing to fetch with here. capturePage() falls back to
      // a text ingest, which is the right degradation.
      return Promise.reject(new Error('page archive is a backend-engine feature'));
    },

    snapshot: () => runScript<PageSnapshot>(id, 'snapshot'),

    media: () => runScript<PageMedia>(id, 'media'),

    async scrape(selector) {
      const items = await evaluate<unknown[]>(
        id,
        `Array.from(document.querySelectorAll(${JSON.stringify(selector)})).slice(0, 200).map((el) => ({
          text: (el.innerText || el.textContent || '').trim().slice(0, 500),
          href: el.getAttribute('href') || null,
          html: el.outerHTML.slice(0, 1000),
        }))`,
      );
      return { selector, count: items.length, items };
    },

    async screenshot() {
      return { frame: await captureFrame(id) };
    },

    async clickRef(ref) {
      const { x, y } = await refPoint(id, ref);
      await click(id, x, y);
      touchNativeTarget(id);
      return null;
    },

    async typeRef(ref, text) {
      // Focus it the way a person would, then select what's there so the typed
      // text replaces it — the semantics of Playwright's `fill`, which the backend
      // engine uses, so `browser.type` means the same thing on both.
      const { x, y } = await refPoint(id, ref);
      await click(id, x, y);
      await evaluate(
        id,
        `(() => {
          const el = document.querySelector('[data-agent-ref="${Math.trunc(ref)}"]');
          if (!el) return;
          if (typeof el.select === 'function') el.select();
          else if (el.isContentEditable) document.getSelection()?.selectAllChildren(el);
        })()`,
      );
      await cdpControl().call(id, 'Input.insertText', { text });
      touchNativeTarget(id);
      return null;
    },

    press: (key) => pressKey(id, key),

    async scroll(dy) {
      const centre = await evaluate<{ x: number; y: number }>(
        id,
        `({ x: innerWidth / 2, y: innerHeight / 2 })`,
      );
      await cdpControl().call(id, 'Input.dispatchMouseEvent', {
        type: 'mouseWheel',
        x: centre.x,
        y: centre.y,
        deltaX: 0,
        deltaY: dy,
      });
      return null;
    },

    back: () => goHistory(id, -1),

    info: () =>
      evaluate<{ url: string; title: string }>(
        id,
        `({ url: location.href, title: document.title })`,
      ),
  };
}
