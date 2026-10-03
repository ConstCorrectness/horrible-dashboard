/**
 * Where each page of a site lands in the published site, and how one file links to
 * another.
 *
 * Every link the build writes is **relative**, so the site works wherever it is
 * served: `you.github.io/blog/`, a custom domain's root, or the local preview under
 * `/api/scrive/sites/<id>/built/`. Only what must be absolute (a feed's links, a
 * share card's URL, the 404 page, which is served at any path) uses `SITE_URL`, which
 * the backend fills in at push time.
 *
 * Pages get folder URLs: `posts/2026-10-01-priors.md` → `posts/2026-10-01-priors/`
 * (written as `…/index.html`). `index.md` at any level is that folder's own page.
 */

/** Replaced by the real site URL (with its trailing slash) when the site is pushed.
 * Must match `SITE_URL` in backend/modules/scrive/publish.py. */
export const SITE_URL = '__SCRIVE_SITE_URL__/';

const PAGE_EXT = /\.(md|ipynb)$/i;

/** A page's folder in the published site: `posts/x.md` → `posts/x/`, `index.md` → ``. */
export function pageDir(pagePath: string): string {
  const stem = pagePath.replace(PAGE_EXT, '');
  if (stem === 'index') return '';
  if (stem.endsWith('/index')) return stem.slice(0, -'index'.length);
  return `${stem}/`;
}

/** The file a page is written to: `posts/x.md` → `posts/x/index.html`. */
export function outputPath(pagePath: string): string {
  return `${pageDir(pagePath)}index.html`;
}

/**
 * A URL as written in a page (relative to the page's own folder, or `/`-rooted at
 * the site) → a path in the site, or `null` when it climbs out of the site.
 * Query and fragment are not part of the path.
 */
export function sitePath(pagePath: string, url: string): string | null {
  const clean = url.split(/[?#]/, 1)[0];
  const parts = clean.startsWith('/') ? [] : pagePath.split('/').slice(0, -1);
  for (const piece of clean.replace(/^\/+/, '').split('/')) {
    if (piece === '..') {
      if (!parts.length) return null;
      parts.pop();
    } else if (piece && piece !== '.') parts.push(decodeURIComponent(piece));
  }
  return parts.join('/');
}

/**
 * `to` (a path in the published site; a folder ends with `/`, the root is ``) as a
 * URL relative to the published file `from`.
 */
export function relativeUrl(from: string, to: string): string {
  const fromDir = from.split('/').slice(0, -1);
  const toParts = to.split('/');
  const leaf = toParts.pop() ?? '';
  let shared = 0;
  while (shared < fromDir.length && shared < toParts.length && fromDir[shared] === toParts[shared])
    shared++;
  const up = fromDir.slice(shared).map(() => '..');
  const down = toParts.slice(shared);
  const path = [...up, ...down, leaf].filter((p, i, all) => p !== '' || i === all.length - 1);
  const joined = path.join('/');
  if (joined === '') return './';
  return joined.split('/').map(encodeSegment).join('/');
}

function encodeSegment(segment: string): string {
  return segment === '..' || segment === '.' ? segment : encodeURIComponent(segment);
}

/** A tag's folder: `tags/<slug>/`. */
export function tagDir(tag: string): string {
  const slug = tag
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '');
  return `tags/${slug || 'tag'}/`;
}
