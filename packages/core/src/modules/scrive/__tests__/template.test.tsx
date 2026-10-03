/**
 * A site theme's own layouts: the `{{…}}` template language (escaping by default, the
 * three blocks, scope lookup, errors that name the line) and how the static build uses
 * a theme's templates — each one replaces its kind of file, the rest stay built-in.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { PageMeta } from '../api';
import { buildSite, type SourcePage } from '../site/build';
import { compile, render, TemplateError } from '../site/template';

const fill = (source: string, context: Record<string, unknown>) => render(compile(source), context);

describe('template', () => {
  it('escapes values unless they are asked for raw', () => {
    expect(fill('<h1>{{ title }}</h1>', { title: '<b>A & B</b>' })).toBe(
      '<h1>&lt;b&gt;A &amp; B&lt;/b&gt;</h1>',
    );
    expect(fill('{{{ content }}}', { content: '<p>hi</p>' })).toBe('<p>hi</p>');
    expect(
      fill('{{ page.author.name }}|{{ missing.deep }}', { page: { author: { name: 'Ada' } } }),
    ).toBe('Ada|');
  });

  it('chooses with if, unless and else', () => {
    const t = '{{#if date}}on {{date}}{{else}}undated{{/if}}·{{#unless prev}}first{{/unless}}';
    expect(fill(t, { date: '2026-10-01', prev: null })).toBe('on 2026-10-01·first');
    expect(fill(t, { date: '', prev: { title: 'x' } })).toBe('undated·');
    // An empty list is false.
    expect(fill('{{#if tags}}tagged{{else}}none{{/if}}', { tags: [] })).toBe('none');
  });

  it('repeats with each, looking in the item before the scope around it', () => {
    const t =
      '{{#each posts}}{{#if @first}}[{{/if}}{{title}} in {{site}}{{#unless @last}}, {{/unless}}{{#if @last}}]{{/if}}{{else}}none{{/each}}';
    expect(
      fill(t, { site: 'Notes', posts: [{ title: 'A' }, { title: 'B', site: 'Elsewhere' }] }),
    ).toBe('[A in Notes, B in Elsewhere]');
    expect(fill(t, { posts: [] })).toBe('none');
    expect(fill('{{#each tags}}{{this}}:{{@index}} {{/each}}', { tags: ['x', 'y'] })).toBe(
      'x:0 y:1 ',
    );
  });

  it('drops comments, including ones that contain braces', () => {
    expect(fill('a{{! note }}b{{!-- {{#if x}} --}}c', {})).toBe('abc');
  });

  it('names the file and line of a mistake', () => {
    expect(() => compile('<ul>\n{{#each posts}}\n<li>', 'layouts/home.html')).toThrow(
      /layouts\/home\.html:2: \{\{#each\}\} is never closed/,
    );
    expect(() => compile('{{#if a}}{{/each}}')).toThrow(TemplateError);
    expect(() => compile('{{ title() }}')).toThrow(/not a name/);
    expect(() => compile('{{> header}}')).toThrow(/unknown tag/);
  });
});

function page(path: string, content: string, meta: Partial<PageMeta> = {}): SourcePage {
  return {
    meta: {
      path,
      kind: path.startsWith('posts/') ? 'post' : 'page',
      title: path,
      status: 'published',
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

const REPO = join(__dirname, '..', '..', '..', '..', '..', '..');
const tokens = readFileSync(
  join(REPO, 'backend', 'modules', 'scrive', 'builtin_themes', 'minimal', 'tokens.css'),
  'utf8',
);

describe('a theme with its own layouts', () => {
  const build = buildSite({
    id: 'blog',
    title: 'Field <Notes>',
    year: 2026,
    theme: {
      layout: 'minimal',
      tokens,
      templates: {
        page: [
          '<article class="mine"><h1>{{page.title}}</h1>',
          '{{#if page.date}}<time>{{page.date}}</time>{{/if}}',
          '{{#each page.tags}}<a class="tag" href="{{url}}">{{title}}</a>{{/each}}',
          '{{{content}}}',
          '{{#if next}}<a rel="next" href="{{next.url}}">{{next.title}}</a>{{/if}}</article>',
        ].join(''),
        base: '<html><head>{{{head}}}</head><body class="themed">{{{body}}}<footer>{{site.title}} {{site.year}}</footer></body></html>',
      },
    },
    pages: [
      page('posts/a.md', '---\ntitle: A\n---\n\nAlpha **bold**.\n', {
        title: 'A',
        date: '2026-10-02',
        tags: ['stats'],
      }),
      page('posts/b.md', '---\ntitle: B\n---\n\nBeta.\n', { title: 'B', date: '2026-10-01' }),
    ],
  });

  it("draws a page with the theme's template, the body from the renderer", () => {
    const html = build.files['posts/a/index.html'];
    expect(html.startsWith('<!doctype html>\n<html><head>')).toBe(true);
    expect(html).toContain('<article class="mine"><h1>A</h1><time>2026-10-02</time>');
    expect(html).toContain('<a class="tag" href="../../tags/stats/">stats</a>');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<a rel="next" href="../b/">B</a>');
    // The head is Scrive's: title, share tags and the stylesheet, relative to the file.
    expect(html).toContain('<link rel="stylesheet" href="../../_scrive/site.css"/>');
    expect(html).toContain('<footer>Field &lt;Notes&gt; 2026</footer>');
  });

  it('keeps the built-in layout for files the theme has no template for', () => {
    // No home.html: the home page is the built-in document. `base.html` wraps only what
    // the theme's own templates draw.
    const home = build.files['index.html'];
    expect(home).toContain('class="site site-minimal"');
    expect(home).not.toContain('class="themed"');
  });

  it('wraps a fragment template in a document', () => {
    const out = buildSite({
      id: 'blog',
      title: 'T',
      theme: { layout: 'minimal', tokens, templates: { page: '<p>{{page.title}}</p>' } },
      pages: [page('pages/about.md', 'About.\n', { title: 'About' })],
    }).files['pages/about/index.html'];
    expect(out).toMatch(/^<!doctype html>\n<html lang="en"><head><meta charSet="utf-8"\/>/);
    expect(out).toContain('<body class="site site-minimal"><p>About</p></body>');
  });
});
