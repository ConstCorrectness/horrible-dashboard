/** Frontmatter field edits: one field's lines change, every other line stays. */
import { describe, expect, it } from 'vitest';

import { setField, withField, yamlValue } from '../myst/frontmatter';
import { readFrontmatter } from '../myst/parse';

const FM = [
  '---',
  '# The author’s comment',
  'title: "Old: title"',
  'date: 2026-10-01',
  'tags:',
  '  - a',
  '  - b',
  'authors:',
  '- name: X',
  '  orcid: 1',
  'status: draft',
  '---',
  '',
].join('\n');

describe('setField', () => {
  it('replaces a scalar in place, keeping comments and order', () => {
    const out = setField(FM, 'status', 'published');
    expect(out).toBe(FM.replace('status: draft', 'status: published'));
  });

  it('replaces a block list with a flow list', () => {
    const out = setField(FM, 'tags', ['x', 'y z']);
    expect(out).toContain('tags: [x, y z]\nauthors:');
    expect(out).not.toContain('  - a');
    expect(readFrontmatter(out).tags).toEqual(['x', 'y z']);
  });

  it('removes a field and everything under it', () => {
    const out = setField(FM, 'authors', null);
    expect(out).not.toContain('authors');
    expect(out).not.toContain('orcid');
    expect(out).toContain('status: draft');
  });

  it('appends a missing field before the closing line', () => {
    expect(setField(FM, 'description', 'Hi')).toContain('status: draft\ndescription: Hi\n---\n');
  });

  it('quotes what YAML would misread', () => {
    expect(yamlValue('a: b')).toBe("'a: b'");
    expect(yamlValue('true')).toBe("'true'");
    expect(yamlValue('2026-10-01')).toBe('2026-10-01');
    const out = setField(FM, 'title', 'New: title');
    expect(readFrontmatter(out).title).toBe('New: title');
  });

  it('creates a frontmatter, and keeps CRLF', () => {
    expect(setField('', 'title', 'T')).toBe('---\ntitle: T\n---\n');
    const crlf = FM.replace(/\n/g, '\r\n');
    expect(setField(crlf, 'status', 'review')).toBe(
      crlf.replace('status: draft', 'status: review'),
    );
  });

  it('does not match a key that only starts the same', () => {
    const raw = '---\ntitles: x\ntitle: y\n---\n';
    expect(setField(raw, 'title', 'z')).toBe('---\ntitles: x\ntitle: z\n---\n');
  });
});

describe('withField', () => {
  it('edits a page’s frontmatter and leaves its body', () => {
    const page = '---\ntitle: A\nstatus: draft\n---\n\n# Body\n';
    expect(withField(page, 'status', 'review')).toBe(page.replace('draft', 'review'));
    expect(withField('# Body\n', 'status', 'draft')).toBe('---\nstatus: draft\n---\n\n# Body\n');
  });
});
