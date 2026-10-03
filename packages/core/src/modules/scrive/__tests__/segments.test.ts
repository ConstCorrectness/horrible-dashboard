/**
 * The golden round-trip corpus: the gate for the source-preserving writer.
 *
 * Every fixture, plus a CRLF and a no-final-newline variant of each generated here
 * (files on disk would lose those to editors and git), must:
 *
 * 1. come back byte-identical from split → join;
 * 2. come back byte-identical when every segment is "edited" to its own body;
 * 3. when one segment is edited, differ from the original in exactly that body.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { joinDoc, replaceBody, splitDoc } from '../myst/segments';

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

describe('golden corpus', () => {
  it('has fixtures', () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(15);
  });

  it.each(fixtures)('%s: split → join is byte-identical', (_name, text) => {
    expect(joinDoc(splitDoc(text))).toBe(text);
  });

  it.each(fixtures)('%s: an identity edit of every segment is byte-identical', (_name, text) => {
    let doc = splitDoc(text);
    for (const segment of doc.segments) doc = replaceBody(doc, segment.id, segment.body);
    expect(joinDoc(doc)).toBe(text);
  });

  it.each(fixtures)('%s: editing one block changes only that block', (_name, text) => {
    const doc = splitDoc(text);
    doc.segments.forEach((segment, i) => {
      if (segment.kind !== 'block') return;
      const before = doc.segments
        .slice(0, i)
        .map((s) => s.body + s.tail)
        .join('');
      const after = doc.segments
        .slice(i + 1)
        .map((s) => s.body + s.tail)
        .join('');
      const out = joinDoc(replaceBody(doc, segment.id, 'EDITED'));
      const eol = /\r\n/.test(text) ? '\r\n' : '\n';
      const ending = /\r?\n$/.test(segment.body) || segment.tail ? eol : '';
      expect(out).toBe(before + 'EDITED' + ending + segment.tail + after);
    });
  });
});

describe('segmentation', () => {
  const book = readFileSync(join(CORPUS, 'jupyter-book.md'), 'utf8');

  it('cuts one segment per top-level block, frontmatter first', () => {
    const doc = splitDoc(book);
    expect(doc.segments[0].kind).toBe('frontmatter');
    expect(doc.segments[0].body.startsWith('---')).toBe(true);
    const types = doc.segments.filter((s) => s.node).map((s) => s.node!.type);
    expect(types).toContain('mystTarget');
    expect(types).toContain('heading');
    expect(types).toContain('mystDirective');
    expect(types).toContain('table');
  });

  it('gives a +++ marker its own segment and keeps its JSON', () => {
    const marker = splitDoc(book).segments.find((s) => s.kind === 'break');
    expect(marker?.body).toBe('+++ {"part": "model"}\n');
  });

  it('reports file line numbers, frontmatter included', () => {
    const doc = splitDoc(book);
    const lines = book.split('\n');
    for (const s of doc.segments) {
      if (s.kind === 'block' || s.kind === 'break') {
        expect(lines[s.line - 1]).toBe(s.body.split('\n')[0]);
      }
    }
  });

  it('keeps footnote and reference definitions when the block before them is edited', () => {
    const text = 'Footnotes[^a] and [links][r].\n\n[^a]: The note.\n\n[r]: https://x.y\n\nNext.\n';
    const doc = splitDoc(text);
    const first = doc.segments.find((s) => s.body.startsWith('Footnotes'))!;
    const out = joinDoc(replaceBody(doc, first.id, 'Rewritten[^a] and [links][r].'));
    expect(out).toBe(
      'Rewritten[^a] and [links][r].\n\n[^a]: The note.\n\n[r]: https://x.y\n\nNext.\n',
    );
  });

  it('never cuts a final directive at a hoisted footnote definition', () => {
    // The parser reports the definition at the document's last line — inside the
    // directive that ends the file.
    const text = 'A[^a].\n\n[^a]: Note.\n\n```{r3f} s.tsx\n:height: 420\n:params: {}\n```\n';
    const last = splitDoc(text).segments.at(-1)!;
    expect(last.body).toBe('```{r3f} s.tsx\n:height: 420\n:params: {}\n```\n');
  });

  it('writes an edited body back in the file’s own line ending', () => {
    const doc = splitDoc('# A\r\n\r\nPara\r\n');
    const para = doc.segments.find((s) => s.body.startsWith('Para'))!;
    expect(joinDoc(replaceBody(doc, para.id, 'Two\nlines'))).toBe('# A\r\n\r\nTwo\r\nlines\r\n');
  });

  it('handles an empty file and a frontmatter-only file', () => {
    expect(splitDoc('').segments).toEqual([]);
    const fm = splitDoc('---\ntitle: x\n---\n');
    expect(fm.segments.map((s) => s.kind)).toEqual(['frontmatter']);
  });
});
