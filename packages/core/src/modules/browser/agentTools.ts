/**
 * Agent tools for the browser module — how the agent orchestrator *uses* the
 * browser. Declared on the `browser.view` panel, so they're in the capability
 * manifest whenever the module is registered (the handlers run frontend-side; see
 * modules/agent/manifest.ts). Parameterized actions must be agentTools, not
 * commands — agent-exposed commands ignore their args today.
 *
 * Two tiers, chosen automatically by whether the real backend engine is enabled
 * (`HORRIBLE_ENABLE_SERVER_BROWSER=1`, reported by `/api/browser/engine`):
 *
 * - **Light (always available).** `browser.read` fetches a URL server-side and returns
 *   its readable text (SSRF-guarded `/api/browser/read`); `browser.open` shows a page in
 *   the UI.
 * - **Full engine (when on).** The same live Chromium the human panel drives becomes the
 *   agent's browser (one WS connection ⇒ one shared session). `browser.read` navigates
 *   the live page and reads its extracted content; `browser.snapshot` returns the
 *   interactable elements (ref + role + name) so the agent can `browser.click`/
 *   `browser.type` by ref; `browser.scrape` pulls structured data by CSS selector.
 */
import type { AgentToolDecl } from '@horribledashboard/sdk';

import { registry } from '../../registry';
import { engineStatus, readerMode } from './api';
import { engine, type SnapshotElement } from './session';

// Cap the text handed back to the model so one page can't blow the context window.
const MAX_TEXT = 8000;

// The engine gate is process-wide and rarely flips within a session; cache the probe.
let engineProbe: Promise<boolean> | null = null;
function engineEnabled(): Promise<boolean> {
  if (!engineProbe) {
    engineProbe = engineStatus()
      .then((s) => s.enabled)
      .catch(() => false);
  }
  return engineProbe;
}

const clip = (text: string) =>
  text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}… [truncated]` : text;

const ENGINE_OFF = {
  error:
    'The full browser engine is off. Enable it (HORRIBLE_ENABLE_SERVER_BROWSER=1 + the ' +
    'browser-engine extra) to snapshot/scrape/click live pages. You can still use ' +
    'browser.read to fetch a page’s text.',
};

export const browserAgentTools: AgentToolDecl[] = [
  {
    name: 'browser.read',
    description:
      'Read a web page and return its readable text (title + main content). With the full engine on, navigates the live shared browser to the URL first; otherwise does a server-side SSRF-guarded fetch. Accepts any http(s) URL; omit url to read the page already open in the engine.',
    params: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'The http(s) URL to read (optional in full mode)' },
      },
    },
    sideEffect: false,
    handler: async (args) => {
      const url = args.url ? String(args.url) : '';
      if (await engineEnabled()) {
        if (url) await engine.navigate(url);
        const c = await engine.content();
        return { url: c.url, title: c.title, author: c.author, text: clip(c.text) };
      }
      if (!url) return { error: 'browser.read needs a url unless the full engine is on.' };
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
      'Open a web page in an embedded browser pane in the UI so the user can see it. Returns immediately — call browser.read to get the page contents.',
    params: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'The http(s) URL to open' },
      },
      required: ['url'],
    },
    sideEffect: true,
    specifierTemplate: '{url}',
    handler: (args) => {
      const url = String(args.url);
      registry.openPanel('browser.view', { params: { url } });
      return { ok: true, url };
    },
  },
  {
    name: 'browser.snapshot',
    description:
      'Return the interactable elements of the live page (each with a numeric ref, role, accessible name, and value) so you can decide what to click or type into. Requires the full browser engine. Use the ref with browser.click / browser.type.',
    params: { type: 'object', properties: {} },
    sideEffect: false,
    handler: async () => {
      if (!(await engineEnabled())) return ENGINE_OFF;
      const snap = await engine.snapshot();
      return {
        url: snap.url,
        title: snap.title,
        elements: snap.elements.map((e: SnapshotElement) => ({
          ref: e.ref,
          role: e.role,
          name: e.name,
          value: e.value,
        })),
      };
    },
  },
  {
    name: 'browser.click',
    description:
      'Click an element on the live page by its ref (from browser.snapshot). Requires the full browser engine.',
    params: {
      type: 'object',
      properties: { ref: { type: 'number', description: 'element ref from browser.snapshot' } },
      required: ['ref'],
    },
    sideEffect: true,
    specifierTemplate: 'ref {ref}',
    handler: async (args) => {
      if (!(await engineEnabled())) return ENGINE_OFF;
      await engine.clickRef(Number(args.ref));
      return { ok: true, ref: Number(args.ref) };
    },
  },
  {
    name: 'browser.type',
    description:
      'Type text into an input element on the live page by its ref (from browser.snapshot). Requires the full browser engine.',
    params: {
      type: 'object',
      properties: {
        ref: { type: 'number', description: 'element ref from browser.snapshot' },
        text: { type: 'string', description: 'text to type' },
      },
      required: ['ref', 'text'],
    },
    sideEffect: true,
    specifierTemplate: '{text} → ref {ref}',
    handler: async (args) => {
      if (!(await engineEnabled())) return ENGINE_OFF;
      await engine.typeRef(Number(args.ref), String(args.text));
      return { ok: true, ref: Number(args.ref) };
    },
  },
  {
    name: 'browser.scrape',
    description:
      'Scrape structured data from the live page by CSS selector — returns each matching element’s text, href, and outerHTML (capped). Requires the full browser engine.',
    params: {
      type: 'object',
      properties: {
        selector: { type: 'string', description: 'CSS selector, e.g. "article h2 a"' },
      },
      required: ['selector'],
    },
    sideEffect: false,
    handler: async (args) => {
      if (!(await engineEnabled())) return ENGINE_OFF;
      return engine.scrape(String(args.selector));
    },
  },
];
