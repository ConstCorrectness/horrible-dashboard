/**
 * Which blocks an edit changed — for the agent-edit review strip.
 *
 * Both texts are cut into segments (`segments.ts`) and compared by body: a block of
 * the new text whose exact source was not in the old text is *changed* (new or
 * edited); old blocks gone beyond those are *removed*. Comparison is a multiset,
 * so a block that merely moved, or a paragraph that appears twice, is not counted as
 * new. Frontmatter is compared as one block and reported apart, since it has no
 * place in the editor's body.
 */
import { splitDoc } from './segments';

export interface BlockChanges {
  /** File lines (1-based) where changed blocks of the new text start. */
  lines: number[];
  changed: number;
  removed: number;
  frontmatter: boolean;
}

const norm = (body: string) => body.replace(/\r\n/g, '\n').replace(/\s+$/, '');

export function blockChanges(before: string, after: string): BlockChanges {
  const old = splitDoc(before).segments;
  const now = splitDoc(after).segments;
  const fm = (segments: typeof old) =>
    norm(segments.find((s) => s.kind === 'frontmatter')?.body ?? '');
  const pool = new Map<string, number>();
  for (const s of old) {
    if (s.kind !== 'block' && s.kind !== 'break') continue;
    const key = norm(s.body);
    pool.set(key, (pool.get(key) ?? 0) + 1);
  }
  const lines: number[] = [];
  for (const s of now) {
    if (s.kind !== 'block' && s.kind !== 'break') continue;
    const key = norm(s.body);
    const left = pool.get(key) ?? 0;
    if (left > 0) pool.set(key, left - 1);
    else lines.push(s.line);
  }
  // An edited block is one gone and one new; only what is gone beyond the new
  // blocks that replaced it counts as removed.
  const gone = [...pool.values()].reduce((sum, n) => sum + n, 0);
  return {
    lines,
    changed: lines.length,
    removed: Math.max(0, gone - lines.length),
    frontmatter: fm(old) !== fm(now),
  };
}
