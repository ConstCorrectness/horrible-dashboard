/**
 * Editor inline autosuggest: an opt-in CodeMirror extension that, after a typing
 * pause, asks the backend for a short fill-in completion and renders it as ghost
 * text at the cursor. Tab accepts, Esc dismisses. Off by default — BufferView
 * includes it only when the `editor.autosuggest` setting is on and no agent edit
 * proposal is being reviewed. See docs/modules/editor.md.
 */
import { Prec, StateEffect, StateField } from '@codemirror/state';
import {
  Decoration,
  EditorView,
  keymap,
  ViewPlugin,
  WidgetType,
  type ViewUpdate,
} from '@codemirror/view';

interface Suggestion {
  /** Cursor offset the ghost text completes at. */
  from: number;
  text: string;
}

const setSuggestion = StateEffect.define<Suggestion | null>();

// The current ghost suggestion, or null. Any doc edit or cursor move clears it,
// unless the transaction is the effect installing a fresh one.
const suggestionField = StateField.define<Suggestion | null>({
  create() {
    return null;
  },
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setSuggestion)) return e.value;
    if (tr.docChanged || tr.selection) return null;
    return value;
  },
});

class GhostWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(other: GhostWidget): boolean {
    return other.text === this.text;
  }
  toDOM(): HTMLElement {
    const span = document.createElement('span');
    span.className = 'cm-ghost-text';
    span.textContent = this.text;
    return span;
  }
}

const ghostDecoration = EditorView.decorations.compute([suggestionField], (state) => {
  const s = state.field(suggestionField);
  if (!s) return Decoration.none;
  return Decoration.set([
    Decoration.widget({ widget: new GhostWidget(s.text), side: 1 }).range(s.from),
  ]);
});

export interface AutosuggestOptions {
  /** Request a completion for the text before/after the cursor. Aborts on `signal`. */
  fetch: (prefix: string, suffix: string, signal: AbortSignal) => Promise<string>;
  /** Optional streaming completion: called concurrently or progressively after initial fetch. */
  fetchStream?: (
    prefix: string,
    suffix: string,
    signal: AbortSignal,
    onChunk: (chunk: string) => void,
  ) => Promise<string>;
  /** Idle delay before requesting, in ms. */
  delayMs?: number;
}

/** Build the inline-autosuggest extension. */
export function autosuggest(opts: AutosuggestOptions) {
  const delay = opts.delayMs ?? 350;

  const requester = ViewPlugin.fromClass(
    class {
      timer: number | undefined;
      controller: AbortController | null = null;

      update(u: ViewUpdate): void {
        // Re-request only on user typing; the field already cleared the stale
        // suggestion. A suggestion-only dispatch has no doc change, so no loop.
        if (u.docChanged) this.schedule(u.view);
      }

      schedule(view: EditorView): void {
        this.cancel();
        this.timer = window.setTimeout(() => void this.run(view), delay);
      }

      async run(view: EditorView): Promise<void> {
        const sel = view.state.selection.main;
        if (!sel.empty) return; // only complete at a bare cursor
        const head = sel.head;
        const prefix = view.state.doc.sliceString(0, head);
        const suffix = view.state.doc.sliceString(head);
        if (!prefix.trim()) return;
        const controller = new AbortController();
        this.controller = controller;

        // Tier 1: Instant local prefix/symbol match
        let text = '';
        try {
          text = (await opts.fetch(prefix, suffix, controller.signal)).replace(/\s+$/, '');
        } catch {
          // aborted or backend error
        }

        // Apply only if nothing moved under us while the request was in flight.
        const cur = view.state.selection.main;
        const stillValid = cur.head === head && view.state.doc.sliceString(0, head) === prefix;
        if (!stillValid) return;

        if (text) {
          view.dispatch({ effects: setSuggestion.of({ from: head, text }) });
        }

        // Tier 2: Streaming model FIM if configured
        if (opts.fetchStream) {
          let streamed = '';
          try {
            await opts.fetchStream(prefix, suffix, controller.signal, (chunk) => {
              if (controller.signal.aborted) return;
              const nowCur = view.state.selection.main;
              if (nowCur.head !== head || view.state.doc.sliceString(0, head) !== prefix) return;
              streamed += chunk;
              const cleaned = streamed.replace(/\s+$/, '');
              if (cleaned) {
                view.dispatch({ effects: setSuggestion.of({ from: head, text: cleaned }) });
              }
            });
          } catch {
            // Streaming aborted or unavailable — keep instant suggestion
          }
        }
      }

      cancel(): void {
        if (this.timer !== undefined) window.clearTimeout(this.timer);
        this.timer = undefined;
        this.controller?.abort();
        this.controller = null;
      }

      destroy(): void {
        this.cancel();
      }
    },
  );

  const keys = Prec.highest(
    keymap.of([
      {
        key: 'Tab',
        run(view) {
          const s = view.state.field(suggestionField, false);
          if (!s) return false; // no suggestion → let Tab indent as usual
          view.dispatch({
            changes: { from: s.from, insert: s.text },
            selection: { anchor: s.from + s.text.length },
            effects: setSuggestion.of(null),
          });
          return true;
        },
      },
      {
        key: 'Escape',
        run(view) {
          if (!view.state.field(suggestionField, false)) return false;
          view.dispatch({ effects: setSuggestion.of(null) });
          return true;
        },
      },
    ]),
  );

  return [suggestionField, ghostDecoration, requester, keys];
}
