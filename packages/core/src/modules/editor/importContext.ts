/**
 * Completion inside a Python `import` statement.
 *
 * The other sources all complete an *identifier* — they match a word before the cursor
 * and rank symbols by prefix. That is the wrong shape here twice over. `from ` has no
 * word to match at all, so every existing source bails on the empty prefix; and
 * `from vllm import ` wants the members of one named module, not a global prefix scan.
 * Both are the cases the user actually reaches for Tab in.
 *
 * The two backend queries this drives (`/editor/complete/modules`, `.../members`) read
 * the same `code_symbols` index the identifier sources use — the corpus is already
 * there, only the question is new. See docs/modules/editor.mdx.
 */
import type {
  Completion,
  CompletionContext,
  CompletionResult,
  CompletionSource,
} from '@codemirror/autocomplete';
import type { EditorState } from '@codemirror/state';

import { apiGet } from '../../api';

/** Where the cursor sits inside an import statement, if it does. */
export type ImportContext = { from: number; prefix: string; justOpened: boolean } & (
  | { kind: 'module' }
  | { kind: 'member'; module: string }
);

/**
 * Classify the cursor's position within an import statement, or null. Pure — the
 * regexes only ever see the text from the start of the line to the cursor, so a
 * trailing `import x` later on the line can't confuse it.
 *
 * `from vll|`            → module, prefix `vll`
 * `import |`             → module, prefix ``, justOpened
 * `from vllm import L|`  → member of `vllm`, prefix `L`
 * `from vllm import |`   → member of `vllm`, prefix ``, justOpened
 * `from vllm import A, |`→ member of `vllm`, prefix ``, justOpened
 *
 * **`justOpened`** means the cursor sits directly after the keyword (or the comma)
 * and exactly one space — the moment a person has finished saying *which* import
 * they want and is waiting to be told the options. It is what lets the popup open
 * on its own there without opening on every space in the statement: after two
 * spaces, or a trailing space following a half-typed name, the user is not asking.
 */
export function importContextAt(state: EditorState, pos: number): ImportContext | null {
  const line = state.doc.lineAt(pos);
  const head = state.sliceDoc(line.from, pos);

  // A `from X import ...` list — check first, since it also matches the `from` shape.
  const member = /^\s*from\s+([\w.]+)\s+import\s+(?:\(\s*)?(?:[\w\s,]*?,\s*)?(\w*)$/.exec(head);
  if (member) {
    return {
      kind: 'member',
      module: member[1],
      from: pos - member[2].length,
      prefix: member[2],
      justOpened: /(?:\bimport |[,(] ?)$/.test(head),
    };
  }

  const module = /^\s*(?:from|import)\s+([\w.]*)$/.exec(head);
  if (module) {
    return {
      kind: 'module',
      from: pos - module[1].length,
      prefix: module[1],
      justOpened: /\b(?:from|import) $/.test(head),
    };
  }

  return null;
}

interface ModuleRow {
  module: string;
  freq: number;
}
interface MemberRow {
  symbol: string;
  kind: string;
  detail: string;
  doc?: string;
}

const KIND_TO_TYPE: Record<string, string> = {
  function: 'function',
  class: 'class',
  variable: 'variable',
  module: 'namespace',
  keyword: 'keyword',
};

/** Never throws — a failed lookup just means no completions. */
async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    return await apiGet<T>(url);
  } catch {
    return fallback;
  }
}

/**
 * Module and member completion inside an import statement. `getLang` reports the
 * buffer's LSP language id; only Python is indexed, so anything else yields nothing.
 */
export function importCompletionSource(getLang: () => string | null): CompletionSource {
  return async (context: CompletionContext): Promise<CompletionResult | null> => {
    if (getLang() !== 'python') return null;
    const ctx = importContextAt(context.state, context.pos);
    if (!ctx) return null;
    // An empty prefix is the point here (`from vllm import <Tab>`). Answer it when
    // the user asked *or* when they have just typed the space after the keyword,
    // which is the same moment VS Code opens its suggest widget and the one the
    // feature exists for. Any other empty prefix in the statement stays quiet —
    // opening on every space is what this guard originally prevented.
    if (!ctx.prefix && !context.explicit && !ctx.justOpened) return null;

    let options: Completion[];
    if (ctx.kind === 'module') {
      const q = `lang=python&prefix=${encodeURIComponent(ctx.prefix)}&limit=40`;
      const res = await fetchJson<{ items: ModuleRow[] }>(`/editor/complete/modules?${q}`, {
        items: [],
      });
      options = res.items.map((r) => ({
        label: r.module,
        type: 'namespace',
        // `apply` is left alone: the label *is* the text, and the module path may
        // contain dots the default word-based apply handles fine given our `from`.
      }));
    } else {
      const q =
        `lang=python&module=${encodeURIComponent(ctx.module)}` +
        `&prefix=${encodeURIComponent(ctx.prefix)}&limit=50`;
      const res = await fetchJson<{ items: MemberRow[] }>(`/editor/complete/members?${q}`, {
        items: [],
      });
      options = res.items.map((r) => ({
        label: r.symbol,
        type: KIND_TO_TYPE[r.kind] ?? 'text',
        detail: r.detail || undefined,
        info: r.doc
          ? () => Object.assign(document.createElement('div'), { textContent: r.doc })
          : undefined,
        // Deliberately no `apply`: we are *inside* the import statement, so routing
        // through `applyImport` would insert a second `from vllm import LLM` line
        // above the one being typed. The bare label is the whole edit.
      }));
    }

    if (!options.length) return null;
    return { from: ctx.from, options, validFor: /^[\w.]*$/ };
  };
}
