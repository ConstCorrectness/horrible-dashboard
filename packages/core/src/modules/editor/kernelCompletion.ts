/**
 * Completion from a notebook's **live kernel** (`POST /api/docs/complete`).
 *
 * Every other source here is static: the language server reads types, the index
 * reads installed packages. Neither can know what a loaded object actually holds —
 * `model.` on an `AutoModelForCausalLM` has submodules named by the checkpoint's
 * config, and `df.` has the columns of a frame that exists only in the kernel. The
 * kernel knows, because it has the object.
 *
 * It is an *extra* source, merged after the language server's by `buildCompletion`:
 * the server's labels win (they carry types and docs), and the kernel fills in the
 * names only runtime knows. When there is no running kernel the route says
 * `unavailable` and this returns nothing — the static sources still answer.
 *
 * ## Normalizing the anchor
 *
 * The kernel reports a replacement range (`cursor_start..cursor_end`) of its own
 * choosing — jedi anchors after the dot, IPython's fallback completer sometimes
 * anchors at the start of the whole dotted expression. The merged completion list
 * has **one** `from`, the identifier start every other source uses, so each match is
 * re-expressed relative to it; a match whose already-typed part disagrees with the
 * document is dropped rather than inserted somewhere wrong.
 */
import type {
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from '@codemirror/autocomplete';

import { apiPost } from '../../api';

interface KernelCompleteResponse {
  source: 'kernel' | 'unavailable';
  matches: { text: string; type: string; signature: string }[];
  cursor_start: number;
  cursor_end: number;
}

const TYPE_TO_CM: Record<string, string> = {
  function: 'function',
  method: 'method',
  class: 'class',
  module: 'namespace',
  instance: 'variable',
  statement: 'variable',
  keyword: 'keyword',
  property: 'property',
  param: 'variable',
  path: 'text',
};

/** Pure, for tests: kernel matches re-anchored onto `wordFrom` (see above). */
export function anchorMatches(
  doc: string,
  pos: number,
  wordFrom: number,
  reply: KernelCompleteResponse,
  cellOffset: number,
): { label: string; type?: string; detail?: string }[] {
  const kernelFrom = cellOffset + reply.cursor_start;
  const out: { label: string; type?: string; detail?: string }[] = [];
  const seen = new Set<string>();
  for (const m of reply.matches) {
    let label = m.text;
    if (kernelFrom < wordFrom) {
      // The kernel replaced more than the word (`model.lm` -> `model.lm_head`):
      // the part before the word must be what is actually in the document.
      const typed = doc.slice(kernelFrom, wordFrom);
      if (!label.startsWith(typed)) continue;
      label = label.slice(typed.length);
    } else if (kernelFrom > wordFrom) {
      label = doc.slice(wordFrom, kernelFrom) + label;
    }
    // Private names only when asked for; `_` is what someone types to see them.
    if (!label || seen.has(label)) continue;
    if (label.startsWith('_') && !doc.slice(wordFrom, pos).startsWith('_')) continue;
    seen.add(label);
    out.push({
      label,
      type: TYPE_TO_CM[m.type] ?? undefined,
      detail: m.signature || (m.type ? m.type : undefined),
    });
  }
  return out;
}

/**
 * @param notebookPath The `.ipynb` path the notebook manager keys its kernel by.
 * @param cellText     The cell's text *and* its offset in the CodeMirror doc (a cell
 *   editor's doc is the cell, so the offset is 0 there).
 */
export function kernelCompletionSource(notebookPath: string): CompletionSource {
  return async (context: CompletionContext): Promise<CompletionResult | null> => {
    const word = context.matchBefore(/[A-Za-z_]\w*/);
    const afterDot = context.state.sliceDoc(Math.max(0, context.pos - 1), context.pos) === '.';
    // The kernel is a round trip, so it is only asked where it can add something:
    // after a dot (members of a live object), on an explicit request, or once a
    // word is two characters long.
    if (!context.explicit && !afterDot && (!word || word.to - word.from < 2)) return null;
    const doc = context.state.doc.toString();
    let reply: KernelCompleteResponse;
    try {
      reply = await apiPost<KernelCompleteResponse>('/docs/complete', {
        notebook_path: notebookPath,
        code: doc,
        cursor_pos: context.pos,
      });
    } catch {
      return null;
    }
    if (context.aborted || reply.source !== 'kernel' || !reply.matches.length) return null;
    const wordFrom = word ? word.from : context.pos;
    const options = anchorMatches(doc, context.pos, wordFrom, reply, 0);
    if (!options.length) return null;
    return { from: wordFrom, options, validFor: /^[A-Za-z_]\w*$/ };
  };
}
