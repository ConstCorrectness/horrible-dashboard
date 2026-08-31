/**
 * Curated "framework imports" completion source for Python buffers. basedpyright
 * doesn't index installed third-party libraries for auto-import — only stdlib
 * (typeshed) and workspace-local symbols (verified) — so common framework
 * symbols/aliases aren't offered in a file until you've already imported them. This
 * static source fills that gap: typing a known symbol/alias suggests it, and
 * accepting inserts the symbol **and** prepends the matching `import` line (deduped).
 * It's independent of the language server (works even when none is running) and ranks
 * below the LSP's in-scope results. See docs/modules/editor.md.
 */
import { type ChangeSpec, type EditorState } from '@codemirror/state';
import { type EditorView } from '@codemirror/view';
import {
  pickedCompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
  type CompletionSource,
} from '@codemirror/autocomplete';

/** One curated import: the typed/inserted token (`label`), the import line it needs,
 * the source module shown in the popup (`detail`), and the pip **dist** name (`pkg`)
 * used to gate on / annotate with the installed version. */
export interface ImportEntry {
  label: string;
  importLine: string;
  detail: string;
  pkg: string;
}

/** Import (module root) → pip distribution name, where they differ. */
const DIST_FOR: Record<string, string> = {
  'matplotlib.pyplot': 'matplotlib',
  'torch.nn.functional': 'torch',
  google: 'google-genai',
  PIL: 'pillow',
};

/** The pip dist name for a module path (the exceptions above, else the top segment). */
function distFor(module: string): string {
  return DIST_FOR[module] ?? module.split('.')[0];
}

/** `import <module>` (optionally `as <alias>`); the alias, when given, is the token. */
function mod(module: string, alias?: string): ImportEntry {
  const base = { importLine: '', detail: module, pkg: distFor(module) };
  return alias
    ? { ...base, label: alias, importLine: `import ${module} as ${alias}` }
    : { ...base, label: module, importLine: `import ${module}` };
}

/** `from <module> import <name>`; `name` is the token. */
function sym(module: string, name: string): ImportEntry {
  return {
    label: name,
    importLine: `from ${module} import ${name}`,
    detail: module,
    pkg: distFor(module),
  };
}

/** The curated set — common frameworks the language server can't auto-import. Order
 * is irrelevant (options are filtered by prefix at request time). */
export const FRAMEWORK_IMPORTS: ImportEntry[] = [
  // Scientific stack.
  mod('numpy', 'np'),
  mod('numpy'),
  sym('numpy', 'ndarray'),
  sym('numpy', 'array'),
  mod('pandas', 'pd'),
  mod('pandas'),
  sym('pandas', 'DataFrame'),
  sym('pandas', 'Series'),
  sym('pandas', 'read_csv'),
  mod('matplotlib.pyplot', 'plt'),
  mod('matplotlib'),
  // PyTorch.
  mod('torch'),
  sym('torch', 'Tensor'),
  sym('torch', 'tensor'),
  sym('torch', 'nn'),
  {
    label: 'F',
    importLine: 'import torch.nn.functional as F',
    detail: 'torch.nn.functional',
    pkg: 'torch',
  },
  // Hugging Face + fine-tuning.
  mod('transformers'),
  sym('transformers', 'AutoModelForCausalLM'),
  sym('transformers', 'AutoModel'),
  sym('transformers', 'AutoTokenizer'),
  sym('transformers', 'pipeline'),
  sym('transformers', 'Trainer'),
  sym('transformers', 'TrainingArguments'),
  mod('datasets'),
  sym('datasets', 'load_dataset'),
  sym('datasets', 'Dataset'),
  sym('trl', 'SFTTrainer'),
  sym('trl', 'SFTConfig'),
  sym('trl', 'GRPOTrainer'),
  mod('bitsandbytes', 'bnb'),
  // Serving / RL / API clients / imaging.
  mod('vllm'),
  sym('vllm', 'LLM'),
  sym('vllm', 'SamplingParams'),
  sym('vllm', 'PoolingParams'),
  sym('vllm', 'LLMEngine'),
  sym('vllm', 'EngineArgs'),
  sym('vllm', 'AsyncLLMEngine'),
  sym('vllm', 'AsyncEngineArgs'),
  sym('vllm', 'TextPrompt'),
  sym('vllm', 'TokensPrompt'),
  sym('vllm', 'RequestOutput'),
  sym('vllm', 'CompletionOutput'),
  sym('vllm.lora.request', 'LoRARequest'),
  sym('vllm.sampling_params', 'GuidedDecodingParams'),
  mod('openai'),
  sym('openai', 'OpenAI'),
  mod('anthropic'),
  sym('anthropic', 'Anthropic'),
  sym('google', 'genai'),
  mod('gymnasium', 'gym'),
  mod('gymnasium'),
  sym('PIL', 'Image'),
];

/** The distinct pip dist names the framework registry tracks — the packages the
 * indexed-packages settings panel lists and reports versions for. */
export const FRAMEWORK_PACKAGE_NAMES: string[] = [
  ...new Set(FRAMEWORK_IMPORTS.map((e) => e.pkg)),
].sort();

/** Import lines already present near the top of the file. Imports live at the top, so
 * the scan is capped — this stays O(1) per lookup and cheap on large files. */
function existingImportLines(state: EditorState): Set<string> {
  const set = new Set<string>();
  const max = Math.min(state.doc.lines, 400);
  for (let i = 1; i <= max; i++) {
    const t = state.doc.line(i).text.trim();
    if (t.startsWith('import ') || t.startsWith('from ')) set.add(t);
  }
  return set;
}

/** The 1-based line at whose start a new import should go: after any shebang, module
 * docstring, and the leading comment/blank/import block. Pure — unit-testable. */
export function importTargetLine(state: EditorState): number {
  const doc = state.doc;
  const n = doc.lines;
  const text = (k: number): string => doc.line(k).text;
  let i = 1;
  if (i <= n && text(i).startsWith('#!')) i++;
  while (i <= n && (text(i).trim() === '' || text(i).trim().startsWith('#'))) i++;
  // A module docstring (triple- or single-quoted) at the top.
  if (i <= n) {
    const t = text(i).trimStart();
    const triple = t.startsWith('"""') ? '"""' : t.startsWith("'''") ? "'''" : '';
    if (triple) {
      if (t.slice(3).includes(triple)) {
        i++; // opens and closes on one line
      } else {
        i++;
        while (i <= n && !text(i).includes(triple)) i++;
        if (i <= n) i++; // consume the closing line
      }
    } else if (/^(['"]).*\1\s*$/.test(t)) {
      i++; // single-line quoted docstring
    }
  }
  // The initial import block (imports plus interspersed comments/blanks).
  let lastImport = 0;
  let j = i;
  while (j <= n) {
    const t = text(j).trim();
    if (t.startsWith('import ') || t.startsWith('from ')) {
      lastImport = j;
      j++;
    } else if (t === '' || t.startsWith('#')) {
      j++;
    } else break;
  }
  return lastImport > 0 ? lastImport + 1 : i;
}

/** A change that inserts `importLine` at the top of the file, or `null` if an
 * equivalent line already exists. `before` is the offset of the symbol being
 * completed — the import is never placed after it. Pure — unit-testable. */
export function ensureImportChange(
  state: EditorState,
  importLine: string,
  before: number,
): { from: number; insert: string } | null {
  if (existingImportLines(state).has(importLine.trim())) return null;
  const doc = state.doc;
  const k = importTargetLine(state);
  let pos = k > doc.lines ? doc.length : doc.line(k).from;
  if (pos > before) pos = 0; // never insert the import below the symbol we're completing
  if (pos === doc.length && doc.length > 0 && !doc.sliceString(doc.length - 1).endsWith('\n')) {
    return { from: pos, insert: `\n${importLine}` };
  }
  return { from: pos, insert: `${importLine}\n` };
}

/** Insert the entry's token at `[from,to]` and ensure its import at the top, in one
 * transaction; leave the cursor after the token and close the popup. */
export function applyImport(view: EditorView, entry: ImportEntry, from: number, to: number): void {
  const importChange = ensureImportChange(view.state, entry.importLine, from);
  const changes: ChangeSpec[] = [{ from, to, insert: entry.label }];
  let cursor = from + entry.label.length;
  if (importChange) {
    changes.push(importChange);
    // The import lands at/above `from`, so the token shifts right by its length.
    cursor += importChange.insert.length;
  }
  view.dispatch({
    changes,
    selection: { anchor: cursor },
    annotations: pickedCompletion.of({ label: entry.label }),
  });
}

/** Build the completion source. `getPackages` returns the interpreter's installed
 * framework versions (pip dist name → version) when known: entries are then **gated**
 * to installed packages and their **version** is shown in the detail. When it returns
 * undefined/empty (env not resolved yet, or non-Python), all entries are offered
 * ungated — the source still works, it just can't gate/annotate.
 *
 * Offers curated framework symbols/aliases whose token prefix-matches the typed word
 * and aren't already imported; merges into the LSP popup (same `from`) and ranks below
 * real results via a negative boost. */
export function frameworkImportSource(
  getPackages?: () => Record<string, string> | undefined,
): CompletionSource {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/[A-Za-z_]\w*/);
    if (!word || (word.from === word.to && !context.explicit)) return null;
    const typed = context.state.sliceDoc(word.from, word.to);
    if (!typed) return null;
    const lower = typed.toLowerCase();
    const existing = existingImportLines(context.state);
    const packages = getPackages?.();
    const gate = packages && Object.keys(packages).length ? packages : null;
    const options: Completion[] = [];
    for (const entry of FRAMEWORK_IMPORTS) {
      if (!entry.label.toLowerCase().startsWith(lower)) continue;
      if (existing.has(entry.importLine.trim())) continue; // already imported → LSP covers it
      if (gate && !(entry.pkg in gate)) continue; // gate to installed packages
      const version = gate ? gate[entry.pkg] : undefined;
      options.push({
        label: entry.label,
        detail: version ? `${entry.detail} ${version}` : entry.detail,
        type: 'namespace',
        boost: -99, // below the LSP's in-scope completions
        apply: (view, _completion, from, to) => applyImport(view, entry, from, to),
      });
    }
    return options.length ? { from: word.from, options, validFor: /^[A-Za-z_]\w*$/ } : null;
  };
}
