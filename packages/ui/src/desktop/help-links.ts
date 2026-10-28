/**
 * Links from the app into its published documentation.
 *
 * The docs are a Docusaurus site built from `docs/` with `routeBasePath: '/'`
 * (website/docusaurus.config.ts), so `docs/modules/terminal.mdx` is served at
 * `<DOCS_URL>modules/terminal`. A module's page is found by its **id**, which
 * is the page's file name for almost every module; the exceptions are listed in
 * `PAGE_ALIASES`.
 *
 * `MODULE_PAGES` is the set of pages known to exist. A module whose id is not in
 * it gets no per-pane Help item at all, never a guessed URL — a Help item that
 * lands on the site's 404 page reads as "this has no docs", which is worse than
 * offering only the general documentation. `help-links.test.ts` checks every
 * entry against the files on disk.
 */
import { openExternal, toastsStore } from '@horrible/core';

/**
 * The published documentation. A constant rather than a setting: this is where
 * *this build's* docs live, and a user pointing it somewhere else is not a thing
 * anyone wants. Deployed from `docs/` by `.github/workflows/docs.yml`.
 */
export const DOCS_URL = 'https://constcorrectness.github.io/horrible-dashboard/';

/** Module ids whose page in `docs/modules/` has the same name. */
export const MODULE_PAGES = new Set([
  'agentpedia',
  'audio',
  'briefing',
  'browser',
  'clubhouse',
  'code',
  'commons',
  'dashboard',
  'database',
  'datasets',
  'discover',
  'docviewer',
  'editor',
  'evals',
  'explorer',
  'games',
  'git',
  'github',
  'hardware',
  'hassault',
  'ide',
  'interpretability',
  'karaoke',
  'kb',
  'keymap',
  'lab',
  'library',
  'llamacpp',
  'localtrack',
  'marketplace',
  'mcp',
  'music',
  'network',
  'notebook',
  'notifications',
  'observability',
  'people',
  'records',
  'repl',
  'research',
  'scratch',
  'scrive',
  'search',
  'settings',
  'share',
  'skills',
  'social',
  'terminal',
  'training',
  'trajectories',
  'visualizer',
  'webml',
]);

/** Module ids whose page is named differently, as a path under `docs/`. */
export const PAGE_ALIASES: Record<string, string> = {
  agent: 'modules/agent-chat',
  docs: 'modules/docs-popup',
  files: 'modules/file-explorer',
  flow: 'modules/flow-canvas',
  interp: 'modules/interp-suite',
  desktop: 'architecture/desktop-shell',
};

/** The docs path (no extension, relative to `docs/`) for a module, or null. */
export function docsPageFor(moduleId: string | undefined): string | null {
  if (!moduleId) return null;
  if (moduleId in PAGE_ALIASES) return PAGE_ALIASES[moduleId]!;
  return MODULE_PAGES.has(moduleId) ? `modules/${moduleId}` : null;
}

/**
 * Open a docs page, or the docs home for no `page`.
 *
 * The result of `openExternal` is **checked**, because this is exactly the
 * failure it exists to catch: `window.open` returns null behind a pop-up blocker
 * and the shell command rejects on an older shell, and neither throws. A Help
 * button that does nothing is worse than no Help button — the user concludes the
 * docs do not exist rather than that a pop-up was blocked.
 */
export async function openDocs(page?: string | null): Promise<void> {
  const url = page ? `${DOCS_URL}${page}` : DOCS_URL;
  if (await openExternal(url)) return;
  toastsStore.add(
    'warning',
    'Could not open the docs',
    `Your browser may have blocked it. The page is at ${url}`,
  );
}
