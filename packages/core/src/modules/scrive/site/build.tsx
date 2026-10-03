/**
 * The static site build: a site's pages → the files of a published site.
 *
 * Pure: everything it needs is passed in (pages, theme, cached cell outputs,
 * diagrams drawn ahead of time — `prepare.ts` gathers those in the browser), and it
 * returns text files plus the lists the backend completes them from (embedded media
 * to copy, share cards to draw). The pages render through the same `MystView` as the
 * in-app Preview, under a `StaticRenderContext`; what you preview is what you publish.
 *
 * Output, for a site with posts:
 *
 *   index.html                  home: the site's index.md, then the posts
 *   posts/<slug>/index.html     one folder per page (relative links work anywhere)
 *   tags/index.html, tags/<tag>/index.html
 *   feed.xml  sitemap.xml  robots.txt  404.html
 *   _scrive/site.css            theme tokens + page.css + site.css
 *
 * Which pages go: `isPublic`, the same rule as the backend's jupyter-book mode — a post
 * once its status is `published`, any other page unless its status says otherwise.
 *
 * A site theme may ship its own layouts (`themes/<id>/layouts/{page,home,list,base}.html`,
 * `template.ts`). Each one it has replaces the built-in React layout for that kind of
 * file; the page body is still `MystView`'s, handed to the template as `{{{content}}}`.
 */
import katex from 'katex';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import type { NbOutput } from '../../../notebook/types';
import type { PageMeta } from '../api';
import { notebookTree } from '../myst/notebook';
import {
  parseMyst,
  readFrontmatter,
  splitFrontmatter,
  type MystNode,
  type MystRoot,
} from '../myst/parse';
import { MystView, toText } from '../render/MystView';
import pageCss from '../render/page.css?raw';
import { StaticRenderContext, type StaticRender } from '../render/static-context';
import {
  Document,
  EntryList,
  HeadTags,
  layoutOf,
  PostHeader,
  PrevNext,
  Section,
  TagList,
  type Entry,
  type LayoutId,
  type NavLink,
  type TocGroup,
} from './layouts';
import { diagramCss, themePalettes } from './palette';
import siteCss from './site.css?raw';
import { compileTheme, render, type Template, type TemplateName } from './template';
import { outputPath, pageDir, relativeUrl, SITE_URL, sitePath, tagDir } from './urls';

export interface SourcePage {
  meta: PageMeta;
  content: string;
}

export interface SiteSource {
  id: string;
  title: string;
  /** `templates`: a site theme's own layouts by name (`page`, `home`, `list`, `base`). */
  theme: { layout: string; tokens: string; templates?: Record<string, string> };
  pages: SourcePage[];
  /** A page's cached code-cell outputs: page path → `cellKey(source, occurrence)`. */
  cells?: Record<string, Record<string, NbOutput[]>>;
  /** `{mermaid}` source → SVG, drawn ahead of time (`prepare.ts`). */
  mermaid?: Record<string, string>;
  /** For the footer; defaults to this year. */
  year?: number;
  /** Pages built whatever their status: a print build exports a draft. */
  force?: string[];
}

/** A share card the backend draws (backend/modules/scrive/cards.py). */
export interface CardRequest {
  path: string;
  title: string;
  description: string;
  kicker: string;
}

export interface SiteBuild {
  files: Record<string, string>;
  /** Site files the pages embed, copied by the backend. */
  assets: string[];
  /** Scene sources the pages embed; the backend reports any that do not exist. */
  scenes: string[];
  cards: CardRequest[];
  /** The source pages published. */
  pages: string[];
  /** Pages left out, and why. */
  leftOut: { path: string; title: string; status: string }[];
}

export function cellKey(source: string, occurrence: number): string {
  return `${occurrence}\u0000${source}`;
}

/** Must agree with `is_public` in backend/modules/scrive/publish.py. */
export function isPublic(meta: Pick<PageMeta, 'kind' | 'status'>): boolean {
  const status = meta.status.trim().toLowerCase();
  if (meta.kind === 'post') return status === 'published';
  return status === '' || status === 'published';
}

export const KATEX_CSS = `https://cdn.jsdelivr.net/npm/katex@${katex.version}/dist/katex.min.css`;

/** The published stylesheet: the theme's tokens, the page styles, the layouts. */
export function siteStylesheet(
  tokens: string,
  // The stylesheets' text. Injectable because vitest stubs `?raw` CSS imports as
  // empty strings; under Vite they are the files.
  css: { page: string; site: string } = { page: pageCss, site: siteCss },
): string {
  // page.css pulls KaTeX in with an @import for the app; the site links it instead,
  // and only from pages that have math.
  const page = css.page.replace(/@import[^;]*;\s*/g, '');
  const diagrams = diagramCss(themePalettes(tokens).alt);
  return [
    `/* Theme tokens */\n${tokens}`,
    `/* Page content (render/page.css) */\n${page}`,
    `/* Layout (site/site.css) */\n${css.site}`,
    `/* Diagrams: the one drawn for the reader's colour scheme */\n${diagrams}\n`,
  ].join('\n\n');
}

interface Prepared {
  source: string;
  dir: string;
  out: string;
  kind: PageMeta['kind'];
  title: string;
  description: string;
  date: string;
  tags: string[];
  /** Site path of the page's own `thumbnail:`, if it names one. */
  thumbnail: string | null;
  tree: MystRoot;
  lineOffset: number;
  /** The body opens with its own `#` heading naming the page: the layout does not
   * repeat the title. (A body that opens with `# Introduction` still gets one.) */
  ownTitle: boolean;
  math: boolean;
  minutes: number;
}

function walk(node: MystNode, visit: (n: MystNode) => void): void {
  visit(node);
  for (const child of node.children ?? []) walk(child, visit);
}

function prepare(page: SourcePage): Prepared {
  const { meta, content } = page;
  let tree: MystRoot;
  let lineOffset = 0;
  let data: Record<string, unknown> = {};
  if (meta.path.endsWith('.ipynb')) {
    tree = notebookTree(content).tree;
  } else {
    const fm = splitFrontmatter(content);
    data = readFrontmatter(fm.raw);
    tree = parseMyst(fm.body);
    lineOffset = fm.lines;
  }
  let math = false;
  walk(tree, (n) => {
    if (n.type === 'math' || n.type === 'inlineMath') math = true;
  });
  const first = tree.children.find((n) => n.type !== 'comment' && n.type !== 'mystTarget');
  const words = toText(tree).split(/\s+/).filter(Boolean).length;
  const thumb = typeof data.thumbnail === 'string' ? data.thumbnail : '';
  return {
    source: meta.path,
    dir: pageDir(meta.path),
    out: outputPath(meta.path),
    kind: meta.kind,
    title: meta.title,
    description: meta.description,
    date: meta.kind === 'post' ? meta.date : '',
    tags: meta.tags,
    thumbnail: thumb && !/^(?:https?:)?\/\//i.test(thumb) ? sitePath(meta.path, thumb) : null,
    tree,
    lineOffset,
    ownTitle:
      first?.type === 'heading' &&
      Number(first.depth) === 1 &&
      toText(first).trim().toLowerCase() === meta.title.trim().toLowerCase(),
    math,
    minutes: meta.kind === 'post' ? Math.max(1, Math.round(words / 230)) : 0,
  };
}

const PAGE_LINK = /\.(md|ipynb)$/i;
const HAS_SCHEME = /^[a-z][\w+.-]*:/i;

function esc(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function absolute(path: string): string {
  return SITE_URL + path.split('/').map(encodeURIComponent).join('/');
}

function rfc822(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toUTCString();
}

function cardPath(dir: string): string {
  return `_scrive/og/${dir.replace(/\/$/, '').replace(/\//g, '--') || 'index'}.png`;
}

/** What a theme template gets for one file, beside what every file gets. */
interface Shape {
  name: Exclude<TemplateName, 'base'>;
  data: Record<string, unknown>;
  content?: ReactNode;
}

/** An entry list item as a template sees it: URLs resolved for the file being written. */
function entryData(entry: Entry, url: (path: string) => string) {
  return {
    title: entry.title,
    url: url(entry.path),
    date: entry.date,
    description: entry.description,
    tags: entry.tags.map((t) => ({ title: t.title, url: url(t.path) })),
  };
}

export function buildSite(input: SiteSource): SiteBuild {
  const layout: LayoutId = layoutOf(input.theme.layout);
  // Compiled once; a malformed template stops the build with its file and line.
  const templates = compileTheme(input.theme.templates);
  const year = input.year ?? new Date().getFullYear();
  const included = (p: SourcePage) => isPublic(p.meta) || !!input.force?.includes(p.meta.path);
  const published = input.pages.filter(included);
  const leftOut = input.pages
    .filter((p) => !included(p))
    .map((p) => ({ path: p.meta.path, title: p.meta.title, status: p.meta.status || 'draft' }));
  const pages = published.map(prepare);
  const bySource = new Map(pages.map((p) => [p.source, p]));
  const assets = new Set<string>();
  const scenes = new Set<string>();
  const files: Record<string, string> = {};
  const cards: CardRequest[] = [];

  const posts = pages
    .filter((p) => p.kind === 'post')
    .sort((a, b) => (b.date + b.source).localeCompare(a.date + a.source));
  const others = pages
    .filter((p) => p.kind !== 'post' && p.dir !== '')
    .sort((a, b) => a.source.localeCompare(b.source));
  const home = bySource.get('index.md') ?? bySource.get('index.ipynb') ?? null;

  const tagIndex = new Map<string, Prepared[]>();
  for (const post of posts)
    for (const tag of post.tags) tagIndex.set(tag, [...(tagIndex.get(tag) ?? []), post]);
  const tagLinks = (tags: string[]): NavLink[] =>
    tags.map((tag) => ({ title: tag, path: tagDir(tag) }));
  const entry = (p: Prepared): Entry => ({
    title: p.title,
    path: p.dir,
    date: p.date,
    description: p.description,
    tags: tagLinks(p.tags),
  });

  // The header: the pages that are not posts (book and article list them elsewhere).
  const nav: NavLink[] =
    layout === 'book' || layout === 'article'
      ? []
      : [
          ...others.slice(0, 6).map((p) => ({ title: p.title, path: p.dir })),
          ...(tagIndex.size ? [{ title: 'Tags', path: 'tags/' }] : []),
        ];
  const toc: TocGroup[] | undefined =
    layout === 'book'
      ? [
          { title: 'Start', links: [{ title: home?.title ?? input.title, path: '' }] },
          ...(others.length
            ? [{ title: 'Pages', links: others.map((p) => ({ title: p.title, path: p.dir })) }]
            : []),
          ...(posts.length
            ? [{ title: 'Posts', links: posts.map((p) => ({ title: p.title, path: p.dir })) }]
            : []),
        ]
      : undefined;

  /** A page's renderer hooks, answered for the file being written. */
  const staticFor = (page: Prepared): StaticRender => ({
    asset(pagePath, url) {
      if (/^(?:https?:)?\/\//i.test(url) || url.startsWith('data:')) return url;
      const path = sitePath(pagePath, url);
      if (path === null || path === '') return url;
      assets.add(path);
      return relativeUrl(page.out, path);
    },
    link(pagePath, href) {
      if (href.startsWith('#') || HAS_SCHEME.test(href) || href.startsWith('//')) return href;
      const hash = href.includes('#') ? href.slice(href.indexOf('#')) : '';
      const path = sitePath(pagePath, href);
      if (path === null) return undefined;
      if (PAGE_LINK.test(path)) {
        const target = bySource.get(path);
        return target ? relativeUrl(page.out, target.dir) + hash : undefined;
      }
      if (/\.[a-z0-9]+$/i.test(path)) {
        assets.add(path);
        return relativeUrl(page.out, path) + hash;
      }
      return href;
    },
    cellOutputs(pagePath, source, occurrence) {
      return input.cells?.[pagePath]?.[cellKey(source, occurrence)] ?? [];
    },
    mermaid(code) {
      return input.mermaid?.[code] ?? null;
    },
    scene(pagePath, src, params) {
      const path = sitePath(pagePath, src) ?? src;
      scenes.add(path);
      const target = `_scrive/scene/${path.replace(/\.tsx$/i, '')}.html`;
      const hash = Object.keys(params).length
        ? `#${encodeURIComponent(JSON.stringify(params))}`
        : '';
      return relativeUrl(page.out, target) + hash;
    },
  });

  const imageFor = (page: Prepared | null): string => {
    if (page?.thumbnail) {
      assets.add(page.thumbnail);
      return absolute(page.thumbnail);
    }
    const card = cardPath(page?.dir ?? '');
    if (!cards.some((c) => c.path === card)) {
      cards.push({
        path: card,
        title: page?.title ?? input.title,
        description: page?.description ?? '',
        kicker: page?.date || (page && page.kind === 'post' ? 'Post' : ''),
      });
    }
    return SITE_URL + card;
  };

  const write = (
    out: string,
    path: string,
    props: {
      title: string;
      description: string;
      type: 'website' | 'article';
      image: string;
      date?: string;
      math?: boolean;
    },
    body: ReactNode,
    absoluteLinks = false,
    shape?: Shape,
  ) => {
    const url = absoluteLinks ? (to: string) => absolute(to) : (to: string) => relativeUrl(out, to);
    const template = shape ? templates[shape.name] : undefined;
    if (shape && template) {
      files[out] = '<!doctype html>\n' + fromTemplate(template, shape, props, path, url);
      return;
    }
    files[out] =
      '<!doctype html>\n' +
      renderToStaticMarkup(
        <Document
          layout={layout}
          siteTitle={input.title}
          title={props.title}
          description={props.description}
          path={path}
          url={url}
          canonical={absolute(path)}
          image={props.image}
          type={props.type}
          date={props.date}
          nav={nav}
          toc={toc}
          katexCss={props.math ? KATEX_CSS : undefined}
          year={year}
        >
          {body}
        </Document>,
      );
  };

  /** A file drawn by the theme's own template (`template.ts`). */
  const fromTemplate = (
    template: Template,
    shape: Shape,
    props: {
      title: string;
      description: string;
      type: 'website' | 'article';
      image: string;
      date?: string;
      math?: boolean;
    },
    path: string,
    url: (path: string) => string,
  ): string => {
    const head = renderToStaticMarkup(
      <HeadTags
        siteTitle={input.title}
        title={props.title}
        description={props.description}
        url={url}
        canonical={absolute(path)}
        image={props.image}
        type={props.type}
        date={props.date}
        katexCss={props.math ? KATEX_CSS : undefined}
      />,
    );
    const link = (l: NavLink) => ({ title: l.title, url: url(l.path), current: l.path === path });
    const context: Record<string, unknown> = {
      site: { title: input.title, root: url(''), feed: url('feed.xml'), year, layout },
      head,
      css: url('_scrive/site.css'),
      nav: nav.map(link),
      toc: (toc ?? []).map((g) => ({ title: g.title, links: g.links.map(link) })),
      ...shape.data,
      content: shape.content ? renderToStaticMarkup(shape.content) : '',
    };
    let html = render(template, context);
    if (templates.base) html = render(templates.base, { ...context, body: html });
    // A template may be a fragment: give it the document around it.
    if (!/<html[\s>]/i.test(html))
      html = `<html lang="en"><head>${head}</head><body class="site site-${layout}">${html}</body></html>`;
    return html;
  };

  const content = (page: Prepared) => (
    <StaticRenderContext.Provider value={staticFor(page)}>
      <MystView
        tree={page.tree}
        site={input.id}
        pagePath={page.source}
        lineOffset={page.lineOffset}
      />
    </StaticRenderContext.Provider>
  );

  // --- every page -------------------------------------------------------------------
  for (const page of pages) {
    if (page === home) continue;
    const url = (to: string) => relativeUrl(page.out, to);
    const at = posts.indexOf(page);
    write(
      page.out,
      page.dir,
      {
        title: page.title,
        description: page.description,
        type: page.kind === 'post' ? 'article' : 'website',
        image: imageFor(page),
        date: page.date,
        math: page.math,
      },
      <article className="site-post">
        <PostHeader
          title={page.title}
          showTitle={!page.ownTitle}
          date={page.date}
          minutes={page.minutes}
          description={page.description}
          tags={tagLinks(page.tags)}
          url={url}
        />
        {page.thumbnail && (
          <img className="site-hero" src={relativeUrl(page.out, page.thumbnail)} alt="" />
        )}
        {content(page)}
        {at >= 0 && (
          <PrevNext
            prev={at > 0 ? { title: posts[at - 1].title, path: posts[at - 1].dir } : undefined}
            next={
              at < posts.length - 1
                ? { title: posts[at + 1].title, path: posts[at + 1].dir }
                : undefined
            }
            url={url}
          />
        )}
      </article>,
      false,
      {
        name: 'page',
        content: content(page),
        data: {
          page: {
            title: page.title,
            showTitle: !page.ownTitle,
            description: page.description,
            date: page.date,
            minutes: page.minutes,
            kind: page.kind,
            url: url(page.dir),
            hero: page.thumbnail ? relativeUrl(page.out, page.thumbnail) : '',
            tags: tagLinks(page.tags).map((t) => ({ title: t.title, url: url(t.path) })),
          },
          prev: at > 0 ? { title: posts[at - 1].title, url: url(posts[at - 1].dir) } : null,
          next:
            at >= 0 && at < posts.length - 1
              ? { title: posts[at + 1].title, url: url(posts[at + 1].dir) }
              : null,
        },
      },
    );
  }

  // --- home -------------------------------------------------------------------------
  {
    const url = (to: string) => relativeUrl('index.html', to);
    const lists: ReactNode[] = [];
    if (layout === 'article') {
      const more = [...others, ...posts].map(entry);
      if (more.length)
        lists.push(
          <Section key="more" title="More">
            <EntryList entries={more} url={url} />
          </Section>,
        );
    } else {
      if (layout === 'book' && others.length)
        lists.push(
          <Section key="pages" title="Pages">
            <EntryList entries={others.map(entry)} url={url} />
          </Section>,
        );
      if (posts.length)
        lists.push(
          <Section key="posts" title="Posts">
            <EntryList entries={posts.map(entry)} url={url} />
          </Section>,
        );
    }
    write(
      'index.html',
      '',
      {
        title: home?.title ?? input.title,
        description: home?.description ?? '',
        type: 'website',
        image: imageFor(home),
        math: home?.math,
      },
      <>
        {home && (
          <article className="site-post site-home">
            {(layout === 'article' || home.description) && (
              <PostHeader
                title={home.title}
                showTitle={layout === 'article' && !home.ownTitle}
                date=""
                minutes={0}
                description={home.description}
                tags={[]}
                url={url}
              />
            )}
            {content(home)}
          </article>
        )}
        {lists}
        {!home && !lists.length && <p className="site-empty">Nothing published yet.</p>}
      </>,
      false,
      {
        name: 'home',
        content: home ? content(home) : undefined,
        data: {
          home: home
            ? {
                title: home.title,
                showTitle: !home.ownTitle,
                description: home.description,
              }
            : null,
          posts: posts.map((p) => entryData(entry(p), url)),
          pages: others.map((p) => entryData(entry(p), url)),
        },
      },
    );
  }

  // --- tags -------------------------------------------------------------------------
  if (tagIndex.size) {
    const tags = [...tagIndex.keys()].sort((a, b) => a.localeCompare(b));
    write(
      'tags/index.html',
      'tags/',
      { title: 'Tags', description: '', type: 'website', image: imageFor(null) },
      <Section title="Tags">
        <TagList
          tags={tags.map((t) => ({
            title: `${t} · ${tagIndex.get(t)?.length ?? 0}`,
            path: tagDir(t),
          }))}
          url={(to) => relativeUrl('tags/index.html', to)}
        />
      </Section>,
      false,
      {
        name: 'list',
        data: {
          title: 'Tags',
          entries: [],
          tags: tags.map((t) => ({
            title: t,
            count: tagIndex.get(t)?.length ?? 0,
            url: relativeUrl('tags/index.html', tagDir(t)),
          })),
        },
      },
    );
    for (const tag of tags) {
      const out = `${tagDir(tag)}index.html`;
      write(
        out,
        tagDir(tag),
        { title: `Tagged “${tag}”`, description: '', type: 'website', image: imageFor(null) },
        <Section title={`Tagged “${tag}”`}>
          <EntryList
            entries={(tagIndex.get(tag) ?? []).map(entry)}
            url={(to) => relativeUrl(out, to)}
          />
        </Section>,
        false,
        {
          name: 'list',
          data: {
            title: `Tagged “${tag}”`,
            tag,
            entries: (tagIndex.get(tag) ?? []).map((p) =>
              entryData(entry(p), (to) => relativeUrl(out, to)),
            ),
            tags: [],
          },
        },
      );
    }
  }

  // --- 404 (served at any path, so its links are absolute) ----------------------------
  write(
    '404.html',
    '404.html',
    { title: 'Not found', description: '', type: 'website', image: imageFor(null) },
    <>
      <h1 className="site-title">Not found</h1>
      <p className="site-lede">
        Nothing is published at this address. <a href={SITE_URL}>Go to the home page</a>.
      </p>
      {posts.length > 0 && (
        <Section title="Recent posts">
          <EntryList entries={posts.slice(0, 5).map(entry)} url={absolute} />
        </Section>
      )}
    </>,
    true,
  );

  // --- feed, sitemap, robots, styles ---------------------------------------------------
  const items = posts
    .slice(0, 50)
    .map((p) =>
      [
        '<item>',
        `<title>${esc(p.title)}</title>`,
        `<link>${esc(absolute(p.dir))}</link>`,
        `<guid isPermaLink="true">${esc(absolute(p.dir))}</guid>`,
        rfc822(p.date) && `<pubDate>${rfc822(p.date)}</pubDate>`,
        p.description && `<description>${esc(p.description)}</description>`,
        ...p.tags.map((t) => `<category>${esc(t)}</category>`),
        '</item>',
      ]
        .filter(Boolean)
        .join(''),
    );
  const latest = posts[0] ? rfc822(posts[0].date) : '';
  files['feed.xml'] =
    [
      '<?xml version="1.0" encoding="utf-8"?>',
      '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
      `<title>${esc(input.title)}</title>`,
      `<link>${SITE_URL}</link>`,
      `<description>${esc(home?.description || input.title)}</description>`,
      `<atom:link href="${SITE_URL}feed.xml" rel="self" type="application/rss+xml"/>`,
      ...(latest ? [`<lastBuildDate>${latest}</lastBuildDate>`] : []),
      ...items,
      '</channel></rss>',
    ].join('\n') + '\n';
  const listed = [
    { dir: '', date: '' },
    ...pages.filter((p) => p !== home).map((p) => ({ dir: p.dir, date: p.date })),
  ];
  files['sitemap.xml'] =
    [
      '<?xml version="1.0" encoding="utf-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
      ...listed.map(
        ({ dir, date }) =>
          `<url><loc>${esc(absolute(dir))}</loc>${date ? `<lastmod>${esc(date)}</lastmod>` : ''}</url>`,
      ),
      '</urlset>',
    ].join('\n') + '\n';
  files['robots.txt'] = `User-agent: *\nAllow: /\nSitemap: ${SITE_URL}sitemap.xml\n`;
  files['_scrive/site.css'] = siteStylesheet(input.theme.tokens);

  return {
    files,
    assets: [...assets].sort(),
    scenes: [...scenes].sort(),
    cards,
    pages: pages.map((p) => p.source),
    leftOut,
  };
}

/**
 * One page for print (the PDF export's print engine): the same document the site
 * publishes, with the site's print rules (`site.css` `@media print`) hiding its chrome.
 * The page is built whatever its status — a PDF is for the person holding it — and
 * nothing else is: the result holds the page's file and the stylesheet.
 */
export function buildPrint(input: SiteSource, path: string): SiteBuild {
  const page = input.pages.find((p) => p.meta.path === path);
  if (!page) throw new Error(`no page ${path}`);
  const build = buildSite({ ...input, pages: [page], force: [path] });
  const out = outputPath(path);
  return {
    files: { [out]: build.files[out], '_scrive/site.css': build.files['_scrive/site.css'] },
    assets: build.assets,
    scenes: build.scenes,
    cards: [],
    pages: [path],
    leftOut: [],
  };
}
