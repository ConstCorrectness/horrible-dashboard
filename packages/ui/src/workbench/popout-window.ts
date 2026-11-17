/**
 * Opening a pop-out window and making its document look like this one.
 *
 * The window is `about:blank` under a `horrible-popout:<id>` frame name — the only
 * shape the desktop shell lets the page open (apps/desktop/src/popouts.ts). This
 * page renders into it; nothing loads there. So everything the main document has
 * that styles a pane must be carried over, and kept carried over:
 *
 * - `<link rel=stylesheet>` and `<style>` in `<head>` — cloned, and re-cloned when
 *   the head changes (Vite's dev server swaps `<style>` text on every save).
 * - Attributes on `<html>` and `<body>` — the theme (`data-theme`, motion, density)
 *   and inline custom properties live there.
 * - **Constructed stylesheets** (`document.adoptedStyleSheets`). CodeMirror mounts
 *   its styles this way, into the document the editor was built in. A constructed
 *   sheet cannot be adopted by a second document, so its rules are copied as text;
 *   nothing announces a change to one, so they are re-read on a slow tick.
 *
 * Windows are kept by id for a moment after release: a React remount (StrictMode,
 * or a workspace switch that keeps the same pop-out id) takes the same window back
 * instead of closing one and opening another in its place.
 */
import type { PopoutBounds } from '@horrible/core';

export const POPOUT_FRAME_PREFIX = 'horrible-popout:';

/** How long a released window waits for a remount before it really closes. */
const CLOSE_GRACE_MS = 150;
/** How often constructed stylesheets are re-read. */
const ADOPTED_SHEETS_POLL_MS = 1000;

export interface PopoutHandle {
  win: Window;
  /** The element React renders the group into. */
  root: HTMLElement;
}

interface Entry extends PopoutHandle {
  stopMirror: () => void;
  closeTimer: ReturnType<typeof setTimeout> | null;
}

const entries = new Map<string, Entry>();

function features(bounds: PopoutBounds | null): string {
  if (!bounds) return 'width=960,height=680';
  const { x, y, w, h } = bounds;
  return `left=${Math.round(x)},top=${Math.round(y)},width=${Math.round(w)},height=${Math.round(h)}`;
}

/**
 * The window for pop-out `id`, opening it if needed. Null when the shell refused
 * (no `window.multi`, or a blocked open) — the caller docks the panes back.
 */
export function acquirePopoutWindow(id: string, bounds: PopoutBounds | null): PopoutHandle | null {
  const kept = entries.get(id);
  if (kept && !kept.win.closed) {
    if (kept.closeTimer) clearTimeout(kept.closeTimer);
    kept.closeTimer = null;
    return kept;
  }
  entries.delete(id);

  const win = window.open('', `${POPOUT_FRAME_PREFIX}${id}`, features(bounds));
  if (!win) return null;
  const doc = win.document;
  doc.open();
  doc.write('<!doctype html><html><head><meta charset="utf-8"></head><body></body></html>');
  doc.close();
  // Relative URLs in copied CSS (fonts, images) resolve against the app, not
  // against `about:blank`.
  const base = doc.createElement('base');
  base.href = document.baseURI;
  doc.head.appendChild(base);

  const root = doc.createElement('div');
  root.id = 'popout-root';
  doc.body.appendChild(root);

  const entry: Entry = { win, root, stopMirror: mirrorDocument(document, doc), closeTimer: null };
  entries.set(id, entry);
  return entry;
}

/** Let go of pop-out `id`'s window; it closes unless re-acquired at once. */
export function releasePopoutWindow(id: string): void {
  const entry = entries.get(id);
  if (!entry) return;
  if (entry.closeTimer) clearTimeout(entry.closeTimer);
  entry.closeTimer = setTimeout(() => {
    entries.delete(id);
    entry.stopMirror();
    if (!entry.win.closed) entry.win.close();
  }, CLOSE_GRACE_MS);
}

// --- mirroring ----------------------------------------------------------------

function copyAttributes(from: Element, to: Element): void {
  for (const attr of [...to.attributes]) {
    if (!from.hasAttribute(attr.name)) to.removeAttribute(attr.name);
  }
  for (const attr of [...from.attributes]) {
    if (to.getAttribute(attr.name) !== attr.value) to.setAttribute(attr.name, attr.value);
  }
}

const MIRRORED = 'data-popout-mirror';

function syncHead(from: Document, to: Document): void {
  for (const el of [...to.head.querySelectorAll(`[${MIRRORED}]`)]) el.remove();
  for (const el of from.head.querySelectorAll('link[rel~="stylesheet"], style')) {
    const clone = to.importNode(el, true) as HTMLElement;
    clone.setAttribute(MIRRORED, '');
    to.head.appendChild(clone);
  }
}

function adoptedSignature(doc: Document): string {
  return doc.adoptedStyleSheets.map((s) => s.cssRules.length).join(',');
}

function syncAdopted(from: Document, to: Document): void {
  let el = to.head.querySelector<HTMLStyleElement>('style[data-popout-adopted]');
  if (!el) {
    el = to.createElement('style');
    el.setAttribute('data-popout-adopted', '');
    to.head.appendChild(el);
  }
  el.textContent = from.adoptedStyleSheets
    .flatMap((sheet) => [...sheet.cssRules].map((rule) => rule.cssText))
    .join('\n');
}

/** Keep `to` styled like `from` until the returned stop is called. */
export function mirrorDocument(from: Document, to: Document): () => void {
  copyAttributes(from.documentElement, to.documentElement);
  copyAttributes(from.body, to.body);
  syncHead(from, to);
  syncAdopted(from, to);

  let headQueued = false;
  const headObserver = new MutationObserver(() => {
    if (headQueued) return;
    headQueued = true;
    queueMicrotask(() => {
      headQueued = false;
      syncHead(from, to);
    });
  });
  headObserver.observe(from.head, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
  });
  const rootObserver = new MutationObserver(() => {
    copyAttributes(from.documentElement, to.documentElement);
    copyAttributes(from.body, to.body);
  });
  rootObserver.observe(from.documentElement, { attributes: true });
  rootObserver.observe(from.body, { attributes: true });

  let signature = adoptedSignature(from);
  const poll = setInterval(() => {
    const next = adoptedSignature(from);
    if (next === signature) return;
    signature = next;
    syncAdopted(from, to);
  }, ADOPTED_SHEETS_POLL_MS);

  return () => {
    headObserver.disconnect();
    rootObserver.disconnect();
    clearInterval(poll);
  };
}
