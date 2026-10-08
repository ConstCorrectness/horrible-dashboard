/**
 * Everything the static build needs, gathered in the browser: the site's pages (only
 * the ones that will be published are read), its theme, each page's cached code-cell
 * outputs, and its Mermaid diagrams drawn ahead of time — `buildSite` itself runs no
 * effects and fetches nothing.
 *
 * Code cells are **not run** here. A published page shows what its cells last
 * produced; a cell never run publishes as code alone. Running is the person's call,
 * in the page.
 */
import {
  cachedCells,
  getSite,
  listPages,
  listThemes,
  readPage,
  siteFileUrl,
  type SiteBundle,
  type ThemeInfo,
} from '../api';
import type { NbOutput } from '../../../notebook/types';
import { cellId, codeCellsOf } from '../cells';
import { parseMyst, splitFrontmatter, type MystNode } from '../myst/parse';
import { buildPrint, buildSite, cellKey, isPublic, type SiteBuild, type SiteSource } from './build';
import { mermaidVariables, themePalettes } from './palette';
import { sitePath } from './urls';

export interface PreparedSite {
  source: SiteSource;
  theme: ThemeInfo;
}

/** The data files a page's `{tokenviz}` blocks draw, as site paths. */
function tokenvizSources(pagePath: string, content: string): string[] {
  const found: string[] = [];
  const visit = (node: MystNode) => {
    if (node.type === 'mystDirective' && node.name === 'tokenviz' && typeof node.args === 'string') {
      const path = sitePath(pagePath, node.args.trim());
      if (path) found.push(path);
    }
    for (const child of node.children ?? []) visit(child);
  };
  visit(parseMyst(splitFrontmatter(content).body));
  return found;
}

/** Fetch each data file once; a missing or malformed one is left out (the page then
 * omits that figure). */
async function loadData(site: string, paths: string[]): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {};
  for (const path of new Set(paths)) {
    try {
      const res = await fetch(siteFileUrl(site, path));
      if (res.ok) out[path] = await res.json();
    } catch {
      // Left out.
    }
  }
  return out;
}

function mermaidSources(content: string): string[] {
  const found: string[] = [];
  const visit = (node: MystNode) => {
    if (node.type === 'mermaid' && typeof node.value === 'string') found.push(node.value);
    for (const child of node.children ?? []) visit(child);
  };
  visit(parseMyst(splitFrontmatter(content).body));
  return found;
}

/** Each diagram drawn in the theme's palette and its other colour scheme. */
async function drawDiagrams(codes: string[], tokens: string): Promise<Record<string, string>> {
  if (!codes.length) return {};
  const { default: mermaid } = await import('mermaid');
  const { base, alt } = themePalettes(tokens);
  const out: Record<string, string> = {};
  let n = 0;
  const draw = async (code: string, palette: Record<string, string>) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'base',
      themeVariables: mermaidVariables(palette),
    });
    try {
      return (await mermaid.render(`scrive-site-mermaid-${n++}`, code)).svg;
    } catch {
      return null;
    }
  };
  for (const code of new Set(codes)) {
    const first = await draw(code, base);
    if (!first) continue; // published as its source (MystView's fallback)
    const second = alt ? await draw(code, alt.palette) : null;
    out[code] =
      `<div class="scrive-mermaid-base">${first}</div>` +
      (second ? `<div class="scrive-mermaid-alt">${second}</div>` : '');
  }
  return out;
}

async function outputsFor(
  site: string,
  path: string,
  content: string,
): Promise<Record<string, NbOutput[]>> {
  const wanted = codeCellsOf(content);
  if (!wanted.length) return {};
  const { cells } = await cachedCells(site, path);
  const byId = new Map(cells.map((c) => [c.id, c.outputs]));
  const out: Record<string, NbOutput[]> = {};
  for (const cell of wanted) {
    const outputs = byId.get(await cellId(cell.source, cell.occurrence));
    if (outputs?.length) out[cellKey(cell.source, cell.occurrence)] = outputs;
  }
  return out;
}

/** Everything a print build of one page needs: that page alone, read whatever its
 * status, with its cell outputs and diagrams. */
export async function preparePage(site: string, path: string): Promise<PreparedSite> {
  const [{ site: meta, config }, metas, themes, page] = await Promise.all([
    getSite(site),
    listPages(site),
    listThemes(site),
    readPage(site, path),
  ]);
  const theme =
    themes.find((t) => t.id === config.theme) ??
    themes.find((t) => t.id === 'minimal') ??
    themes[0];
  const pageMeta = metas.find((m) => m.path === path) ?? page.meta;
  const cells: SiteSource['cells'] = {};
  if (path.endsWith('.md')) {
    const outputs = await outputsFor(site, path, page.content);
    if (Object.keys(outputs).length) cells[path] = outputs;
  }
  return {
    theme,
    source: {
      id: site,
      title: config.title || meta.title,
      theme: { layout: theme.layout, tokens: theme.tokens, templates: theme.templates },
      pages: [{ meta: pageMeta, content: page.content }],
      cells,
      mermaid: await drawDiagrams(mermaidSources(page.content), theme.tokens),
      data: await loadData(site, tokenvizSources(path, page.content)),
    },
  };
}

/** A print build of one page, shaped for the backend's PDF export. */
export async function buildPrintBundle(site: string, path: string): Promise<SiteBundle> {
  const { source } = await preparePage(site, path);
  const build = buildPrint(source, path);
  return {
    files: build.files,
    assets: build.assets,
    scenes: build.scenes,
    apps: build.apps,
    cards: [],
    pages: build.pages,
  };
}

export async function prepareSite(site: string): Promise<PreparedSite> {
  const [{ site: meta, config }, metas, themes] = await Promise.all([
    getSite(site),
    listPages(site),
    listThemes(site),
  ]);
  const theme =
    themes.find((t) => t.id === config.theme) ??
    themes.find((t) => t.id === 'minimal') ??
    themes[0];
  const pages = await Promise.all(
    metas.map(async (m) => ({
      meta: m,
      content: isPublic(m) ? (await readPage(site, m.path)).content : '',
    })),
  );
  const cells: SiteSource['cells'] = {};
  const codes: string[] = [];
  const dataPaths: string[] = [];
  for (const page of pages) {
    if (!page.content || !page.meta.path.endsWith('.md')) continue;
    const outputs = await outputsFor(site, page.meta.path, page.content);
    if (Object.keys(outputs).length) cells[page.meta.path] = outputs;
    codes.push(...mermaidSources(page.content));
    dataPaths.push(...tokenvizSources(page.meta.path, page.content));
  }
  return {
    theme,
    source: {
      id: site,
      title: config.title || meta.title,
      theme: { layout: theme.layout, tokens: theme.tokens, templates: theme.templates },
      pages,
      cells,
      mermaid: await drawDiagrams(codes, theme.tokens),
      data: await loadData(site, dataPaths),
    },
  };
}

/** Build the site and shape it for the backend. */
export async function buildBundle(
  site: string,
): Promise<{ build: SiteBuild; bundle: SiteBundle; theme: ThemeInfo }> {
  const { source, theme } = await prepareSite(site);
  const build = buildSite(source);
  return {
    build,
    theme,
    bundle: {
      files: build.files,
      assets: build.assets,
      scenes: build.scenes,
      apps: build.apps,
      cards: build.cards,
      pages: build.pages,
    },
  };
}
