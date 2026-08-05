/**
 * The CodeMirror side of the documentation popup: a hover tooltip and an explicit
 * Shift-Tab, both resolved through the source chain.
 *
 * Written against plain CodeMirror rather than against the LSP client, because the
 * caller that needed it most has no language server: a notebook cell. The chain
 * handles the difference — a cell asks `kernel` first and gets an answer about the
 * object that actually exists in its namespace, which is strictly better than what
 * a language server could have told it.
 */
import { hoverTooltip, keymap, type Tooltip } from '@codemirror/view';
import type { EditorState, Extension } from '@codemirror/state';

import {
  enabledDocSources,
  lookupDocs,
  type DocEntry,
  type DocLookupRequest,
  type DocSourceId,
} from './chain';
import { renderMarkdown } from './markdown';
import { getSetting } from '../settings';

/** Word characters for symbol extraction: identifiers plus the dots that join them. */
const SYMBOL_CHAR = /[A-Za-z0-9_.]/;

/**
 * The dotted symbol around `pos`.
 *
 * Dots are included so `pd.DataFrame` resolves as one name rather than as
 * `DataFrame` alone — the index needs the prefix to pick between same-named
 * symbols in different modules. A leading dot is trimmed: hovering just after
 * `df.` should not ask about `.`.
 */
export function symbolAt(text: string, pos: number): { text: string; from: number; to: number } {
  let from = pos;
  let to = pos;
  while (from > 0 && SYMBOL_CHAR.test(text[from - 1])) from--;
  while (to < text.length && SYMBOL_CHAR.test(text[to])) to++;
  let symbol = text.slice(from, to);
  const lead = symbol.length - symbol.replace(/^\.+/, '').length;
  if (lead) {
    symbol = symbol.slice(lead);
    from += lead;
  }
  symbol = symbol.replace(/\.+$/, '');
  return { text: symbol, from, to: from + symbol.length };
}

export interface DocsExtensionOptions {
  /** Notebook path, so the `kernel` source can find the session. */
  notebookPath?: () => string | undefined;
  lang?: string;
  /** Restrict the chain — a notebook cell passes the set without `lsp`. */
  sources?: () => DocSourceId[] | undefined;
}

/** Render one resolved entry into a popup body. */
export function renderDocEntry(entry: DocEntry): HTMLElement {
  const dom = document.createElement('div');
  dom.className = 'cm-docs-popup';

  if (entry.signature) {
    const sig = document.createElement('pre');
    sig.className = 'cm-docs-signature';
    // textContent, not innerHTML: a signature is arbitrary text from a package.
    sig.textContent = entry.signature;
    dom.appendChild(sig);
  }
  if (entry.body) dom.appendChild(renderMarkdown(entry.body));

  // Which source answered is not decoration: the same symbol reads differently
  // from a live kernel than from a search result, and a user who disagrees with
  // the answer needs to know which source to reorder.
  const foot = document.createElement('div');
  foot.className = 'cm-docs-source';
  if (entry.url) {
    const link = document.createElement('a');
    link.href = entry.url;
    link.target = '_blank';
    link.rel = 'noreferrer noopener';
    link.textContent = entry.source;
    foot.appendChild(link);
  } else {
    foot.textContent = entry.source;
  }
  dom.appendChild(foot);
  return dom;
}

function requestFor(
  state: EditorState,
  pos: number,
  opts: DocsExtensionOptions,
): DocLookupRequest | null {
  const code = state.doc.toString();
  const { text: symbol } = symbolAt(code, pos);
  if (!symbol) return null;
  return {
    symbol,
    lang: opts.lang ?? 'python',
    notebookPath: opts.notebookPath?.(),
    code,
    cursorPos: pos,
    sources: opts.sources?.(),
  };
}

/**
 * Hover documentation. Honours `docs.hover`; the delay is CodeMirror's own
 * `hoverTime`, read from `docs.hoverDelayMs`.
 */
export function docsHover(opts: DocsExtensionOptions = {}): Extension {
  return hoverTooltip(
    async (view, pos): Promise<Tooltip | null> => {
      if (getSetting<boolean>('docs.hover') === false) return null;
      const req = requestFor(view.state, pos, opts);
      if (!req) return null;
      const { text: symbol, from, to } = symbolAt(view.state.doc.toString(), pos);
      if (!symbol) return null;
      // The web source is dropped from *hover* unless explicitly allowed. It is
      // the only source that leaves the machine: measured at ~7s for a real
      // lookup, and a billable API call per symbol you happen to rest the pointer
      // on. Shift-Tab still uses it, because there the user asked. This is a
      // separate switch rather than a second ordered list, so it cannot disagree
      // with `docs.sources` about priority.
      const sources = (req.sources ?? enabledDocSources()).filter(
        (s) => s !== 'web' || getSetting<boolean>('docs.webOnHover') === true,
      );
      if (!sources.length) return null;
      const { entries } = await lookupDocs({ ...req, sources });
      const entry = entries[0];
      if (!entry) return null;
      return {
        pos: from,
        end: to,
        above: true,
        create: () => ({ dom: renderDocEntry(entry) }),
      };
    },
    { hoverTime: Math.max(0, Number(getSetting<number>('docs.hoverDelayMs') ?? 400)) },
  );
}

/**
 * Shift-Tab: the explicit lookup, Jupyter's gesture.
 *
 * Separate from hover on purpose — hover can be turned off (it is noise while you
 * are typing) without losing the ability to ask. The result is shown through the
 * caller's `show`, because a notebook cell and an editor buffer put a panel in
 * different places.
 */
export function docsKeymap(
  opts: DocsExtensionOptions & { show: (entry: DocEntry | null) => void },
): Extension {
  return keymap.of([
    {
      key: 'Shift-Tab',
      run: (view) => {
        const pos = view.state.selection.main.head;
        const req = requestFor(view.state, pos, opts);
        if (!req) return false;
        void lookupDocs(req).then(({ entries }) => opts.show(entries[0] ?? null));
        return true;
      },
    },
  ]);
}
