/**
 * Edit one frontmatter field without re-dumping the rest.
 *
 * The properties inspector changes `title`, `status`, `tags`… one at a time. Dumping
 * the whole YAML back would drop the author's comments, key order and quoting, and
 * turn a one-word change into a rewritten block. So a field is edited at the line
 * level: its own lines (the `key:` line and anything indented or listed under it)
 * are replaced, and every other line is left as it was.
 *
 * Only top-level keys are addressed. Values are written by `js-yaml` on the JSON
 * schema, as one line: a string that YAML would read as something else (`true`,
 * `1.0`, `a: b`) comes out quoted, and a list comes out in flow style (`[a, b]`).
 */
import yaml from 'js-yaml';

import { splitFrontmatter } from './parse';

export type FieldValue = string | number | boolean | string[] | null;

const OPEN = /^---[ \t]*\r?\n/;

/** One YAML line for `value`. */
export function yamlValue(value: Exclude<FieldValue, null>): string {
  return yaml
    .dump(value, { schema: yaml.JSON_SCHEMA, flowLevel: 0, lineWidth: -1 })
    .replace(/\r?\n$/, '');
}

/** Whether a line belongs to the field above it: indented, a `- item` of a block
 * sequence, or blank inside a block scalar. */
const continues = (line: string) => /^[ \t]/.test(line) || /^-(?:[ \t]|$)/.test(line);

/**
 * `raw` (a frontmatter block, delimiters included, or `''`) with `key` set to
 * `value` — or removed, for `null`. A missing key is appended before the closing
 * `---`; a file with no frontmatter gets one.
 */
export function setField(raw: string, key: string, value: FieldValue): string {
  const eol = /\r\n/.test(raw) ? '\r\n' : '\n';
  const line = value === null ? null : `${key}: ${yamlValue(value)}`;
  if (!raw || !OPEN.test(raw)) {
    return line === null ? raw : `---${eol}${line}${eol}---${eol}`;
  }
  const lines = raw.split(/\r?\n/);
  // `raw` ends with the closing delimiter and (usually) a line ending.
  const close = lines.findIndex((l, i) => i > 0 && /^---[ \t]*$/.test(l));
  if (close < 0) return raw;
  const keyLine = new RegExp(`^${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[ \\t]*:`);
  const start = lines.findIndex((l, i) => i > 0 && i < close && keyLine.test(l));
  if (start < 0) {
    if (line === null) return raw;
    lines.splice(close, 0, line);
    return lines.join(eol);
  }
  let end = start + 1;
  while (end < close && continues(lines[end])) end++;
  lines.splice(start, end - start, ...(line === null ? [] : [line]));
  return lines.join(eol);
}

/** A whole page's text with one frontmatter field set (see `setField`). */
export function withField(content: string, key: string, value: FieldValue): string {
  const fm = splitFrontmatter(content);
  const next = setField(fm.raw, key, value);
  // A page that had no frontmatter gets one, a blank line above its body.
  if (fm.raw || next === '') return next + fm.body;
  return `${next}${fm.body ? '\n' : ''}${fm.body}`;
}
