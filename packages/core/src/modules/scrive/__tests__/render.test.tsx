/**
 * The renderer: link/embed safety, MyST path resolution, directive bodies, and that
 * every corpus fixture renders — with unknown directives shown, never dropped.
 *
 * Rendered with `react-dom/server` (core's vitest has no DOM). `sanitizeHtml` needs
 * `DOMParser`, so raw HTML renders empty here; it is exercised in the browser.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { parseMyst, readFrontmatter, splitFrontmatter } from '../myst/parse';
import {
  assetUrl,
  relativeTo,
  safeEmbedSrc,
  safeHref,
  slug,
  splitDirectiveBody,
} from '../render/directives';
import { notebookTree } from '../myst/notebook';
import { groupInlineHtml } from '../render/inline-html';
import { MystView } from '../render/MystView';

const CORPUS = join(__dirname, 'corpus');

function render(text: string, pagePath = 'posts/p.md'): string {
  const fm = splitFrontmatter(text);
  return renderToStaticMarkup(
    <MystView tree={parseMyst(fm.body)} site="blog" pagePath={pagePath} lineOffset={fm.lines} />,
  );
}

describe('safeHref', () => {
  it.each([
    ['https://x.y/a', 'https://x.y/a'],
    ['mailto:a@b.c', 'mailto:a@b.c'],
    ['#sec', '#sec'],
    ['../other.md', '../other.md'],
    ['page', 'page'],
  ])('keeps %s', (input, out) => expect(safeHref(input)).toBe(out));

  it.each([
    'javascript:alert(1)',
    'JAVASCRIPT:alert(1)',
    'java\tscript:alert(1)',
    'data:text/html,x',
    'vbscript:x',
  ])('refuses %s', (input) => expect(safeHref(input)).toBeUndefined());

  it('embeds only https', () => {
    expect(safeEmbedSrc('https://www.youtube.com/embed/x')).toBe('https://www.youtube.com/embed/x');
    expect(safeEmbedSrc('http://x.y')).toBeUndefined();
    expect(safeEmbedSrc('javascript:x')).toBeUndefined();
  });
});

describe('assetUrl', () => {
  const path = (url: string) => decodeURIComponent(url.split('path=')[1] ?? '');

  it('resolves relative to the page, and / to the site root, as MyST does', () => {
    expect(path(assetUrl('blog', 'posts/p.md', 'fig.png'))).toBe('posts/fig.png');
    expect(path(assetUrl('blog', 'posts/p.md', '../media/fig.png'))).toBe('media/fig.png');
    expect(path(assetUrl('blog', 'posts/p.md', '/media/fig.png'))).toBe('media/fig.png');
    expect(path(assetUrl('blog', 'index.md', './media/a b.png'))).toBe('media/a b.png');
  });

  it('cannot climb above the site root', () => {
    expect(path(assetUrl('blog', 'posts/p.md', '../../../etc/passwd'))).toBe('etc/passwd');
  });

  it('writes an uploaded file’s path relative to the page', () => {
    expect(relativeTo('posts/p.md', 'media/x.png')).toBe('../media/x.png');
    expect(relativeTo('index.md', 'media/x.png')).toBe('media/x.png');
    expect(relativeTo('posts/p.md', 'posts/media/x.png')).toBe('media/x.png');
    expect(relativeTo('a/b/p.md', 'a/c/x.png')).toBe('../c/x.png');
  });

  it('leaves web URLs alone', () => {
    expect(assetUrl('blog', 'p.md', 'https://cdn.x/a.png')).toBe('https://cdn.x/a.png');
  });
});

describe('splitDirectiveBody', () => {
  it('separates leading options from the body', () => {
    expect(splitDirectiveBody(':height: 400\n:params: {"a": 1}\n\nBody\nmore')).toEqual({
      options: { height: '400', params: '{"a": 1}' },
      body: 'Body\nmore',
    });
    expect(splitDirectiveBody('No options')).toEqual({ options: {}, body: 'No options' });
  });

  it('slugs headings like MyST and GitHub', () => {
    expect(slug('The *model*: v2!')).toBe('the-model-v2');
  });
});

describe('frontmatter data', () => {
  it('keeps dates as the strings the author typed', () => {
    const fm = splitFrontmatter('---\ntitle: "A: B"\ndate: 2026-10-01\ntags: [x, y]\n---\nbody');
    expect(readFrontmatter(fm.raw)).toEqual({
      title: 'A: B',
      date: '2026-10-01',
      tags: ['x', 'y'],
    });
    expect(readFrontmatter('---\ntitle: [unclosed\n---\n')).toEqual({});
  });
});

describe('MystView', () => {
  const fixtures = readdirSync(CORPUS).filter((f) => f.endsWith('.md'));

  it.each(fixtures)('renders %s', (name) => {
    expect(() => render(readFileSync(join(CORPUS, name), 'utf8'))).not.toThrow();
  });

  it('renders the Jupyter Book sample’s vocabulary', () => {
    const html = render(readFileSync(join(CORPUS, 'jupyter-book.md'), 'utf8'));
    expect(html).toContain('class="katex'); // inline + display math
    expect(html).toContain('scrive-admonition is-note');
    expect(html).toMatch(/<details class="scrive-admonition is-note">/); // :class: dropdown
    expect(html).toContain('code cell · python');
    expect(html).toContain('<table>');
    expect(html).toContain('scrive-tabs'); // tab-set
    expect(html).toContain('scrive-figure');
    expect(html).toContain(encodeURIComponent('posts/media/dag.png'));
    expect(html).toContain('scrive-footnotes');
    expect(html).toContain('https://mystmd.org/guide'); // reference link resolved
    expect(html).toContain('3D scene'); // r3f placeholder
    expect(html).not.toContain('A comment the reader never sees');
  });

  it('shows an unknown directive as written instead of dropping it', () => {
    const html = render(readFileSync(join(CORPUS, 'directives-nested.md'), 'utf8'));
    expect(html).toContain('{unknown-directive}');
    expect(html).toContain('Body that Scrive does not know.');
    expect(html).toContain('scrive-grid');
    expect(html).toContain('scrive-mermaid');
    expect(html).toContain('<iframe');
    expect(html).toMatch(
      /sandbox="allow-scripts allow-same-origin allow-presentation allow-popups"/,
    );
  });

  it('tags top-level blocks with their file line, frontmatter included', () => {
    const html = render('---\ntitle: x\n---\n\n# Head\n\nPara.\n');
    expect(html).toContain('data-line="5"');
    expect(html).toContain('data-line="7"');
  });

  it('never makes a javascript: link', () => {
    // markdown-it refuses the link (it stays literal text); raw HTML is sanitized.
    const html = render('[x](javascript:alert(1)) and <a href="javascript:alert(2)">y</a>');
    expect(html).not.toMatch(/href="\s*javascript:/i);
  });

  it('renders a notebook page from its cells and stored outputs', () => {
    const nb = JSON.stringify({
      metadata: { kernelspec: { language: 'python' } },
      cells: [
        { cell_type: 'markdown', source: ['# Results\n', 'Mean is $\\mu$.'] },
        {
          cell_type: 'code',
          source: 'df.head()',
          execution_count: 3,
          outputs: [
            { output_type: 'execute_result', data: { 'text/plain': 'frame' }, metadata: {} },
            {
              output_type: 'display_data',
              data: { 'image/svg+xml': '<svg><script>x()</script></svg>' },
              metadata: {},
            },
          ],
        },
      ],
    });
    const html = renderToStaticMarkup(
      <MystView tree={notebookTree(nb).tree} site="blog" pagePath="nb/a.ipynb" />,
    );
    expect(html).toContain('Results');
    expect(html).toContain('class="katex');
    expect(html).toContain('[3]');
    expect(html).toContain('frame');
    // SVG output is an image, never inline markup.
    expect(html).toContain('data:image/svg+xml;base64,');
    expect(html).not.toContain('<script');
    expect(notebookTree('not json').tree.children[0].type).toBe('scriveParseError');
  });

  it('keeps raw inline HTML inline', () => {
    expect(render('Press <kbd>K</kbd>.')).not.toMatch(/<p>[^]*<div/);
    expect(render('- tight <kbd>K</kbd>\n- list')).not.toMatch(/<li>[^]*<div/);
  });
});

describe('groupInlineHtml', () => {
  /** The paragraph's children as `html` strings for runs, `type:value` otherwise. */
  function groups(source: string): string[] {
    const para = parseMyst(source).children[0];
    return groupInlineHtml(para.children ?? []).map((item) =>
      'html' in item ? item.html : `${item.node.type}:${item.node.value ?? ''}`,
    );
  }

  it('puts a tag’s text back inside it', () => {
    // The tour page's line: handlers survive grouping and are stripped by sanitizeHtml.
    expect(
      groups(
        'Press <kbd>Ctrl</kbd>+<kbd>S</kbd> to save. <span onclick="alert(1)">Handlers are stripped.</span>',
      ),
    ).toEqual([
      'text:Press ',
      '<kbd>Ctrl</kbd>',
      'text:+',
      '<kbd>S</kbd>',
      'text: to save. ',
      '<span onclick="alert(1)">Handlers are stripped.</span>',
    ]);
  });

  it('escapes the text it serializes', () => {
    expect(groups('<b>a &lt; b &amp;&lt;script&gt;</b>')).toEqual([
      '<b>a &lt; b &amp;&lt;script&gt;</b>',
    ]);
  });

  it('carries nested tags and plain phrasing inside a run', () => {
    expect(groups('<b>x *em* `<c>` <b>in</b> <i>y</i></b> z')).toEqual([
      '<b>x <em>em</em> <code>&lt;c&gt;</code> <b>in</b> <i>y</i></b>',
      'text: z',
    ]);
  });

  it('leaves unmatched, void, and non-phrasing runs as separate nodes', () => {
    expect(groups('<kbd>open <br> </i>')).toEqual([
      'html:<kbd>',
      'text:open ',
      'html:<br>',
      'text: ',
      'html:</i>',
    ]);
    // A link needs the renderer (safeHref, published link rewriting), not a string.
    expect(groups('<a href="https://a.b">[l](https://c.d)</a>')).toEqual([
      'html:<a href="https://a.b">',
      'link:',
      'html:</a>',
    ]);
  });
});
