/**
 * CodeMirror autocompletion source for the Python REPL pane.
 *
 * Drives autocompletion from the live `ReplSession` kernel via Jedi, anchored
 * with `anchorMatches` so object attribute lookups (`df.`, `sys.`, `x.`) and
 * live workspace symbols populate the completion popup as the user types.
 */
import type {
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from '@codemirror/autocomplete';

import { anchorMatches } from '../editor/kernelCompletion';
import type { ReplSession } from './client';

export function replCompletionSource(session: ReplSession): CompletionSource {
  return async (context: CompletionContext): Promise<CompletionResult | null> => {
    const word = context.matchBefore(/[A-Za-z_]\w*/);
    const afterDot = context.state.sliceDoc(Math.max(0, context.pos - 1), context.pos) === '.';
    // Trigger on explicit (Tab/Ctrl-Space), after dot (`obj.`), or on 1+ typed characters
    if (!context.explicit && !afterDot && (!word || word.to - word.from < 1)) return null;
    const doc = context.state.doc.toString();
    try {
      const reply = await session.complete(doc, context.pos);
      if (context.aborted || reply.source !== 'kernel' || !reply.matches.length) return null;
      const wordFrom = word ? word.from : context.pos;
      const matches = reply.matches.map((m) => ({
        text: m.text,
        type: m.type,
        signature: m.signature ?? '',
      }));
      const options = anchorMatches(doc, context.pos, wordFrom, {
        source: 'kernel',
        matches,
        cursor_start: reply.cursor_start,
        cursor_end: reply.cursor_end,
      }, 0);
      if (!options.length) return null;
      return { from: wordFrom, options, validFor: /^[A-Za-z_]\w*$/ };
    } catch {
      return null;
    }
  };
}
