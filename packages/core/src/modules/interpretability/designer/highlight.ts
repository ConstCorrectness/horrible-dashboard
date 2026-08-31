/**
 * Static Python tokenization for the generated-module pane.
 *
 * The pane is deliberately not a CodeMirror view in read mode (see CodePane's header
 * comment), so it inherits no highlighting. It does two things per *line* — light the
 * lines a hovered node generated, and render a gutter number — so highlighting has to
 * arrive as spans **inside** the existing per-line DOM rather than as editor
 * decorations. That is what this does: parse once with the same lezer grammar
 * CodeMirror would use, then hand back one token array per line.
 *
 * The correctness argument is reassembly — joining every token's text back together
 * must reproduce the source exactly, and the line count must not move, because the
 * `# horrible:node=` markers are keyed by line number. Both are pinned by tests.
 */
import { pythonLanguage } from '@codemirror/lang-python';
import { classHighlighter, highlightTree } from '@lezer/highlight';

/** One run of source text and the highlight classes it carries ('' = unstyled). */
export interface Token {
  text: string;
  cls: string;
}

/**
 * Above this many characters we don't parse. A generated module is a few hundred
 * lines; anything near this is pathological, and a blocking parse in a render path is
 * a worse failure than plain text.
 */
const MAX_CHARS = 200_000;

/** Split a run on newlines, pushing each piece into `lines` and starting new lines as
 * it goes. A styled range genuinely spans lines (a docstring, a bracketed expression),
 * so this — not the caller — is what keeps one array per source line. */
function pushRun(lines: Token[][], text: string, cls: string): void {
  const parts = text.split('\n');
  for (let i = 0; i < parts.length; i++) {
    if (i > 0) lines.push([]);
    if (parts[i]) lines[lines.length - 1].push({ text: parts[i], cls });
  }
}

/**
 * Tokenize Python source into one array of tokens per line. Lines are always
 * `source.split('\n').length` long; an empty line is an empty array.
 */
export function tokenizeLines(source: string): Token[][] {
  const plain = (): Token[][] =>
    source.split('\n').map((line) => (line ? [{ text: line, cls: '' }] : []));
  if (!source) return [[]];
  if (source.length > MAX_CHARS) return plain();

  let tree;
  try {
    tree = pythonLanguage.parser.parse(source);
  } catch {
    // A grammar that throws must not take the pane down with it.
    return plain();
  }

  const lines: Token[][] = [[]];
  let pos = 0;
  highlightTree(tree, classHighlighter, (from, to, classes) => {
    // highlightTree only reports *styled* ranges; the gaps between them are real
    // source (whitespace, punctuation the grammar gives no tag) and must be emitted
    // too, or the text silently loses characters.
    if (from > pos) pushRun(lines, source.slice(pos, from), '');
    pushRun(lines, source.slice(from, to), classes);
    pos = to;
  });
  if (pos < source.length) pushRun(lines, source.slice(pos), '');
  return lines;
}
