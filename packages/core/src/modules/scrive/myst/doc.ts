/**
 * The block editor's document ⇄ the file, without reformatting anything the author
 * did not touch.
 *
 * `openDoc` cuts the file into segments (`segments.ts`) and makes one top-level
 * editor block per segment, tagged with the segment's id (`attrs.seg`). A segment
 * the editor cannot represent losslessly (`pm.ts` → `editableBlock`) becomes a
 * `mystBlock`, which holds its source as text.
 *
 * `writeDoc` goes back. For each top-level block, in order:
 *
 * - **unchanged** (deep-equal to the block as it was opened): the segment's original
 *   bytes, body and tail;
 * - **edited**: the block printed as MyST in place of the body — the tail (blank
 *   lines, reference and footnote definitions) is kept;
 * - **new** (no segment, or a second copy of one): the block printed.
 *
 * A segment whose block was deleted loses its body, but its tail survives if it holds
 * anything but blank lines — a `[ref]: url` definition another paragraph still uses.
 *
 * Wherever the output departs from the original file's order (an insert, a move, a
 * deletion, an edit) the two blocks either side are kept a blank line apart, so a
 * new paragraph can never run into its neighbour.
 */
import { detectEol } from '../eol';
import { splitDoc, type Segment } from './segments';
import { editableBlock, printBlock, type PMNode } from './pm';

export interface OpenedDoc {
  /** The editor document: `{ type: 'doc', content: [...] }`. */
  doc: PMNode;
  /** What `writeDoc` needs to put the file back together. */
  base: DocBase;
}

export interface DocBase {
  /** The frontmatter block verbatim (`''` if none). The properties inspector edits
   * this; `writeDoc` takes the current value. */
  frontmatter: string;
  /** Text between the frontmatter and the first block. */
  preamble: string;
  /** Block segments by id, with their order in the original file. */
  segments: Map<string, Segment & { index: number }>;
  eol: '\n' | '\r\n';
}

const withEol = (text: string, eol: string) => (eol === '\n' ? text : text.replace(/\n/g, eol));

/** The source a raw block shows: the segment body, without its final newline. */
export const sourceOf = (segment: Segment) =>
  segment.body.replace(/\r?\n$/, '').replace(/\r\n/g, '\n');

/** Split a file into the editor document and the base `writeDoc` rebuilds it from. */
export function openDoc(text: string): OpenedDoc {
  const source = splitDoc(text);
  const eol = detectEol(text) as '\n' | '\r\n';
  const segments = new Map<string, Segment & { index: number }>();
  const content: PMNode[] = [];
  let frontmatter = '';
  let preamble = '';
  let index = 0;
  for (const segment of source.segments) {
    if (segment.kind === 'frontmatter') {
      frontmatter = segment.body;
      continue;
    }
    if (segment.kind === 'preamble') {
      preamble = segment.tail;
      continue;
    }
    segments.set(segment.id, { ...segment, index: index++ });
    const text = sourceOf(segment);
    const rich =
      segment.kind === 'block' && segment.node ? editableBlock(segment.node, text) : null;
    const block: PMNode = rich ?? {
      type: 'mystBlock',
      attrs: {
        source: text,
        kind: segment.kind === 'break' ? 'break' : (segment.node?.type ?? ''),
      },
    };
    content.push({ ...block, attrs: { ...block.attrs, seg: segment.id } });
  }
  return { doc: { type: 'doc', content }, base: { frontmatter, preamble, segments, eol } };
}

/** Deep equality of two JSON values, key order ignored. */
export function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    const bb = b as unknown[];
    return a.length === bb.length && a.every((v, i) => sameJson(v, bb[i]));
  }
  const ka = Object.keys(a).filter((k) => (a as Record<string, unknown>)[k] !== undefined);
  const kb = Object.keys(b).filter((k) => (b as Record<string, unknown>)[k] !== undefined);
  return (
    ka.length === kb.length &&
    ka.every((k) => sameJson((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
  );
}

interface Piece {
  body: string;
  tail: string;
  /** The segment's position in the original file; `-1` for new text. */
  index: number;
  /** Verbatim from the original file. */
  original: boolean;
}

/**
 * The file for `doc`. `baseline` maps a segment id to its block as the editor first
 * held it — the editor normalises attributes on load, so the comparison is against
 * what it produced, not what `openDoc` fed it.
 */
export function writeDoc(
  doc: PMNode,
  base: DocBase,
  baseline: Map<string, PMNode>,
  frontmatter = base.frontmatter,
): string {
  const { eol } = base;
  const blocks = doc.content ?? [];
  const tagOf = (block: PMNode) => (typeof block.attrs?.seg === 'string' ? block.attrs.seg : '');
  const untag = (block: PMNode | undefined) => {
    if (!block) return block;
    const attrs = { ...(block.attrs ?? {}) };
    delete attrs.seg;
    return { ...block, attrs };
  };
  // Segments no block holds unchanged under its tag. An untagged block identical to
  // one of them is that block — ProseMirror split it off its tag (Enter at the start
  // of a paragraph leaves the tag on the new, empty half) — and gets its bytes back.
  const intact = new Set(
    blocks
      .filter(
        (b) => base.segments.has(tagOf(b)) && sameJson(untag(b), untag(baseline.get(tagOf(b)))),
      )
      .map(tagOf),
  );
  const orphans = [...base.segments.keys()].filter((id) => !intact.has(id));

  const used = new Set<string>();
  const pieces: Piece[] = [];
  for (const block of blocks) {
    let id = tagOf(block);
    if (!base.segments.has(id)) {
      id =
        orphans.find((o) => !used.has(o) && sameJson(untag(block), untag(baseline.get(o)))) ?? '';
    }
    const segment = id && !used.has(id) ? base.segments.get(id) : undefined;
    if (segment && sameJson(untag(block), untag(baseline.get(id)))) {
      used.add(id);
      pieces.push({ body: segment.body, tail: segment.tail, index: segment.index, original: true });
      continue;
    }
    const printed = withEol(printBlock(block), eol);
    // A block emptied out (or the editor's trailing empty paragraph) has no MyST: it
    // is a deletion, so its segment's definitions are kept below.
    if (!printed.trim()) continue;
    if (segment) {
      used.add(id);
      // Keep the body's own ending: whole lines, except a file's unterminated last.
      const ends = /\r?\n$/.test(segment.body) || segment.tail !== '';
      pieces.push({
        body: ends ? printed + eol : printed,
        tail: segment.tail,
        index: segment.index,
        original: false,
      });
    } else {
      pieces.push({ body: printed + eol, tail: '', index: -1, original: false });
    }
  }

  // Deleted blocks: keep definitions that rode in their tails, near where they were.
  for (const segment of base.segments.values()) {
    if (used.has(segment.id) || !/\S/.test(segment.tail)) continue;
    const tail = segment.tail.replace(/^(?:[ \t]*\r?\n)+/, '');
    let at = 0;
    for (let i = 0; i < pieces.length; i++)
      if (pieces[i].index >= 0 && pieces[i].index < segment.index) at = i + 1;
    pieces.splice(at, 0, { body: '', tail, index: -1, original: false });
  }

  // A tail of blank lines is spacing between a block and the one that followed it.
  // Where that neighbour is no longer next (a move, a deletion), the spacing stays
  // behind — the separators below supply what the new neighbours need — so a block
  // dragged to the end does not leave the file ending in a blank line.
  const lastIndex = base.segments.size - 1;
  pieces.forEach((piece, i) => {
    const next = pieces[i + 1];
    const keepsNeighbour = next
      ? piece.index >= 0 && next.index === piece.index + 1
      : piece.index === lastIndex;
    if (!keepsNeighbour && !/\S/.test(piece.tail)) piece.tail = '';
  });

  let out = frontmatter + base.preamble;
  // A frontmatter added to a file that had none needs a line before the body.
  if (frontmatter && !base.frontmatter && !base.preamble && pieces.length) out += eol;
  pieces.forEach((piece, i) => {
    const prev = pieces[i - 1];
    const inPlace = prev && prev.original && piece.original && prev.index === piece.index - 1;
    if (prev && !inPlace) {
      // A blank line between this and whatever precedes it.
      if (!out.endsWith(eol)) out += eol;
      if (!out.endsWith(eol + eol)) out += eol;
    }
    out += piece.body + piece.tail;
  });
  return out;
}
