/**
 * The one authority on "what language is this buffer?".
 *
 * This used to be two maps that disagreed: a regex over the tab title chose the
 * *highlighting* (JS/TS, Python, else Markdown — so a `.rs` or `.cpp` file rendered as
 * Markdown and nobody was told), while a separate extension switch chose the *LSP
 * language id*. One resolver now answers both, and filename matching comes from
 * `@codemirror/language-data` so the grammar set is CodeMirror's, not a table we
 * maintain. Grammars are dynamic imports — nothing loads until a buffer needs it.
 *
 * Precedence: an explicit pin → an explicit hint → the filename → a content sniff →
 * Markdown. The sniff is consulted **only** for a buffer whose name carries no
 * extension (an untitled scratch buffer, a note); once a file has a name, the name is
 * the answer, because a `.py` file that momentarily contains prose is still Python.
 */
import { LanguageDescription } from '@codemirror/language';
import { languages } from '@codemirror/language-data';

/** The resolved language of a buffer. `name` is a language-data language name
 * ('Python', 'C++', 'Markdown'); `desc` is null only when even Markdown is missing. */
export interface ResolvedLanguage {
  name: string;
  desc: LanguageDescription | null;
  /** The LSP languageId the backend has a server for, or null (no LSP). */
  lspId: string | null;
}

/**
 * Language name → LSP languageId. Kept in sync with `LSP_SERVERS` in
 * `backend/modules/lsp/manager.py` — a name here with no server there just means the
 * client asks for one and gets nothing, which is quiet but harmless; a *server* there
 * with no entry here is unreachable.
 */
const LSP_IDS: Record<string, string> = {
  Python: 'python',
  TypeScript: 'typescript',
  TSX: 'typescriptreact',
  JavaScript: 'javascript',
  JSX: 'javascriptreact',
  Rust: 'rust',
  'C++': 'cpp',
  C: 'cpp',
};

/** Extensions whose language-data match we deliberately narrow: `.tsx`/`.jsx` match
 * the same JavaScript description, and only the filename tells them apart. */
const EXT_NAMES: Record<string, string> = {
  tsx: 'TSX',
  jsx: 'JSX',
  ts: 'TypeScript',
  mts: 'TypeScript',
  cts: 'TypeScript',
};

/** A language-data description by name, or null. */
export function descriptionFor(name: string): LanguageDescription | null {
  return (
    languages.find((l) => l.name === name) ??
    LanguageDescription.matchLanguageName(languages, name, true) ??
    null
  );
}

/** The trailing extension of a filename, lowercased, or '' when it has none. */
function extensionOf(title: string): string {
  const base = title.split(/[/\\]/).pop() ?? title;
  const dot = base.lastIndexOf('.');
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : '';
}

/** Whether a buffer title carries an extension at all — the gate on content sniffing. */
export function hasExtension(title: string): boolean {
  return extensionOf(title) !== '';
}

/**
 * Guess a language from buffer content. Returns a language-data name, or null when
 * nothing is confident.
 *
 * Deliberately conservative: a shebang is decisive on its own, but every other family
 * needs **two** corroborating signals before the buffer's language changes underneath
 * a cursor. A single `import` line is not enough — it appears in JavaScript, Python,
 * Rust and Java alike, and flipping the grammar mid-sentence is worse than staying on
 * Markdown a few keystrokes longer. Pure — unit-tested.
 */
export function sniffLanguage(content: string): string | null {
  const head = content.slice(0, 8000);
  if (!head.trim()) return null;

  const shebang = /^#![^\n]*\b(python[\d.]*|node|bash|sh|zsh|ruby|perl)\b/.exec(head);
  if (shebang) {
    const bin = shebang[1];
    if (bin.startsWith('python')) return 'Python';
    if (bin === 'node') return 'JavaScript';
    if (bin === 'ruby') return 'Ruby';
    if (bin === 'perl') return 'Perl';
    return 'Shell';
  }

  // A whole-document structural match is decisive on its own — nothing else parses.
  const trimmed = head.trim();
  if (/^[[{]/.test(trimmed) && /[\]}]\s*$/.test(content.trim())) {
    try {
      JSON.parse(content);
      return 'JSON';
    } catch {
      /* not JSON — fall through to the signal count */
    }
  }

  const count = (patterns: RegExp[]): number =>
    patterns.reduce((n, re) => n + (re.test(head) ? 1 : 0), 0);

  const scores: Array<[string, number]> = [
    [
      'C++',
      count([
        /^\s*#include\s*[<"]/m,
        /\bstd::/,
        /^\s*(?:template\s*<|namespace\s+\w+\s*\{)/m,
        /^\s*(?:public|private|protected)\s*:/m,
      ]),
    ],
    [
      'Rust',
      count([
        /^\s*(?:pub\s+)?fn\s+\w+/m,
        /\blet\s+mut\s+/,
        /^\s*impl(?:<[^>]*>)?\s+\w/m,
        /^\s*use\s+[\w:]+(?:::\{|\s*;)/m,
        /^\s*#\[derive\(/m,
      ]),
    ],
    [
      'Python',
      count([
        /^\s*def\s+\w+\s*\(/m,
        /^\s*(?:from\s+[\w.]+\s+)?import\s+\w/m,
        /^\s*class\s+\w+(?:\s*\([^)]*\))?\s*:/m,
        /\bself\b/,
        /^\s*(?:if|for|while|with|try|elif|else)\b[^\n]*:\s*$/m,
      ]),
    ],
    [
      'JavaScript',
      count([
        /^\s*(?:export\s+)?(?:async\s+)?function\s+\w+/m,
        /^\s*(?:const|let|var)\s+\w+\s*=/m,
        /=>\s*[{(]/,
        /^\s*import\s+[\w{*][^\n]*\bfrom\b/m,
        /\bconsole\.log\(/,
      ]),
    ],
    ['Go', count([/^\s*package\s+\w+\s*$/m, /^\s*func\s+\w*\s*\(/m, /:=/, /^\s*import\s+\(/m])],
    ['Markdown', count([/^#{1,6}\s+\S/m, /^\s*[-*]\s+\S/m, /^```/m, /\[[^\]]+\]\([^)]+\)/])],
  ];

  scores.sort((a, b) => b[1] - a[1]);
  const [name, score] = scores[0];
  // Two signals minimum, and a clear winner — a tie means the evidence points two ways
  // at once, which is exactly when guessing does damage.
  if (score < 2 || score === scores[1][1]) return null;
  return name;
}

/** What a buffer's language should be, from everything known about it. */
export function resolveLanguage(input: {
  /** The tab title / filename. */
  title: string;
  /** An explicit hint from the opener (a note opened by the visualizer). */
  hint?: string | null;
  /** The buffer's current text — consulted only when the title has no extension. */
  content?: string;
  /** A language the user chose by hand; overrides everything. */
  pinned?: string | null;
}): ResolvedLanguage {
  const { title, hint, content, pinned } = input;

  const byName = (name: string | null | undefined): ResolvedLanguage | null => {
    if (!name) return null;
    const desc = descriptionFor(name);
    return desc ? { name: desc.name, desc, lspId: LSP_IDS[desc.name] ?? null } : null;
  };

  const pin = byName(pinned);
  if (pin) return pin;

  // The opener's hint arrives as an lsp-ish id ('python', 'javascript'), which
  // matchLanguageName resolves; keep it ahead of the filename since a note has none.
  const hinted = byName(hint);
  if (hinted) return hinted;

  const ext = extensionOf(title);
  if (ext) {
    const narrowed = byName(EXT_NAMES[ext]);
    if (narrowed) return narrowed;
    const matched = LanguageDescription.matchFilename(
      languages,
      title.split(/[/\\]/).pop() ?? title,
    );
    if (matched) return { name: matched.name, desc: matched, lspId: LSP_IDS[matched.name] ?? null };
  } else if (content) {
    const sniffed = byName(sniffLanguage(content));
    if (sniffed) return sniffed;
  }

  return byName('Markdown') ?? { name: 'Markdown', desc: null, lspId: null };
}

/**
 * Map a filename to an LSP languageId, or null. Retained as the narrow entry point for
 * callers that only have a name (the notebook coordinator, the editor service).
 */
export function lspLanguageId(nameOrPath: string): string | null {
  return resolveLanguage({ title: nameOrPath }).lspId;
}

/** The language names offered in the buffer header's picker, in menu order. */
export const PICKABLE_LANGUAGES: string[] = [
  'Markdown',
  'Python',
  'TypeScript',
  'TSX',
  'JavaScript',
  'JSX',
  'Rust',
  'C++',
  'C',
  'Go',
  'JSON',
  'YAML',
  'TOML',
  'HTML',
  'CSS',
  'SQL',
  'Shell',
];

/**
 * The extension `Save As` proposes for a language.
 *
 * Not `desc.extensions[0]` — language-data lists them in no particular order and its
 * first Python entry is `BUILD`, so a sniffed-Python scratch buffer offered to save
 * itself as `untitled.BUILD`. The conventional extension is a naming convention, not a
 * grammar fact, so the languages we offer name theirs; anything else falls back to the
 * first all-lowercase extension, which at least never proposes a bazel file.
 */
const CANONICAL_EXT: Record<string, string> = {
  Markdown: 'md',
  Python: 'py',
  TypeScript: 'ts',
  TSX: 'tsx',
  JavaScript: 'js',
  JSX: 'jsx',
  Rust: 'rs',
  'C++': 'cpp',
  C: 'c',
  Go: 'go',
  JSON: 'json',
  YAML: 'yaml',
  TOML: 'toml',
  HTML: 'html',
  CSS: 'css',
  SQL: 'sql',
  Shell: 'sh',
};

export function extensionForLanguage(name: string): string {
  const canonical = CANONICAL_EXT[name];
  if (canonical) return canonical;
  const ext = descriptionFor(name)?.extensions?.find((e) => e === e.toLowerCase());
  return ext ?? 'txt';
}
