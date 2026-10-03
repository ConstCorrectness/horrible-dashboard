/**
 * The source-preserving writer: a MyST file as an ordered list of **segments** whose
 * concatenation is the file, byte for byte.
 *
 * Every edit Scrive makes — the block editor, an agent's `replaceSection`, a
 * "Turn into" — replaces the *body* of one segment and leaves every other byte of
 * the file alone. So a hand-written file is never reformatted by being opened and
 * saved, and its git diff is exactly the block that changed.
 *
 * How the file is cut, and why it is cut that way:
 *
 * - A segment starts on the first line of each top-level node of the parse, after
 *   flattening the `block` wrappers that `+++` breaks create (and giving each `+++`
 *   marker a segment of its own).
 * - A segment's `body` is that node's own lines (trailing blank lines trimmed). Its
 *   `tail` is everything from there to the next segment's first line.
 * - The tail is not just blank lines. `myst-parser` emits **no node** for a
 *   `[ref]: url` definition and **hoists** a footnote definition to the end of the
 *   tree with the position of the document's last line. Neither can start a segment
 *   reliably, so both ride in the tail of whatever precedes them — and because an
 *   edit replaces only a body, they survive the edit untouched.
 * - Positions are line-granular (markdown-it maps), and top-level blocks are
 *   line-aligned, so cutting on lines never splits a block.
 */
import { parseMyst, splitFrontmatter, type MystNode, type MystRoot } from './parse';

export type SegmentKind = 'frontmatter' | 'block' | 'break' | 'preamble';

export interface Segment {
  /** Stable for the life of a `SourceDoc`; a fresh split assigns fresh ids. */
  id: string;
  kind: SegmentKind;
  /** The node's own source. */
  body: string;
  /** What follows it up to the next segment: blank lines, reference/footnote
   * definitions, anything without a reliable node of its own. */
  tail: string;
  /** 1-based line of `body` in the whole file. */
  line: number;
  /** The top-level node this segment was cut for (absent for frontmatter, a
   * preamble, and `+++` markers). */
  node?: MystNode;
}

export interface SourceDoc {
  segments: Segment[];
  /** The whole-body parse the segments were cut from — what the renderer uses, so
   * cross-block constructs (footnotes, reference links) resolve. */
  tree: MystRoot;
  /** Lines the frontmatter spans: add to a body-relative line for a file line. */
  frontmatterLines: number;
}

/** Split `text` into lines, each keeping its own terminator (`\n` or `\r\n`). */
function splitLines(text: string): string[] {
  const lines = text.match(/[^\n]*\n|[^\n]+$/g);
  return lines ?? [];
}

const BLANK = /^[ \t]*\r?\n?$/;

/** The top-level nodes in source order, `block` wrappers flattened. */
function topLevel(
  tree: MystRoot,
  lines: string[],
): { line: number; node?: MystNode; kind: SegmentKind }[] {
  const out: { line: number; node?: MystNode; kind: SegmentKind }[] = [];
  for (const node of tree.children) {
    const start = node.position?.start.line;
    if (node.type === 'block') {
      // Only a real `+++` line is a marker; the implicit first block has none.
      if (start && /^\+\+\+/.test(lines[start - 1] ?? '')) out.push({ line: start, kind: 'break' });
      for (const child of node.children ?? []) {
        const line = child.position?.start.line;
        if (line) out.push({ line, node: child, kind: 'block' });
      }
    } else if (start) {
      out.push({ line: start, node, kind: 'block' });
    }
  }
  // Source order, one segment per start line, and never a start inside the node
  // before it. A footnote definition is hoisted with the position of the document's
  // last line — which can be inside a final directive, and would cut it in two — so
  // definitions never start a segment: their text rides in the tail before them.
  out.sort((a, b) => a.line - b.line);
  const kept: typeof out = [];
  let end = 0;
  for (const entry of out) {
    if (entry.node?.type === 'footnoteDefinition' || entry.line <= end) continue;
    kept.push(entry);
    end = entry.node?.position?.end.line ?? entry.line;
  }
  return kept;
}

let seq = 0;
const nextId = () => `s${(++seq).toString(36)}`;

export function splitDoc(text: string): SourceDoc {
  const fm = splitFrontmatter(text);
  const lines = splitLines(fm.body);
  const tree = parseMyst(fm.body);
  const starts = topLevel(tree, lines).filter((s) => s.line >= 1 && s.line <= lines.length);

  const segments: Segment[] = [];
  if (fm.raw) {
    segments.push({ id: nextId(), kind: 'frontmatter', body: fm.raw, tail: '', line: 1 });
  }
  // Anything before the first node: leading blank lines after the frontmatter, or a
  // body that is only reference definitions.
  const firstLine = starts.length ? starts[0].line : lines.length + 1;
  if (firstLine > 1) {
    segments.push({
      id: nextId(),
      kind: 'preamble',
      body: '',
      tail: lines.slice(0, firstLine - 1).join(''),
      line: fm.lines + 1,
    });
  }

  starts.forEach((start, i) => {
    const next = i + 1 < starts.length ? starts[i + 1].line : lines.length + 1;
    const span = lines.slice(start.line - 1, next - 1);
    // The node's own lines: through its reported end, never past the next start,
    // then without trailing blank lines (markdown-it's maps sometimes include one).
    const end = start.node?.position?.end.line ?? start.line;
    let bodyLen = Math.max(1, Math.min(span.length, end - start.line + 1));
    while (bodyLen > 1 && BLANK.test(span[bodyLen - 1])) bodyLen--;
    segments.push({
      id: nextId(),
      kind: start.kind,
      body: span.slice(0, bodyLen).join(''),
      tail: span.slice(bodyLen).join(''),
      line: fm.lines + start.line,
      node: start.node,
    });
  });

  return { segments, tree, frontmatterLines: fm.lines };
}

/** The file. For an unedited `SourceDoc` this is the text it was split from. */
export function joinDoc(doc: Pick<SourceDoc, 'segments'>): string {
  let out = '';
  for (const s of doc.segments) out += s.body + s.tail;
  return out;
}

/** The line ending a segment body should end with, judged from the text around it. */
function eolOf(text: string): string {
  return /\r\n/.test(text) ? '\r\n' : '\n';
}

/**
 * Replace one segment's body. Everything else — other segments, this segment's tail
 * — is untouched. `body` is normalised to end with exactly one line ending (in the
 * file's own style) when the old one did, so a block that is edited does not glue
 * itself onto the next one or grow a blank line.
 */
export function replaceBody(doc: SourceDoc, id: string, body: string): SourceDoc {
  const index = doc.segments.findIndex((s) => s.id === id);
  if (index < 0) throw new Error(`no segment ${id}`);
  const old = doc.segments[index];
  const eol = eolOf(old.body + old.tail);
  let next = body.replace(/\r?\n$/, '');
  if (eol === '\r\n') next = next.replace(/\r?\n/g, '\r\n');
  // Keep the old body's own terminator: a body is whole lines except the file's last
  // line. An empty body (a preamble) that gains text gets one, so it does not run
  // into the blank lines that were its tail.
  if (/\r?\n$/.test(old.body) || (old.body === '' && next !== '' && old.tail)) next += eol;
  const segments = doc.segments.slice();
  segments[index] = { ...old, body: next };
  return { ...doc, segments };
}
