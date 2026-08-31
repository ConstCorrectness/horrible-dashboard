/**
 * The buffer completion stack, independent of whether a language server exists.
 *
 * This used to live inside `lspExtension`, which meant `autocompletion()` was only
 * ever configured for a `workspace-file:` buffer in one of five languages. Every other
 * buffer — a note, an unsaved scratch buffer, a `.json`, a Drive file — had **no
 * completion source at all**, even though the `code_symbols` index that serves them
 * was already built. Extracting it makes the language server one optional source among
 * several rather than the gate on all of them.
 *
 * > One `autocompletion()` may be live at a time. CodeMirror merges configuration from
 * > every instance, but `override` is a *replacing* field — a second instance's
 * > override silently wins and the first one's sources vanish with no error. So the
 * > LSP compartment and the standalone compartment are configured from the same
 * > predicate and are never both non-empty.
 */
import {
  acceptCompletion,
  autocompletion,
  completionStatus,
  startCompletion,
  type CompletionResult,
  type CompletionSource,
} from '@codemirror/autocomplete';
import { indentWithTab } from '@codemirror/commands';
import { Prec, type Extension } from '@codemirror/state';
import { keymap, type Command } from '@codemirror/view';

import { importCompletionSource, importContextAt } from './importContext';
import { frameworkImportSource } from './pythonImports';
import { dbSymbolSource } from './symbolCompletion';

/** How the popup opens: as you type (plus Tab/Ctrl-Space), or only on Tab/Ctrl-Space. */
export type CompletionTrigger = 'auto' | 'manual';

export interface CompletionOptions {
  /** The buffer's LSP language id ('python', 'rust', …) or null. */
  languageId: string | null;
  /** The language server's own source, when one is running. Ranked first. */
  lspSource?: CompletionSource;
  /** Installed framework versions, for gating/annotating the curated imports. */
  getPackages?: () => Record<string, string> | undefined;
  /** `editor.indexedSymbols` — the indexed stdlib/package prefix index. */
  indexedSymbols?: boolean;
  /** `editor.frameworkImports` — the curated framework-import completions. */
  frameworkImports?: boolean;
  /** `editor.importCompletions` — module/member completion inside `import` lines. */
  importCompletions?: boolean;
  /** `editor.completionTrigger`. */
  trigger?: CompletionTrigger;
}

/**
 * Merge every source into one, so the offline sources can be **deduped against the
 * server's own results**. basedpyright ships typeshed, so once it's warm it already
 * offers `defaultdict`, `Path`, … as auto-imports — listing the indexed copy beside it
 * showed every stdlib symbol twice. The server wins any label it provides (it is type-
 * and scope-aware); the others only fill in what it didn't offer, which is what makes
 * them useful during the cold start and for third-party packages it can't auto-import.
 */
function mergeSources(primary: CompletionSource | undefined, rest: CompletionSource[]) {
  return async (context: Parameters<CompletionSource>[0]): Promise<CompletionResult | null> => {
    const [lsp, ...others] = await Promise.all([
      primary ? primary(context) : Promise.resolve(null),
      ...rest.map((src) => src(context)),
    ]);
    const extras = others.filter((r): r is CompletionResult => r != null);
    if (!lsp && !extras.length) return null;
    const seen = new Set((lsp?.options ?? []).map((o) => o.label));
    const options = [...(lsp?.options ?? [])];
    for (const result of extras) {
      for (const option of result.options) {
        if (seen.has(option.label)) continue;
        seen.add(option.label);
        options.push(option);
      }
    }
    if (!options.length) return null;
    // An import-context result anchors on a dotted module path rather than a bare
    // word, so its `from` disagrees with the identifier sources' — and it is the one
    // that must win, since it is the only source that matched at all in that position.
    const anchor = extras.find((r) => r.validFor?.toString() === /^[\w.]*$/.toString());
    return {
      from: anchor?.from ?? lsp?.from ?? extras[0].from,
      options,
      validFor: anchor ? /^[\w.]*$/ : /^[A-Za-z_]\w*$/,
    };
  };
}

/** Build the `autocompletion()` extension for a buffer. */
export function buildCompletion(opts: CompletionOptions): Extension {
  const lang = () => opts.languageId;
  const sources: CompletionSource[] = [];
  if (opts.indexedSymbols !== false) sources.push(dbSymbolSource(lang));
  if (opts.languageId === 'python') {
    if (opts.importCompletions !== false) sources.push(importCompletionSource(lang));
    if (opts.frameworkImports !== false) {
      sources.push(frameworkImportSource(opts.getPackages ?? (() => undefined)));
    }
  }
  if (!opts.lspSource && !sources.length) return [];
  return autocompletion({
    override: [mergeSources(opts.lspSource, sources)],
    activateOnTyping: opts.trigger !== 'manual',
  });
}

/**
 * Tab opens the completion popup when the cursor is somewhere completions could
 * plausibly come from.
 *
 * The whole design risk lives here: `startCompletion` returns true whether or not it
 * finds anything, so calling it unconditionally would swallow indentation everywhere.
 * It therefore returns false — falling through to `indentWithTab` — unless all of:
 * the selection is empty; the text before the cursor on this line is not entirely
 * whitespace (that is indentation, unambiguously); and either a word character
 * precedes the cursor or the cursor is inside an import statement (`from <Tab>`,
 * which has no word to match and is exactly why this exists).
 */
export const tabStartCompletion: Command = (view) => {
  if (completionStatus(view.state) != null) return false; // a popup is already open
  const sel = view.state.selection.main;
  if (!sel.empty) return false;
  const line = view.state.doc.lineAt(sel.head);
  const before = view.state.sliceDoc(line.from, sel.head);
  if (!before.trim()) return false;
  const completable = /[\w.]$/.test(before) || importContextAt(view.state, sel.head) != null;
  if (!completable) return false;
  return startCompletion(view);
};

/**
 * The buffer's Tab/completion keymap. Tab accepts an open popup (VS Code style), then
 * tries to open one, then indents — `basicSetup` deliberately leaves Tab unbound for
 * accessibility, which is why it otherwise escapes to browser focus traversal.
 * Ghost-text autosuggest binds Tab at `Prec.highest`, so an inline suggestion is still
 * accepted ahead of all of this.
 */
export const completionKeymap: Extension = Prec.high(
  keymap.of([
    { key: 'Tab', run: acceptCompletion },
    { key: 'Tab', run: tabStartCompletion },
    indentWithTab,
  ]),
);
