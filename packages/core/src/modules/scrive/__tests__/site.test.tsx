/**
 * The static site build: where pages land, that every link is relative, that drafts
 * stay home, and that a published page carries none of the app's chrome (run bars,
 * agent buttons, editor line anchors). Also the theme palettes Mermaid is drawn
 * with, and the MyST plugin a Jupyter Book build of the site runs.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

import { mystParse } from 'myst-parser';
import { describe, expect, it } from 'vitest';

import type { PageMeta } from '../api';
import { buildSite, cellKey, isPublic, siteStylesheet, type SourcePage } from '../site/build';
import { diagramCss, mermaidVariables, themePalettes } from '../site/palette';
import { outputPath, pageDir, relativeUrl, SITE_URL, sitePath, tagDir } from '../site/urls';

const REPO = join(__dirname, '..', '..', '..', '..', '..', '..');
const THEMES = join(REPO, 'backend', 'modules', 'scrive', 'builtin_themes');
const tokens = (id: string) => readFileSync(join(THEMES, id, 'tokens.css'), 'utf8');

function page(path: string, content: string, meta: Partial<PageMeta> = {}): SourcePage {
  return {
    meta: {
      path,
      kind: path.startsWith('posts/') ? 'post' : path.endsWith('.ipynb') ? 'notebook' : 'page',
      title: path,
      status: '',
      date: '',
      tags: [],
      description: '',
      updated_at: 0,
      revision: 'r',
      ...meta,
    },
    content,
  };
}

const POST = `---
title: Priors
---

Some math $x^2$ and a [link to about](../pages/about.md#team), a [draft](./draft.md),
and [the PDF](../media/paper.pdf).

![A plot](../media/plot.png)

::::{tab-set}
:::{tab-item} One
First.
:::
:::{tab-item} Two
Second.
:::
::::

:::{pending}
Explain the posterior.
:::

\`\`\`{r3f} ../scenes/orbit.tsx
:params: {"speed": {"value": 2, "min": 0, "max": 5}}
\`\`\`

\`\`\`{code-cell} python
print(1)
\`\`\`
`;

function site(layout = 'minimal') {
  return buildSite({
    id: 'blog',
    title: 'Field Notes',
    theme: { layout, tokens: tokens(layout) },
    year: 2026,
    pages: [
      page('index.md', '---\ntitle: Home\ndescription: Notes.\n---\n\nWelcome.\n', {
        title: 'Home',
        description: 'Notes.',
      }),
      page('posts/2026-10-01-priors.md', POST, {
        title: 'Priors',
        status: 'published',
        date: '2026-10-01',
        tags: ['stats', 'Bayes rule'],
        description: 'Why priors matter.',
      }),
      page(
        'posts/2026-10-02-later.md',
        '---\ntitle: Later\nthumbnail: ../media/t.png\n---\n\nHi.\n',
        {
          title: 'Later',
          status: 'published',
          date: '2026-10-02',
        },
      ),
      page('posts/draft.md', '---\ntitle: Draft\n---\n\nNot yet.\n', { status: 'draft' }),
      page('pages/about.md', '---\ntitle: About\n---\n\n## Team\n\nUs.\n', { title: 'About' }),
    ],
    cells: {
      'posts/2026-10-01-priors.md': {
        [cellKey('print(1)', 0)]: [{ output_type: 'stream', name: 'stdout', text: 'ONE\n' }],
      },
    },
  });
}

describe('urls', () => {
  it('gives every page a folder', () => {
    expect(pageDir('index.md')).toBe('');
    expect(pageDir('posts/a.md')).toBe('posts/a/');
    expect(pageDir('pages/index.md')).toBe('pages/');
    expect(outputPath('nb/run.ipynb')).toBe('nb/run/index.html');
  });

  it('links relative to the file being written', () => {
    expect(relativeUrl('posts/a/index.html', 'media/x.png')).toBe('../../media/x.png');
    expect(relativeUrl('posts/a/index.html', 'posts/b/')).toBe('../b/');
    expect(relativeUrl('posts/a/index.html', '')).toBe('../../');
    expect(relativeUrl('index.html', '')).toBe('./');
    expect(relativeUrl('posts/a/index.html', 'posts/a/')).toBe('./');
    expect(relativeUrl('index.html', 'media/a b.png')).toBe('media/a%20b.png');
  });

  it('resolves a page link and refuses one that leaves the site', () => {
    expect(sitePath('posts/a.md', '../media/x.png?v=1')).toBe('media/x.png');
    expect(sitePath('posts/a.md', '/media/x.png')).toBe('media/x.png');
    expect(sitePath('posts/a.md', '../../etc/passwd')).toBeNull();
    expect(tagDir('Bayes rule')).toBe('tags/bayes-rule/');
  });
});

describe('isPublic', () => {
  it('lets a post out only when published, a page unless held back', () => {
    expect(isPublic({ kind: 'post', status: 'published' })).toBe(true);
    expect(isPublic({ kind: 'post', status: '' })).toBe(false);
    expect(isPublic({ kind: 'post', status: 'review' })).toBe(false);
    expect(isPublic({ kind: 'page', status: '' })).toBe(true);
    expect(isPublic({ kind: 'page', status: 'draft' })).toBe(false);
    expect(isPublic({ kind: 'notebook', status: '' })).toBe(true);
  });
});

describe('buildSite', () => {
  const out = site();
  const post = out.files['posts/2026-10-01-priors/index.html'];

  it('writes the pages, tags, feed and support files, and leaves drafts out', () => {
    expect(Object.keys(out.files).sort()).toEqual([
      '404.html',
      '_scrive/site.css',
      'feed.xml',
      'index.html',
      'pages/about/index.html',
      'posts/2026-10-01-priors/index.html',
      'posts/2026-10-02-later/index.html',
      'robots.txt',
      'sitemap.xml',
      'tags/bayes-rule/index.html',
      'tags/index.html',
      'tags/stats/index.html',
    ]);
    expect(out.leftOut).toEqual([
      { path: 'posts/draft.md', title: 'posts/draft.md', status: 'draft' },
    ]);
    expect(out.pages).not.toContain('posts/draft.md');
    expect(Object.values(out.files).join('')).not.toContain('Not yet.');
  });

  it('makes every link relative, and links to drafts plain text', () => {
    expect(post).toContain('href="../../_scrive/site.css"');
    expect(post).toContain('src="../../media/plot.png"');
    expect(post).toContain('href="../../pages/about/#team"');
    expect(post).toContain('href="../../media/paper.pdf"');
    expect(post).toMatch(/<a>draft<\/a>/);
    expect(out.assets).toEqual(['media/paper.pdf', 'media/plot.png', 'media/t.png']);
  });

  it('publishes content, not the app around it', () => {
    expect(post).not.toContain('data-line');
    expect(post).not.toContain('Explain the posterior');
    expect(post).not.toMatch(/>Run</);
    expect(post).not.toContain('write with agent');
    // The cell's cached output, and tabs that work without script.
    expect(post).toContain('ONE');
    expect(post.match(/type="radio"/g)).toHaveLength(2);
    expect(post).toContain(
      `src="../../_scrive/scene/scenes/orbit.html#${encodeURIComponent('{"speed":2}')}"`,
    );
    expect(post).toContain('sandbox="allow-scripts"');
  });

  it('lists the scenes it embeds, resolved against the page', () => {
    expect(out.scenes).toEqual(['scenes/orbit.tsx']);
  });

  it('titles a page whose body opens with a different # heading', () => {
    const built = buildSite({
      id: 'b',
      title: 'B',
      theme: { layout: 'minimal', tokens: '' },
      pages: [
        page('pages/a.md', '# Introduction\n\nText.\n', { title: 'Guide' }),
        page('pages/b.md', '# Guide\n\nText.\n', { title: 'Guide' }),
      ],
    }).files;
    expect(built['pages/a/index.html']).toContain('<h1 class="site-title">Guide</h1>');
    expect(built['pages/b/index.html']).not.toContain('class="site-title"');
  });

  it('links KaTeX only from pages with math', () => {
    expect(post).toContain('katex.min.css');
    expect(out.files['pages/about/index.html']).not.toContain('katex');
  });

  it('gives each page a share image, its thumbnail when it names one', () => {
    const later = out.files['posts/2026-10-02-later/index.html'];
    expect(later).toContain(`content="${SITE_URL}media/t.png"`);
    expect(post).toContain(`content="${SITE_URL}_scrive/og/posts--2026-10-01-priors.png"`);
    expect(out.cards.map((c) => c.path)).toContain('_scrive/og/posts--2026-10-01-priors.png');
    expect(out.cards.map((c) => c.path)).not.toContain('_scrive/og/posts--2026-10-02-later.png');
    expect(out.cards.find((c) => c.path.endsWith('priors.png'))?.kicker).toBe('2026-10-01');
  });

  it('lists posts newest first, with prev/next between them', () => {
    const home = out.files['index.html'];
    expect(home.indexOf('Later')).toBeLessThan(home.indexOf('Priors'));
    expect(home).toContain('href="posts/2026-10-01-priors/"');
    expect(post).toContain('Newer');
  });

  it('writes a feed and sitemap with absolute URLs, and a 404 that works anywhere', () => {
    expect(out.files['feed.xml']).toContain(`<link>${SITE_URL}posts/2026-10-01-priors/</link>`);
    expect(out.files['feed.xml']).toContain('<pubDate>Thu, 01 Oct 2026 00:00:00 GMT</pubDate>');
    expect(out.files['feed.xml']).toContain('<category>Bayes rule</category>');
    expect(out.files['sitemap.xml']).toContain(`<loc>${SITE_URL}pages/about/</loc>`);
    expect(out.files['404.html']).toContain(`href="${SITE_URL}_scrive/site.css"`);
  });

  it('shapes the page by layout', () => {
    expect(site('book').files['index.html']).toContain('class="site-toc"');
    expect(site('tactical').files['index.html']).toContain('class="site site-tactical"');
    const article = site('article');
    expect(article.files['index.html']).toContain('More');
    expect(article.files['index.html']).not.toContain('class="site-nav"');
  });

  it('inlines tokens and page styles, never the app-only KaTeX import', () => {
    const own = (rel: string) =>
      readFileSync(join(REPO, 'packages', 'core', 'src', 'modules', 'scrive', rel), 'utf8');
    const css = siteStylesheet(tokens('minimal'), {
      page: own('render/page.css'),
      site: own('site/site.css'),
    });
    expect(css).toContain('--accent');
    expect(css).toContain('.scrive-page');
    expect(css).toContain('.site-entry');
    expect(css).not.toContain('@import');
  });
});

describe('palette', () => {
  it('reads the default palette and the other scheme', () => {
    const { base, alt } = themePalettes(tokens('tactical'));
    expect(alt?.scheme).toBe('light');
    expect(base.bg).not.toBe(alt?.palette.bg);
    expect(alt?.palette['font-mono']).toBe(base['font-mono']); // inherited
    expect(mermaidVariables(base).primaryBorderColor).toBe(base.accent);
    expect(diagramCss(alt)).toContain('prefers-color-scheme: light');
    expect(themePalettes(':root { --bg: red; }').alt).toBeNull();
  });
});

describe('scrive-myst-plugin (Jupyter Book mode)', async () => {
  const pluginPath = join(REPO, 'backend', 'modules', 'scrive', 'static', 'scrive-myst-plugin.mjs');
  const plugin = (await import(/* @vite-ignore */ pathToFileURL(pluginPath).href)).default as {
    directives: Parameters<typeof mystParse>[1] extends infer O
      ? O extends { directives?: infer D }
        ? D
        : never
      : never;
  };
  const parse = (text: string) =>
    JSON.parse(JSON.stringify(mystParse(text, { directives: plugin.directives })));

  it('turns {r3f} into an iframe of the prebuilt scene page', () => {
    process.env.BASE_URL = '/blog';
    const tree = parse('```{r3f} scenes/orbit.tsx\n:params: {"speed": 3}\n```\n');
    const iframe = JSON.stringify(tree).match(/"type":"iframe"[^}]*/)?.[0] ?? '';
    expect(iframe).toContain(
      `"src":"/blog/_scrive/scene/scenes/orbit.html#${encodeURIComponent('{"speed":3}')}"`,
    );
    delete process.env.BASE_URL;
  });

  it('drops {pending} and draws {video} as media', () => {
    const tree = parse(':::{pending}\nTodo.\n:::\n\n```{video} media/a.mp4\n```\n');
    // mystmd replaces a directive with its children; {pending} has none.
    expect(tree.children[0]).toMatchObject({ name: 'pending', children: [] });
    expect(tree.children[1].children).toEqual([{ type: 'image', url: 'media/a.mp4' }]);
  });
});
