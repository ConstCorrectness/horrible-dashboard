/**
 * The block editor's document model: mdast ⇄ editor JSON, the MyST printer, and
 * `openDoc` / `writeDoc` against the golden corpus.
 *
 * The gate, for every fixture in LF, CRLF and no-final-newline form:
 *
 * 1. open → write with nothing changed is byte-identical;
 * 2. forcing any one rich block to be re-printed leaves the document's MyST meaning
 *    unchanged, and every byte outside that block where it was.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { openDoc, sameJson, writeDoc, type DocBase } from '../myst/doc';
import { parseMyst, splitFrontmatter, type MystNode } from '../myst/parse';
import { blockFromMdast, editableBlock, printBlock, type PMNode } from '../myst/pm';

const CORPUS = join(__dirname, 'corpus');

const fixtures: [string, string][] = readdirSync(CORPUS)
  .filter((f) => f.endsWith('.md'))
  .sort()
  .flatMap((name) => {
    const text = readFileSync(join(CORPUS, name), 'utf8');
    return [
      [name, text],
      [`${name} (CRLF)`, text.replace(/\r?\n/g, '\r\n')],
      [`${name} (no final newline)`, text.replace(/\r?\n$/, '')],
    ] as [string, string][];
  });

const baselineOf = (doc: PMNode) =>
  new Map((doc.content ?? []).map((b) => [String(b.attrs?.seg), b] as const));

/** A document's meaning: its parse, positions and soft-break layout aside. */
function meaning(text: string): unknown {
  const strip = (n: unknown): unknown => {
    if (Array.isArray(n)) return n.map(strip);
    if (!n || typeof n !== 'object') return n;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(n as MystNode)) {
      // `id`: code-cell outputs get a random one per parse.
      if (k === 'position' || k === 'key' || k === 'html_id' || k === 'id') continue;
      if (k === 'value' && (n as MystNode).type === 'mystDirective' && (n as MystNode).children)
        continue;
      out[k] =
        k === 'value' && (n as MystNode).type === 'text' && typeof v === 'string'
          ? v.replace(/[ \t]*\r?\n[ \t]*/g, ' ')
          : strip(v);
    }
    return out;
  };
  const body = splitFrontmatter(text.replace(/\r\n/g, '\n')).body;
  // `block` wrappers group by `+++`; flatten so a blank line moved does not matter.
  const flat = parseMyst(body).children.flatMap((c) =>
    c.type === 'block' ? (c.children ?? []) : [c],
  );
  return strip(flat);
}

/** Open `text`, then print `text` with the block at `i` forced through the printer. */
function reprint(text: string, i: number): { out: string; base: DocBase; doc: PMNode } {
  const { doc, base } = openDoc(text);
  const baseline = baselineOf(doc);
  baseline.delete(String(doc.content?.[i].attrs?.seg));
  return { out: writeDoc(doc, base, baseline), base, doc };
}

describe('openDoc / writeDoc: golden corpus', () => {
  it.each(fixtures)('%s: open → write unchanged is byte-identical', (_name, text) => {
    const { doc, base } = openDoc(text);
    expect(writeDoc(doc, base, baselineOf(doc))).toBe(text);
  });

  it.each(fixtures)('%s: re-printing any rich block keeps its meaning', (_name, text) => {
    const { doc } = openDoc(text);
    (doc.content ?? []).forEach((block, i) => {
      if (block.type === 'mystBlock') return;
      const { out } = reprint(text, i);
      expect(meaning(out), `block ${i} (${block.type})`).toEqual(meaning(text));
    });
  });

  it('opens most of the Jupyter Book sample as rich blocks', () => {
    const { doc } = openDoc(readFileSync(join(CORPUS, 'jupyter-book.md'), 'utf8'));
    const types = (doc.content ?? []).map((b) => b.type);
    expect(types).toContain('heading');
    expect(types).toContain('paragraph');
    expect(types).toContain('admonition');
    expect(types).toContain('mathBlock');
    expect(types).toContain('table');
    // Directives the editor does not model stay raw, verbatim.
    const raw = (doc.content ?? []).filter((b) => b.type === 'mystBlock');
    expect(raw.length).toBeGreaterThan(0);
    expect(raw.some((b) => String(b.attrs?.source).includes('{figure}'))).toBe(true);
    expect(types).toContain('codeCell');
  });
});

describe('writeDoc: edits', () => {
  const text = '---\ntitle: T\n---\n\n# One\n\nPara *a*\nwrapped.\n\n[r]: https://x.y\n\nLast.\n';

  it('rewrites only the edited block, keeping its tail', () => {
    const { doc, base } = openDoc(text);
    const baseline = baselineOf(doc);
    const content = doc.content!.slice();
    content[1] = { ...content[1], content: [{ type: 'text', text: 'New para' }] };
    expect(writeDoc({ ...doc, content }, base, baseline)).toBe(
      '---\ntitle: T\n---\n\n# One\n\nNew para\n\n[r]: https://x.y\n\nLast.\n',
    );
  });

  it('separates an inserted block from its neighbours', () => {
    const { doc, base } = openDoc('A\n\nB\n');
    const content = doc.content!.slice();
    content.splice(1, 0, { type: 'paragraph', content: [{ type: 'text', text: 'New' }] });
    expect(writeDoc({ ...doc, content }, base, baselineOf(doc))).toBe('A\n\nNew\n\nB\n');
  });

  it('appends after an unterminated last line', () => {
    const { doc, base } = openDoc('A');
    const content = [
      ...doc.content!,
      { type: 'paragraph', content: [{ type: 'text', text: 'B' }] },
    ];
    expect(writeDoc({ ...doc, content }, base, baselineOf(doc))).toBe('A\n\nB\n');
  });

  it('keeps a deleted block’s definitions', () => {
    const { doc, base } = openDoc(text);
    const content = doc.content!.filter((_, i) => i !== 1);
    const out = writeDoc({ ...doc, content }, base, baselineOf(doc));
    expect(out).toContain('[r]: https://x.y');
    expect(out).not.toContain('Para');
    expect(out).toContain('Last.');
  });

  it('writes CRLF files in CRLF', () => {
    const crlf = 'A\r\n\r\nB\r\n';
    const { doc, base } = openDoc(crlf);
    const content = doc.content!.slice();
    content[0] = { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: 'H' }] };
    expect(writeDoc({ ...doc, content }, base, baselineOf(doc))).toBe('## H\r\n\r\nB\r\n');
  });

  it('an emptied block is a deletion, and a trailing empty paragraph writes nothing', () => {
    const { doc, base } = openDoc('A\n\nB\n');
    const content = [{ ...doc.content![0], content: [] }, doc.content![1], { type: 'paragraph' }];
    expect(writeDoc({ ...doc, content }, base, baselineOf(doc))).toBe('B\n');
  });

  it('a block split off its tag keeps its original bytes', () => {
    // Enter at the start of a paragraph: the tag stays on the new empty half.
    const text = 'A\n\nWrapped\nlines.\n';
    const { doc, base } = openDoc(text);
    const para = doc.content![1];
    const untaggedPara = { ...para, attrs: { ...para.attrs, seg: null } };
    const content = [doc.content![0], { type: 'paragraph', attrs: para.attrs }, untaggedPara];
    expect(writeDoc({ ...doc, content }, base, baselineOf(doc))).toBe(text);
  });

  it('a dragged block moves its own bytes', () => {
    const text = '# H\n\nWrapped\nfirst.\n\nSecond *para*.\n';
    const { doc, base } = openDoc(text);
    const [h, first, second] = doc.content!;
    expect(writeDoc({ ...doc, content: [h, second, first] }, base, baselineOf(doc))).toBe(
      '# H\n\nSecond *para*.\n\nWrapped\nfirst.\n',
    );
  });

  it('a copied block is new text, not a second original', () => {
    const { doc, base } = openDoc('A\n\nB\n');
    const content = [...doc.content!, doc.content![0]];
    expect(writeDoc({ ...doc, content }, base, baselineOf(doc))).toBe('A\n\nB\n\nA\n');
  });

  it('takes an edited frontmatter, and adds one to a file without', () => {
    const { doc, base } = openDoc(text);
    expect(writeDoc(doc, base, baselineOf(doc), '---\ntitle: U\n---\n')).toBe(
      text.replace('title: T', 'title: U'),
    );
    const bare = openDoc('Body.\n');
    expect(writeDoc(bare.doc, bare.base, baselineOf(bare.doc), '---\ntitle: X\n---\n')).toBe(
      '---\ntitle: X\n---\n\nBody.\n',
    );
  });
});

describe('printer', () => {
  const block = (src: string) => parseMyst(src).children[0];
  const roundTrip = (src: string) => printBlock(blockFromMdast(block(src), src));

  it.each([
    'Some *em*, **strong**, `code` and $x^2$.',
    'A [link](https://a.b "t") and ![alt](img.png) and {ref}`sec`.',
    '- a\n- b\n  - nested',
    '3. three\n4. four',
    '- [ ] todo\n- [x] done',
    '> quote\n>\n> more',
    '```python\nprint(1)\n```',
    '$$\nE = mc^2\n$$ (eq-label)',
    ':::{note} Title here\n:class: dropdown\n\nBody **bold**.\n:::',
    '| a | b |\n| :- | -: |\n| 1 | 2 |',
    'Line one\\\nline two',
    ':::{dropdown} Derivation\n:open:\nBody *x*.\n:::',
    '```{code-cell} python\n:tags: [hide-input]\nimport numpy as np\nnp.pi\n```',
    '```{code-cell}\nx = 1\n```',
    '***',
    '---',
  ])('round-trips %j', (src) => {
    const out = roundTrip(src);
    expect(editableBlock(block(src), src), `not editable: ${src}`).not.toBeNull();
    expect(parseMyst(out).children[0]).toBeTruthy();
    expect(meaning(out)).toEqual(meaning(src));
  });

  it('escapes MyST syntax in plain text', () => {
    const p: PMNode = {
      type: 'paragraph',
      content: [{ type: 'text', text: 'costs $5 and $6, {ref}`x` is literal' }],
    };
    const out = printBlock(p);
    const tree = parseMyst(out).children[0];
    expect(tree.children?.map((c) => c.type)).toEqual(['text']);
    expect(String(tree.children?.[0].value)).toBe('costs $5 and $6, {ref}`x` is literal');
  });

  it('escapes a % that would open a comment', () => {
    const p: PMNode = { type: 'paragraph', content: [{ type: 'text', text: '% not a comment' }] };
    expect(parseMyst(printBlock(p)).children[0].type).toBe('paragraph');
  });

  it('fences a directive longer than anything nested in it', () => {
    const inner: PMNode = {
      type: 'admonition',
      attrs: { name: 'tip', args: '', options: {}, fence: ':' },
      content: [{ type: 'paragraph', content: [{ type: 'text', text: 'in' }] }],
    };
    const outer: PMNode = { ...inner, attrs: { ...inner.attrs, name: 'note' }, content: [inner] };
    const out = printBlock(outer);
    expect(out.startsWith('::::{note}')).toBe(true);
    const tree = parseMyst(out).children[0];
    expect(tree.type).toBe('mystDirective');
    expect(tree.children?.[0].children?.[0].type).toBe('mystDirective');
  });

  it('keeps a backtick-fenced directive in backticks', () => {
    const src = '```{warning}\nPlain\n```';
    expect(roundTrip(src)).toBe(src);
  });

  it('prints lists tight: MyST reads a loose list the same', () => {
    expect(roundTrip('- a\n- b')).toBe('- a\n- b');
    expect(roundTrip('- a\n\n- b')).toBe('- a\n- b');
    expect(meaning('- a\n\n- b')).toEqual(meaning('- a\n- b'));
  });

  it('opens an {r3f} scene as a block and prints it back as written', () => {
    const src = '```{r3f} scenes/posterior.tsx\n:height: 420\n:params: {"samples": 500}\n```';
    const node = parseMyst(src).children[0];
    const pm = editableBlock(node, src);
    expect(pm?.type).toBe('r3fScene');
    expect(pm?.attrs?.options).toEqual({ height: '420', params: '{"samples": 500}' });
    expect(printBlock(pm!)).toBe(src);
  });

  it('opens a {space} embed as a block and prints it back as written', () => {
    const src =
      '```{space} webml-community/smollm-webgpu\n:height: 640\n:host: webml-community-smollm-webgpu.static.hf.space\n```';
    const node = parseMyst(src).children[0];
    const pm = editableBlock(node, src);
    expect(pm?.type).toBe('scriveEmbed');
    expect(pm?.attrs).toMatchObject({ name: 'space', src: 'webml-community/smollm-webgpu' });
    expect(pm?.attrs?.options).toEqual({
      height: '640',
      host: 'webml-community-smollm-webgpu.static.hf.space',
    });
    expect(printBlock(pm!)).toBe(src);
    // A colon fence opens the same block; a body keeps it raw.
    const colon = ':::{space} a/b\n:::';
    expect(editableBlock(parseMyst(colon).children[0], colon)?.type).toBe('scriveEmbed');
    const withBody = '```{space} a/b\nwords\n```';
    expect(editableBlock(parseMyst(withBody).children[0], withBody)).toBeNull();
  });

  it('keeps a {math} directive a directive', () => {
    const src = '```{math}\n:label: eq:a\nx = 1\n```';
    expect(roundTrip(src)).toBe(src);
    expect(roundTrip('$$\nx = 1\n$$ (eq:a)')).toBe('$$\nx = 1\n$$ (eq:a)');
  });
});

describe('fidelity', () => {
  it('keeps raw what the editor cannot hold', () => {
    const cases = ['```{figure} a.png\nCaption.\n```', '(label)=', '% comment', 'Term\n: Def'];
    for (const src of cases) {
      const node = parseMyst(src).children[0];
      expect(editableBlock(node, src), src).toBeNull();
    }
  });

  it('a paragraph with a footnote reference is editable', () => {
    const src = 'See [^1].\n\n[^1]: Note.';
    const node = parseMyst(src).children[0];
    expect(editableBlock(node, 'See [^1].')).not.toBeNull();
  });

  it('sameJson ignores key order and undefined', () => {
    expect(sameJson({ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1, d: undefined })).toBe(
      true,
    );
    expect(sameJson({ a: 1 }, { a: 2 })).toBe(false);
  });
});
