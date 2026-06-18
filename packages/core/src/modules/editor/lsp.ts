/**
 * Editor ⇄ Language Server bridge (the frontend half of the LSP spine). This is a
 * minimal LSP **client**: it drives a backend-spawned server (which is a dumb
 * JSON-RPC pipe — see backend/modules/lsp/manager.py) over the shared `/ws` `lsp`
 * channel, and renders `textDocument/publishDiagnostics` through `@codemirror/lint`.
 *
 * First capability is **diagnostics** (the highest-value, most-visible one); the
 * same pipe carries completion/hover/etc. as later additions. Document sync is
 * full-text (`didOpen`/`didChange`/`didClose`) — simple and correct; incremental
 * sync is an optimization for later. See docs/modules/editor.md.
 */
import { type Extension, type Text } from '@codemirror/state';
import {
  EditorView,
  ViewPlugin,
  keymap,
  hoverTooltip,
  type Tooltip,
  type ViewUpdate,
} from '@codemirror/view';
import { lintGutter, setDiagnostics, type Diagnostic } from '@codemirror/lint';
import {
  autocompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from '@codemirror/autocomplete';

import { sendChannel, subscribeChannel, type WsMessage } from '../../ws';

/** LSP CompletionItemKind → CodeMirror completion `type` (drives the icon). */
const COMPLETION_KIND: Record<number, string> = {
  2: 'method',
  3: 'function',
  4: 'function', // constructor
  5: 'property', // field
  6: 'variable',
  7: 'class',
  8: 'interface',
  9: 'namespace', // module
  10: 'property',
  13: 'enum',
  14: 'keyword',
  15: 'text', // snippet
  21: 'constant',
  22: 'type', // struct
  25: 'type', // type parameter
};

interface LspCompletionItem {
  label: string;
  kind?: number;
  detail?: string;
  insertText?: string;
  insertTextFormat?: number; // 1 = plain, 2 = snippet
  textEdit?: { newText: string };
  sortText?: string;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
}

/** The slice of the plugin the statically-configured extensions (completion source,
 * hover tooltip, go-to-definition keymap) reach through `ref.plugin`. */
interface LspClient {
  initialized: boolean;
  complete(context: CompletionContext): Promise<CompletionResult | null>;
  hover(pos: number): Promise<Tooltip | null>;
  definition(pos: number): Promise<void>;
}

/** The text a completion inserts. Snippets (format 2) carry `${1:…}` placeholders
 * we don't expand yet, so fall back to the plain label for those. */
function completionApply(it: LspCompletionItem): string {
  if (it.insertTextFormat === 2) return it.label;
  return it.textEdit?.newText ?? it.insertText ?? it.label;
}

/** Map a file extension to an LSP languageId the backend has a server for, or null
 * (no LSP). Kept in sync with `LSP_SERVERS` in the backend manager. */
export function lspLanguageId(nameOrPath: string): string | null {
  const ext = nameOrPath.toLowerCase().split('.').pop() ?? '';
  switch (ext) {
    case 'py':
      return 'python';
    case 'ts':
    case 'mts':
    case 'cts':
      return 'typescript';
    case 'tsx':
      return 'typescriptreact';
    case 'js':
    case 'mjs':
    case 'cjs':
      return 'javascript';
    case 'jsx':
      return 'javascriptreact';
    case 'rs':
      return 'rust';
    default:
      return null;
  }
}

/** The directory of an absolute path (its own separator). */
export function dirOf(path: string): string {
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return i <= 0 ? path : path.slice(0, i);
}

/** A `file://` URI for an absolute OS path (handles Windows drive letters). */
function pathToUri(path: string): string {
  let s = path.replace(/\\/g, '/');
  if (/^[A-Za-z]:/.test(s)) s = `/${s}`; // C:/x → /C:/x
  return `file://${encodeURI(s)}`;
}

interface LspPosition {
  line: number;
  character: number;
}
interface LspDiagnostic {
  range: { start: LspPosition; end: LspPosition };
  severity?: number;
  message: string;
  source?: string;
}

/** LSP (line, character) → a CodeMirror document offset, clamped to the doc. */
function offsetAt(view: EditorView, pos: LspPosition): number {
  const doc = view.state.doc;
  const line = doc.line(Math.min(Math.max(pos.line + 1, 1), doc.lines));
  return Math.min(line.from + pos.character, line.to);
}

function toCmDiagnostic(view: EditorView, d: LspDiagnostic): Diagnostic {
  const severity = d.severity === 1 ? 'error' : d.severity === 2 ? 'warning' : 'info';
  return {
    from: offsetAt(view, d.range.start),
    to: offsetAt(view, d.range.end),
    severity,
    message: d.source ? `${d.message} (${d.source})` : d.message,
  };
}

/** A CodeMirror document offset → LSP (line, character). */
function positionAt(doc: Text, offset: number): LspPosition {
  const line = doc.lineAt(offset);
  return { line: line.number - 1, character: offset - line.from };
}

/** Reverse of `pathToUri`: a `file://` URI → an OS path matching `sep`'s flavor. */
function uriToPath(uri: string, sep: string): string {
  let s = decodeURI(uri.replace(/^file:\/\//, ''));
  if (/^\/[A-Za-z]:/.test(s)) s = s.slice(1); // /C:/x → C:/x
  return sep === '\\' ? s.replace(/\//g, '\\') : s;
}

/** Whether two `file://` URIs point at the same document. Servers vary in drive-
 * letter case and slash count (`file:///c:/…` vs our `file:///C:/…`), so compare
 * normalized paths — case-insensitively on Windows. */
function sameUri(a: string, b: string, sep: string): boolean {
  const norm = (u: string): string => {
    const p = uriToPath(u, sep);
    return sep === '\\' ? p.toLowerCase() : p;
  };
  return norm(a) === norm(b);
}

type HoverContents = string | { value: string } | (string | { value: string })[];

/** Flatten an LSP hover `contents` (MarkupContent / MarkedString[s]) to plain text. */
function hoverText(result: unknown): string {
  const contents = (result as { contents?: HoverContents } | null)?.contents;
  if (!contents) return '';
  const one = (c: string | { value: string }): string => (typeof c === 'string' ? c : c.value);
  const text = Array.isArray(contents) ? contents.map(one).join('\n\n') : one(contents);
  return text.trim();
}

interface LspLocation {
  uri: string;
  range: { start: LspPosition; end: LspPosition };
}

/** Normalize `textDocument/definition`'s many shapes (Location, Location[],
 * LocationLink[]) to a single target, or null. */
function firstLocation(result: unknown): LspLocation | null {
  const r = Array.isArray(result) ? result[0] : result;
  if (!r || typeof r !== 'object') return null;
  const link = r as { targetUri?: string; targetSelectionRange?: LspLocation['range'] };
  if (link.targetUri && link.targetSelectionRange) {
    return { uri: link.targetUri, range: link.targetSelectionRange };
  }
  const loc = r as LspLocation;
  return loc.uri && loc.range ? { uri: loc.uri, range: loc.range } : null;
}

let sessionCounter = 0;

export interface LspOptions {
  path: string;
  languageId: string;
  root: string;
  /** Open another file at a 0-based line for cross-file go-to-definition (the
   * editor module supplies this; same-file jumps move the cursor directly). */
  openFile?: (path: string, line: number) => void;
}

/** Build the LSP extension for one buffer. Manages a server session for its
 * lifetime: start → initialize → didOpen, debounced didChange, didClose on
 * destroy, plus completion, hover, and go-to-definition. Stays quiet if the
 * backend has no server for the language. */
export function lspExtension(opts: LspOptions): Extension {
  const uri = pathToUri(opts.path);
  const sep = opts.path.includes('\\') ? '\\' : '/';
  // The completion source is configured once (below) but must reach the *live*
  // plugin instance to issue requests — share it through this ref.
  const ref: { plugin: LspClient | null } = { plugin: null };

  const plugin = ViewPlugin.fromClass(
    class implements LspClient {
      sessionId = `lsp-${++sessionCounter}`;
      version = 1;
      initialized = false;
      dead = false;
      changeTimer: number | undefined;
      nextId = 2; // 1 is reserved for initialize
      pending = new Map<number, PendingRequest>();
      unsubscribe: () => void;

      constructor(readonly view: EditorView) {
        this.unsubscribe = subscribeChannel('lsp', (msg) => this.onMessage(msg));
        sendChannel('lsp', 'start', {
          sessionId: this.sessionId,
          languageId: opts.languageId,
          root: opts.root,
        });
        ref.plugin = this;
      }

      rpc(payload: Record<string, unknown>): void {
        sendChannel('lsp', 'rpc', { sessionId: this.sessionId, payload });
      }

      /** A JSON-RPC request whose response resolves the returned promise. */
      request(method: string, params: Record<string, unknown>): Promise<unknown> {
        const id = this.nextId++;
        return new Promise<unknown>((resolve, reject) => {
          this.pending.set(id, { resolve, reject });
          this.rpc({ jsonrpc: '2.0', id, method, params });
          window.setTimeout(() => {
            if (this.pending.delete(id)) reject(new Error('lsp request timed out'));
          }, 4000);
        });
      }

      async complete(context: CompletionContext): Promise<CompletionResult | null> {
        const word = context.matchBefore(/[\w$]+/);
        const dot = context.matchBefore(/\.[\w$]*$/);
        if (!context.explicit && !dot && !word) return null;
        const doc = this.view.state.doc;
        const line = doc.lineAt(context.pos);
        const position = { line: line.number - 1, character: context.pos - line.from };
        let result: unknown;
        try {
          result = await this.request('textDocument/completion', {
            textDocument: { uri },
            position,
          });
        } catch {
          return null; // timed out / errored — no completions
        }
        const items = (
          Array.isArray(result)
            ? result
            : ((result as { items?: LspCompletionItem[] })?.items ?? [])
        ) as LspCompletionItem[];
        if (!items.length) return null;
        const options: Completion[] = items.slice(0, 200).map((it) => ({
          label: it.label,
          type: it.kind ? COMPLETION_KIND[it.kind] : undefined,
          detail: it.detail,
          apply: completionApply(it),
        }));
        return { from: word ? word.from : context.pos, options, validFor: /^[\w$]*$/ };
      }

      async hover(pos: number): Promise<Tooltip | null> {
        let result: unknown;
        try {
          result = await this.request('textDocument/hover', {
            textDocument: { uri },
            position: positionAt(this.view.state.doc, pos),
          });
        } catch {
          return null;
        }
        const text = hoverText(result);
        if (!text) return null;
        return {
          pos,
          above: true,
          create: () => {
            const dom = document.createElement('div');
            dom.className = 'cm-lsp-hover';
            dom.textContent = text;
            return { dom };
          },
        };
      }

      async definition(pos: number): Promise<void> {
        let result: unknown;
        try {
          result = await this.request('textDocument/definition', {
            textDocument: { uri },
            position: positionAt(this.view.state.doc, pos),
          });
        } catch {
          return;
        }
        const loc = firstLocation(result);
        if (!loc) return;
        if (sameUri(loc.uri, uri, sep)) {
          // Same file: move the cursor to the definition and scroll to it.
          const off = offsetAt(this.view, loc.range.start);
          this.view.dispatch({ selection: { anchor: off }, scrollIntoView: true });
          this.view.focus();
        } else {
          opts.openFile?.(uriToPath(loc.uri, sep), loc.range.start.line);
        }
      }

      onMessage(msg: WsMessage): void {
        const data = (msg.data ?? {}) as { sessionId?: string; payload?: Record<string, unknown> };
        if (data.sessionId !== this.sessionId) return;
        if (msg.event === 'error' || msg.event === 'exit') {
          this.dead = true;
          return;
        }
        if (msg.event === 'started') {
          this.rpc({
            jsonrpc: '2.0',
            id: 1,
            method: 'initialize',
            params: {
              processId: null,
              rootUri: pathToUri(opts.root),
              capabilities: {
                textDocument: {
                  synchronization: { dynamicRegistration: false },
                  publishDiagnostics: { relatedInformation: false },
                  completion: {
                    completionItem: { snippetSupport: false },
                    contextSupport: false,
                  },
                  hover: { contentFormat: ['plaintext', 'markdown'] },
                  definition: { dynamicRegistration: false },
                },
              },
              workspaceFolders: null,
            },
          });
          return;
        }
        if (msg.event !== 'rpc' || !data.payload) return;
        const p = data.payload;
        // The initialize response → complete the handshake and open the document.
        if (!this.initialized && p.id === 1 && 'result' in p) {
          this.initialized = true;
          this.rpc({ jsonrpc: '2.0', method: 'initialized', params: {} });
          this.rpc({
            jsonrpc: '2.0',
            method: 'textDocument/didOpen',
            params: {
              textDocument: {
                uri,
                languageId: opts.languageId,
                version: this.version,
                text: this.view.state.doc.toString(),
              },
            },
          });
          return;
        }
        // Resolve a pending request (completion, hover, …) by its JSON-RPC id.
        if (typeof p.id === 'number' && this.pending.has(p.id)) {
          const waiter = this.pending.get(p.id)!;
          this.pending.delete(p.id);
          if ('error' in p) waiter.reject(p.error);
          else waiter.resolve(p.result);
          return;
        }
        if (p.method === 'textDocument/publishDiagnostics') {
          const params = p.params as { uri?: string; diagnostics?: LspDiagnostic[] } | undefined;
          if (!params || params.uri !== uri) return;
          const diags = (params.diagnostics ?? [])
            .map((d) => toCmDiagnostic(this.view, d))
            .sort((a, b) => a.from - b.from);
          this.view.dispatch(setDiagnostics(this.view.state, diags));
        }
      }

      update(u: ViewUpdate): void {
        if (!u.docChanged || !this.initialized || this.dead) return;
        if (this.changeTimer !== undefined) window.clearTimeout(this.changeTimer);
        this.changeTimer = window.setTimeout(() => this.sendChange(), 300);
      }

      sendChange(): void {
        if (this.dead) return;
        this.rpc({
          jsonrpc: '2.0',
          method: 'textDocument/didChange',
          params: {
            textDocument: { uri, version: ++this.version },
            contentChanges: [{ text: this.view.state.doc.toString() }],
          },
        });
      }

      destroy(): void {
        if (this.changeTimer !== undefined) window.clearTimeout(this.changeTimer);
        if (this.initialized && !this.dead) {
          this.rpc({
            jsonrpc: '2.0',
            method: 'textDocument/didClose',
            params: { textDocument: { uri } },
          });
        }
        sendChannel('lsp', 'stop', { sessionId: this.sessionId });
        this.unsubscribe();
        ref.plugin = null;
      }
    },
  );

  // LSP is the authoritative completion source for code buffers; it returns null
  // when the server isn't ready or has nothing, so completion just stays quiet.
  const completionSource = (context: CompletionContext): Promise<CompletionResult | null> =>
    ref.plugin?.initialized ? ref.plugin.complete(context) : Promise.resolve(null);

  const hover = hoverTooltip((_view, pos) =>
    ref.plugin?.initialized ? ref.plugin.hover(pos) : null,
  );

  // F12 jumps to definition (same-file: move the cursor; cross-file: open it).
  const gotoDefinition = keymap.of([
    {
      key: 'F12',
      run: (view) => {
        if (!ref.plugin?.initialized) return false;
        void ref.plugin.definition(view.state.selection.main.head);
        return true;
      },
    },
  ]);

  return [
    lintGutter(),
    plugin,
    autocompletion({ override: [completionSource] }),
    hover,
    gotoDefinition,
  ];
}
