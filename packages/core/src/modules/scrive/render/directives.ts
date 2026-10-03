/**
 * Helpers for the parts of MyST the raw parse leaves to us.
 *
 * `myst-parser` fully processes the directives it ships (admonitions, figure,
 * dropdown, math, iframe, mermaid, code-cell…). Others — sphinx-design's `tab-set`,
 * `grid` and `card`, and Scrive's own `video` and `r3f` — come back as a bare
 * `mystDirective` with the options still inside `value`. `splitDirectiveBody` does
 * what the parser would have: leading `:key: value` lines are options, one blank
 * line after them is dropped, and the rest is the body.
 */
import { apiUrl } from '../../../origin';

export function splitDirectiveBody(value = ''): { options: Record<string, string>; body: string } {
  const lines = value.split(/\r?\n/);
  const options: Record<string, string> = {};
  let i = 0;
  for (; i < lines.length; i++) {
    const match = /^:([\w-]+):\s*(.*)$/.exec(lines[i]);
    if (!match) break;
    options[match[1]] = match[2];
  }
  if (i > 0 && lines[i] === '') i++;
  return { options, body: lines.slice(i).join('\n') };
}

/**
 * The URL to load an embedded file from. Absolute web URLs pass through; anything
 * else is a path in the site, resolved the way MyST resolves it — relative to the
 * page's own folder, or to the site root when it starts with `/` — and served by
 * `/api/scrive/sites/{site}/asset`.
 */
export function assetUrl(site: string, pagePath: string, url: string): string {
  if (/^(?:https?:)?\/\//i.test(url) || url.startsWith('data:image/')) return url;
  const parts = url.startsWith('/') ? [] : pagePath.split('/').slice(0, -1);
  for (const piece of url.replace(/^\/+/, '').split('/')) {
    if (piece === '..') parts.pop();
    else if (piece && piece !== '.') parts.push(piece);
  }
  return apiUrl(`/api/scrive/sites/${site}/asset?path=${encodeURIComponent(parts.join('/'))}`);
}

/**
 * `sitePath` (relative to the site root) as a path relative to the page — what a
 * page embeds, so the file still resolves when the site is built by Jupyter Book.
 */
export function relativeTo(pagePath: string, sitePath: string): string {
  const from = pagePath.split('/').slice(0, -1);
  const to = sitePath.split('/');
  let shared = 0;
  while (shared < from.length && shared < to.length - 1 && from[shared] === to[shared]) shared++;
  return [...from.slice(shared).map(() => '..'), ...to.slice(shared)].join('/');
}

/**
 * A link a reader may follow: web, mail, in-page anchors and relative paths. Never
 * `javascript:` or `data:`. Pages are often agent-written, and the preview runs on
 * the app's own origin.
 */
export function safeHref(url: unknown): string | undefined {
  if (typeof url !== 'string') return undefined;
  // Browsers ignore tabs/newlines inside a scheme (`java\tscript:`), so strip them
  // before deciding what the scheme is.
  const trimmed = url.replace(/[\t\n\r]/g, '').trim();
  const scheme = /^([a-z][\w+.-]*):/i.exec(trimmed);
  if (!scheme) return trimmed; // relative path or #anchor
  return ['http', 'https', 'mailto'].includes(scheme[1].toLowerCase()) ? trimmed : undefined;
}

/** `https://…` only: an embed of anything else (http, file:, javascript:) is refused. */
export function safeEmbedSrc(url: unknown): string | undefined {
  return typeof url === 'string' && /^https:\/\//i.test(url.trim()) ? url.trim() : undefined;
}

/** A heading's anchor: lowercase words joined by dashes, as MyST and GitHub do. */
export function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .trim()
    .replace(/\s+/g, '-');
}
