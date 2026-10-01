/**
 * Agent tools for the browser module — how the agent orchestrator *uses* the
 * browser. Declared on the `browser.view` panel, so they're in the capability
 * manifest whenever the module is registered (the handlers run frontend-side; see
 * modules/agent/manifest.ts). Parameterized actions must be agentTools, not
 * commands — agent-exposed commands ignore their args today.
 *
 * Which browser they drive is decided per call (engines.ts):
 *
 * - **The native pane** (desktop, `browser.nativeCdp`). The real browser the human
 *   is looking at; the agent's clicks happen in front of them, and they can take
 *   over at any point. This is the default on Windows.
 * - **The backend's headless Chromium** (`HORRIBLE_ENABLE_SERVER_BROWSER=1`), when
 *   no native pane is open — the web build, or reading a page in the background.
 * - **Neither**: `browser.read` still works as an SSRF-guarded server fetch.
 *
 * The loop the tools are shaped for is the one real browsing agents use: read the
 * page as a numbered list of interactable elements (`browser.snapshot`), act on one
 * by ref, and get the *new* snapshot back in the same result — so the agent never
 * acts on refs from a page that has since changed, and never pays a round trip just
 * to look again.
 *
 * **Remembering.** `browser.media` lists the page's images/videos and `browser.save`
 * files the page or its media into a knowledge library — the write half of RAG,
 * where `library.search` is the read half.
 */
import type { AgentToolDecl } from '@horribledashboard/sdk';

import { hasCapability } from '../../capabilities';
import { openDocument } from '../../layout/controller';
import { windowControl } from '../../window';
import { readerMode } from './api';
import {
  captureAllMedia,
  capturePage,
  captureMedia,
  isDescribed,
  isSavable,
  pageMedia,
} from './capture';
import { agentEngine, revealNativeTarget } from './engines';
import { activeNativeTarget, nativeEngine, waitForNativeTarget } from './native-engine';
import type { BrowserEngine, MediaItem, PageSnapshot, SnapshotElement } from './session';

// Cap the text handed back to the model so one page can't blow the context window.
const MAX_TEXT = 8000;

/** How long `browser.open` waits for a freshly opened native pane to load. */
const OPEN_TIMEOUT_MS = 15_000;

const clip = (text: string) =>
  text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}… [truncated]` : text;

const NO_BROWSER = {
  error:
    'No live browser is open. Call browser.open with a URL first (it opens a browser ' +
    'pane the user can watch). browser.read still works on any URL without one.',
};

const CHALLENGE_NOTE =
  'This page is showing a CAPTCHA or bot check. Do not try to solve it: tell the user ' +
  'to complete it in the browser pane, then continue once they say it is done.';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The snapshot shape returned to the model (coordinates are noise to it). */
function summarize(snap: PageSnapshot) {
  return {
    url: snap.url,
    title: snap.title,
    elements: snap.elements.map((e: SnapshotElement) => ({
      ref: e.ref,
      role: e.role,
      name: e.name,
      value: e.value,
    })),
    ...(snap.challenge ? { challenge: true, note: CHALLENGE_NOTE } : {}),
  };
}

/**
 * The page as it stands after an action. A click that navigates tears the old
 * document down mid-evaluate, so a failed first look gets one more try once the
 * new page has had a moment to exist.
 */
async function settledSnapshot(eng: BrowserEngine) {
  await sleep(400);
  try {
    return summarize(await eng.snapshot());
  } catch {
    await sleep(1200);
    return summarize(await eng.snapshot());
  }
}

/** Run an action on the current engine and return the page it leaves behind. */
async function act(run: (eng: BrowserEngine) => Promise<unknown>) {
  const eng = await agentEngine();
  if (!eng) return NO_BROWSER;
  await run(eng);
  return { ok: true, page: await settledSnapshot(eng) };
}

/** Resolve once native webview `id` reports a finished load, or after `ms`. */
function waitForLoad(id: string, ms: number): Promise<void> {
  const control = windowControl()?.browserWebview;
  if (!control) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      off();
      resolve();
    };
    const off = control.onEvent((e) => {
      if (e.kind === 'load' && e.id === id && !e.loading) finish();
    });
    const timer = setTimeout(finish, ms);
  });
}

export const browserAgentTools: AgentToolDecl[] = [
  {
    name: 'browser.read',
    description:
      'Read a web page and return its readable text (title + main content). Navigates the live browser (the open browser pane, if any) to the URL first; with no live browser, does a server-side fetch. Omit url to read the page already open.',
    params: {
      type: 'object',
      properties: {
        url: {
          type: 'string',
          description: 'The http(s) URL to read (optional when a page is open)',
        },
      },
    },
    sideEffect: false,
    handler: async (args) => {
      const url = args.url ? String(args.url) : '';
      const eng = await agentEngine();
      if (eng) {
        if (url) await eng.navigate(url);
        const c = await eng.content();
        return { url: c.url, title: c.title, author: c.author, text: clip(c.text) };
      }
      if (!url) return { error: 'browser.read needs a url when no browser is open.' };
      const article = await readerMode(url);
      return {
        url: article.url,
        title: article.title,
        author: article.author,
        text: clip(article.text),
      };
    },
  },
  {
    name: 'browser.open',
    description:
      'Open a web page in the browser pane so the user can see it, and make it the page the other browser tools act on. Reuses the open browser pane if there is one. Returns once the page has loaded — then use browser.snapshot to see what is on it.',
    params: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'The http(s) URL to open' },
      },
      required: ['url'],
    },
    sideEffect: true,
    specifierTemplate: '{url}',
    handler: async (args) => {
      const url = String(args.url);
      if (hasCapability('browser.nativeCdp')) {
        // A native pane is already open: drive it in place, and bring it forward
        // so the user sees what the agent is doing.
        const existing = activeNativeTarget();
        if (existing) {
          revealNativeTarget(existing);
          await nativeEngine(existing).navigate(url);
          return { ok: true, url };
        }
        openDocument('browser.view', `browser.view:${url}`, { url }, () => true);
        const id = await waitForNativeTarget(OPEN_TIMEOUT_MS);
        if (id) await waitForLoad(id, OPEN_TIMEOUT_MS);
        return { ok: true, url, ready: Boolean(id) };
      }
      // Reuse an open browser pane rather than splitting a new one per
      // navigation — the agent browsing five pages is one session, not five panes.
      openDocument('browser.view', `browser.view:${url}`, { url }, () => true);
      return { ok: true, url };
    },
  },
  {
    name: 'browser.snapshot',
    description:
      'List the interactable elements of the open page (each with a numeric ref, role, accessible name, and value) so you can decide what to click or type into. Use the ref with browser.click / browser.type. Only on-screen elements are listed — scroll to see more.',
    params: { type: 'object', properties: {} },
    sideEffect: false,
    handler: async () => {
      const eng = await agentEngine();
      if (!eng) return NO_BROWSER;
      return summarize(await eng.snapshot());
    },
  },
  {
    name: 'browser.click',
    description:
      'Click an element on the open page by its ref (from browser.snapshot). Returns the page as it looks afterwards, with fresh refs — refs from earlier snapshots are no longer valid.',
    params: {
      type: 'object',
      properties: { ref: { type: 'number', description: 'element ref from browser.snapshot' } },
      required: ['ref'],
    },
    sideEffect: true,
    specifierTemplate: 'ref {ref}',
    handler: (args) => act((eng) => eng.clickRef(Number(args.ref))),
  },
  {
    name: 'browser.type',
    description:
      'Type text into an input on the open page by its ref (from browser.snapshot), replacing what is there. Set submit to press Enter afterwards (searches, forms). Returns the page as it looks afterwards, with fresh refs.',
    params: {
      type: 'object',
      properties: {
        ref: { type: 'number', description: 'element ref from browser.snapshot' },
        text: { type: 'string', description: 'text to type' },
        submit: { type: 'boolean', description: 'press Enter after typing' },
      },
      required: ['ref', 'text'],
    },
    sideEffect: true,
    specifierTemplate: '{text} → ref {ref}',
    handler: (args) =>
      act(async (eng) => {
        await eng.typeRef(Number(args.ref), String(args.text));
        if (args.submit) await eng.press('Enter');
      }),
  },
  {
    name: 'browser.press',
    description:
      'Press one key on the open page, on whatever has focus: Enter, Tab, Escape, Backspace, ArrowUp/Down/Left/Right, PageUp/PageDown, Home, End, or a single character. Returns the page afterwards.',
    params: {
      type: 'object',
      properties: { key: { type: 'string', description: 'the key to press, e.g. "Enter"' } },
      required: ['key'],
    },
    sideEffect: true,
    specifierTemplate: '{key}',
    handler: (args) => act((eng) => eng.press(String(args.key))),
  },
  {
    name: 'browser.scroll',
    description:
      'Scroll the open page up or down to reveal more of it (snapshot only lists what is on screen). Returns the page afterwards.',
    params: {
      type: 'object',
      properties: {
        direction: { type: 'string', enum: ['down', 'up'], description: 'which way to scroll' },
        screens: { type: 'number', description: 'how far, in screen-heights (default 1)' },
      },
      required: ['direction'],
    },
    sideEffect: false,
    handler: (args) => {
      const screens = Math.min(Math.max(Number(args.screens) || 1, 0.25), 10);
      const sign = args.direction === 'up' ? -1 : 1;
      // About one viewport per screen; the page's real height is not ours to know.
      return act((eng) => eng.scroll(sign * screens * 700));
    },
  },
  {
    name: 'browser.back',
    description: 'Go back one page in the open browser history. Returns the page afterwards.',
    params: { type: 'object', properties: {} },
    sideEffect: true,
    handler: () => act((eng) => eng.back()),
  },
  {
    name: 'browser.scrape',
    description:
      'Scrape structured data from the live page by CSS selector — returns each matching element’s text, href, and outerHTML (capped). Requires an open page (browser.open).',
    params: {
      type: 'object',
      properties: {
        selector: { type: 'string', description: 'CSS selector, e.g. "article h2 a"' },
      },
      required: ['selector'],
    },
    sideEffect: false,
    handler: async (args) => {
      const eng = await agentEngine();
      if (!eng) return NO_BROWSER;
      return eng.scrape(String(args.selector));
    },
  },
  {
    name: 'browser.media',
    description:
      'List the images and videos on the open page — each with its src, alt text, caption, and surrounding context. Use this to see what is available to save before calling browser.save. Requires an open page (browser.open).',
    params: { type: 'object', properties: {} },
    sideEffect: false,
    handler: async () => {
      if (!(await agentEngine())) return NO_BROWSER;
      const media = await pageMedia();
      // `savable` tells the model which items browser.save would actually accept, so
      // it doesn't try to save a decorative image and get an error. It's not the same
      // as `described`: with CLIP on, an undescribed image is savable via its pixels.
      const savableFlags = await Promise.all(
        [...media.images, ...media.videos].map((m) => isSavable(m)),
      );
      const savable = new Map(
        [...media.images, ...media.videos].map((m, i) => [m.src, savableFlags[i]]),
      );
      const summarize = (items: MediaItem[]) =>
        items.map((m) => ({
          src: m.src,
          alt: m.alt,
          caption: m.context?.[0] ?? '',
          width: m.width,
          height: m.height,
          described: isDescribed(m),
          savable: savable.get(m.src) ?? false,
        }));
      return {
        url: media.url,
        title: media.title,
        images: summarize(media.images),
        videos: summarize(media.videos),
      };
    },
  },
  {
    name: 'browser.save',
    description:
      'Save what is on the open page into a knowledge library so it can be semantically searched later with library.search. Use target "page" to save the article text, "media" to save one image/video by its src (from browser.media), or "allMedia" to save every described image/video on the page. Requires an open page (browser.open).',
    params: {
      type: 'object',
      properties: {
        target: {
          type: 'string',
          enum: ['page', 'media', 'allMedia'],
          description: 'What to save: the page text, one media item, or all media',
        },
        src: {
          type: 'string',
          description: 'For target "media": the src of the image/video (from browser.media)',
        },
        library: { type: 'string', description: 'Library to save into (default: "default")' },
        tags: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional tags to file the source under',
        },
      },
      required: ['target'],
    },
    sideEffect: true,
    specifierTemplate: '{target}',
    handler: async (args) => {
      if (!(await agentEngine())) return NO_BROWSER;
      const opts = {
        library: args.library ? String(args.library) : undefined,
        tags: Array.isArray(args.tags) ? args.tags.map(String) : undefined,
      };
      const target = String(args.target);

      if (target === 'page') {
        const source = await capturePage(opts);
        return { ok: true, saved: 'page', id: source.id, title: source.title };
      }
      if (target === 'allMedia') {
        const { saved, skipped } = await captureAllMedia(opts);
        return {
          ok: true,
          saved: saved.length,
          skipped,
          note: skipped
            ? `${skipped} item(s) had no alt text, caption, or heading to embed, so they were skipped — nothing could match them in a search.`
            : undefined,
        };
      }
      if (target !== 'media') {
        return { error: `unknown target "${target}" — use page, media, or allMedia.` };
      }

      const src = args.src ? String(args.src) : '';
      if (!src) return { error: 'browser.save with target "media" needs a src.' };
      const media = await pageMedia();
      const item = [...media.images, ...media.videos].find((m) => m.src === src);
      if (!item) {
        return { error: `no image or video with src "${src}" on this page — call browser.media.` };
      }
      if (!(await isSavable(item))) {
        return {
          error:
            'That media has no alt text, caption, or nearby heading. Without CLIP visual ' +
            'search it is embedded only via the text describing it, so there is nothing to ' +
            'index — saving it would make it unfindable. Enable the library.clipEnabled ' +
            'setting (and the `clip` extra) to index media by appearance instead.',
        };
      }
      const source = await captureMedia(item, media.url, opts);
      return { ok: true, saved: 'media', id: source.id, title: source.title, src };
    },
  },
];
