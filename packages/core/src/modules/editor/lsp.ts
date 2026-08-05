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
  pickedCompletion,
  snippetCompletion,
  startCompletion,
  type Completion,
  type CompletionContext,
  type CompletionResult,
  type CompletionSource,
} from '@codemirror/autocomplete';

import { dialogs } from '../../dialogs';
import { enabledDocSources, lookupDocs } from '../../docs/chain';
import { renderDocEntry, symbolAt } from '../../docs/cm-docs';
import { renderMarkdown } from '../../docs/markdown';
import { sendChannel, subscribeChannel, type WsMessage } from '../../ws';
import { getBuffer } from './buffers';
import {
  recordDiagnostics,
  registerLspClient,
  type AgentDiagnostic,
  type AgentReference,
  type LspBufferClient,
  type LspGrounding,
  type LspTarget,
  type RenameOutcome,
} from './lsp-registry';
import { loadSource, saveSource } from './sources';
import { frameworkImportSource } from './pythonImports';
import { dbSymbolSource } from './symbolCompletion';
import { fetchPythonEnv } from './pythonEnv';

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

/** LSP MarkupContent (or the legacy plain-string form) — hover/documentation bodies. */
type MarkupContent = string | { kind?: string; value: string };

interface LspCompletionItem {
  label: string;
  kind?: number;
  detail?: string;
  documentation?: MarkupContent;
  insertText?: string;
  insertTextFormat?: number; // 1 = plain, 2 = snippet
  // A plain `TextEdit` (`range`) or an `InsertReplaceEdit` (`insert`/`replace`); the
  // range says what `newText` replaces, which can extend before the typed word (e.g.
  // an auto-import whose `newText` is a qualified `os.O_ASYNC` over `os.`).
  textEdit?: {
    newText: string;
    range?: { start: LspPosition; end: LspPosition };
    insert?: { start: LspPosition; end: LspPosition };
    replace?: { start: LspPosition; end: LspPosition };
  };
  sortText?: string;
  // `data` (and any other server-specific fields) ride along on the parsed object so
  // `completionItem/resolve` can echo the item back verbatim; not all are typed here.
  data?: unknown;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason?: unknown) => void;
}

/** The slice of the plugin the statically-configured extensions (completion source,
 * hover tooltip, go-to-definition + rename keymaps) and the by-URI client registry
 * reach through `ref.plugin`. Extends the agent-facing `LspBufferClient`. */
interface LspClient extends LspBufferClient {
  initialized: boolean;
  dead: boolean;
  /** Characters that auto-open the completion list (from the server's capabilities). */
  triggerChars: string[];
  flushChanges(): void;
  /** Resolve once the server is usable, or after `timeoutMs`; true if it became ready. */
  waitReady(timeoutMs: number): Promise<boolean>;
  complete(context: CompletionContext): Promise<CompletionResult | null>;
  hover(pos: number): Promise<Tooltip | null>;
  definition(pos: number): Promise<void>;
}

/** The literal text a plain (non-snippet) completion inserts. */
function completionInsertText(it: LspCompletionItem): string {
  return it.textEdit?.newText ?? it.insertText ?? it.label;
}

/** The start position an item's `textEdit` replaces — a plain `TextEdit` `range`, or
 * an `InsertReplaceEdit`'s `replace`/`insert` — or undefined for a bare insert. The
 * start is what matters: it can sit before the typed word, so honoring it stops
 * qualified inserts (auto-imports) from doubling. */
function textEditStart(te: LspCompletionItem['textEdit']): LspPosition | undefined {
  return (te?.range ?? te?.replace ?? te?.insert)?.start;
}

/** Convert an LSP snippet body to a CodeMirror snippet template. LSP tab stops
 * (`${1:default}`, bare `$1`, final `$0`) map onto CodeMirror's `${…}` fields;
 * CodeMirror orders fields by document position, which matches how servers emit
 * them, and `$0`/`${0}` become the empty final field. */
function lspSnippetToCm(body: string): string {
  return body
    .replace(/\$\{0(:[^}]*)?\}/g, '${}') // ${0} / ${0:x} → final cursor
    .replace(/\$0/g, '${}') // $0 → final cursor
    .replace(/\$(\d+)/g, '${$1}'); // bare $1 → ${1}
}

/** Plain-text body of an LSP MarkupContent (or legacy string), else empty. */
function markupText(doc: MarkupContent | undefined): string {
  if (!doc) return '';
  return typeof doc === 'string' ? doc : (doc.value ?? '');
}

// Named HTML entities that turn up in LSP docstrings (servers convert RST/plain
// docstrings to markdown, aligning columns with `&nbsp;` and escaping `<`/`>`/`&`).
// We decode these to their characters so they don't render literally as `&nbsp;`.

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

/** LSP DiagnosticSeverity (1=error … 4=hint) → the agent-facing severity name. */
function severityName(s?: number): AgentDiagnostic['severity'] {
  return s === 1 ? 'error' : s === 2 ? 'warning' : s === 4 ? 'hint' : 'info';
}

/** Flatten an LSP diagnostic to the 1-based shape the agent read tools expose. */
function toAgentDiagnostic(d: LspDiagnostic): AgentDiagnostic {
  return {
    severity: severityName(d.severity),
    message: d.message,
    source: d.source,
    line: d.range.start.line + 1,
    column: d.range.start.character + 1,
    endLine: d.range.end.line + 1,
    endColumn: d.range.end.character + 1,
  };
}

interface TextEdit {
  range: { start: LspPosition; end: LspPosition };
  newText: string;
}
interface WorkspaceEdit {
  changes?: Record<string, TextEdit[]>;
  documentChanges?: unknown[];
}

/** Collapse a WorkspaceEdit's two shapes (`changes` map and `documentChanges`
 * array) into one `uri → edits` map. */
function workspaceEditChanges(edit: WorkspaceEdit): Map<string, TextEdit[]> {
  const out = new Map<string, TextEdit[]>();
  const add = (u: string, edits: TextEdit[]): void => {
    out.set(u, (out.get(u) ?? []).concat(edits));
  };
  if (edit.changes) for (const [u, edits] of Object.entries(edit.changes)) add(u, edits);
  for (const dc of edit.documentChanges ?? []) {
    const d = dc as { textDocument?: { uri?: string }; edits?: TextEdit[] };
    if (d.textDocument?.uri && Array.isArray(d.edits)) add(d.textDocument.uri, d.edits);
  }
  return out;
}

/** An LSP (line, character) → an offset into a raw string (no CodeMirror doc),
 * used to apply edits to a file that isn't the live buffer. Counts `\n`-delimited
 * lines; mid-line edits (the only ones rename produces) are exact. */
function stringOffsetAt(content: string, pos: LspPosition): number {
  let line = 0;
  let i = 0;
  while (line < pos.line && i < content.length) {
    const nl = content.indexOf('\n', i);
    if (nl < 0) return content.length;
    i = nl + 1;
    line++;
  }
  return Math.min(i + pos.character, content.length);
}

/** Apply LSP text edits to a plain string (rightmost-first so offsets stay valid). */
function applyEditsToString(content: string, edits: TextEdit[]): string {
  const ranges = edits
    .map((e) => ({
      from: stringOffsetAt(content, e.range.start),
      to: stringOffsetAt(content, e.range.end),
      newText: e.newText,
    }))
    .sort((a, b) => b.from - a.from);
  let out = content;
  for (const r of ranges) out = out.slice(0, r.from) + r.newText + out.slice(r.to);
  return out;
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

/** A document open on a shared session: the callback the session invokes to route this
 * document's diagnostics back to its buffer's linter. */
interface SessionDoc {
  onDiagnostics: (raw: LspDiagnostic[]) => void;
}

/**
 * One language-server session, **shared by every buffer** on the same interpreter +
 * project root (the pool key). Owns the transport — its `sessionId`, the `start`/
 * `initialize` handshake, a single JSON-RPC id space + pending map, and
 * `workspace/configuration` answering — plus a registry of open documents, routing each
 * server `publishDiagnostics` to the owning buffer by URI. The backend spawns one
 * process per `sessionId`, so one session here is **one basedpyright indexed once for
 * the whole project**. Refcounted: the server is stopped when the last document closes.
 */
class LspSession {
  readonly sessionId = `lsp-${++sessionCounter}`;
  initialized = false;
  dead = false;
  triggerChars: string[] = [];
  resolveProvider = false;
  refcount = 0;
  // Set while the session is idle (no open documents) but kept warm before disposal —
  // cleared if a buffer re-acquires it (see `acquireSession`).
  disposeTimer: number | undefined;
  private nextId = 2; // 1 is reserved for initialize
  private pending = new Map<number, PendingRequest>();
  private docs = new Map<string, SessionDoc>();
  private queuedOpens: (() => void)[] = [];
  private readonly unsubscribe: () => void;
  // Resolved once `initialize` comes back (or the session dies), so a completion
  // request that arrives during the cold start can *wait* for the server instead of
  // silently returning nothing — spawning basedpyright and indexing a project takes
  // seconds, and that used to be a dead window with no results and no retry.
  private readyResolve!: () => void;
  readonly readyPromise: Promise<void> = new Promise<void>((res) => {
    this.readyResolve = res;
  });
  // Called when the session becomes ready, so open popups can re-query (see
  // `lspExtension`). Cleared after firing — readiness happens once per session.
  private readyListeners: (() => void)[] = [];

  /** Run `fn` when the session finishes initializing (immediately if it already has). */
  onReady(fn: () => void): void {
    if (this.initialized || this.dead) fn();
    else this.readyListeners.push(fn);
  }

  /** Settle the readiness promise and notify listeners. Idempotent. */
  private settleReady(): void {
    this.readyResolve();
    const listeners = this.readyListeners;
    this.readyListeners = [];
    for (const fn of listeners) fn();
  }

  /** Resolve once the server is usable, or after `timeoutMs` — whichever comes first.
   * Returns whether it actually became ready. */
  async waitReady(timeoutMs: number): Promise<boolean> {
    if (this.ready()) return true;
    if (this.dead || timeoutMs <= 0) return false;
    let timer: number | undefined;
    await Promise.race([
      this.readyPromise,
      new Promise<void>((res) => {
        timer = window.setTimeout(res, timeoutMs);
      }),
    ]);
    if (timer !== undefined) window.clearTimeout(timer);
    return this.ready();
  }

  constructor(
    readonly key: string,
    readonly languageId: string,
    readonly root: string,
    readonly interpreter: string | undefined,
  ) {
    this.unsubscribe = subscribeChannel('lsp', (msg) => this.onMessage(msg));
    sendChannel('lsp', 'start', { sessionId: this.sessionId, languageId, root });
  }

  ready(): boolean {
    return this.initialized && !this.dead;
  }

  private rpc(payload: Record<string, unknown>): void {
    sendChannel('lsp', 'rpc', { sessionId: this.sessionId, payload });
  }

  /** A JSON-RPC request whose response resolves the returned promise. */
  request(method: string, params: Record<string, unknown>, timeoutMs = 4000): Promise<unknown> {
    if (this.dead) return Promise.reject(new Error('lsp session dead'));
    const id = this.nextId++;
    return new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.rpc({ jsonrpc: '2.0', id, method, params });
      window.setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error('lsp request timed out'));
      }, timeoutMs);
    });
  }

  /** Register a document and open it on the server (queued until initialize completes). */
  openDocument(
    uri: string,
    languageId: string,
    textOrFn: string | (() => string),
    doc: SessionDoc,
  ): void {
    this.docs.set(uri, doc);
    const open = (): void => {
      if (this.dead) return;
      const text = typeof textOrFn === 'function' ? textOrFn() : textOrFn;
      this.rpc({
        jsonrpc: '2.0',
        method: 'textDocument/didOpen',
        params: { textDocument: { uri, languageId, version: 1, text } },
      });
    };
    if (this.initialized) open();
    else this.queuedOpens.push(open);
  }

  didChange(uri: string, version: number, text: string): void {
    if (!this.ready()) return;
    this.rpc({
      jsonrpc: '2.0',
      method: 'textDocument/didChange',
      params: { textDocument: { uri, version }, contentChanges: [{ text }] },
    });
  }

  closeDocument(uri: string): void {
    if (this.ready()) {
      this.rpc({
        jsonrpc: '2.0',
        method: 'textDocument/didClose',
        params: { textDocument: { uri } },
      });
    }
    this.docs.delete(uri);
  }

  dispose(): void {
    sendChannel('lsp', 'stop', { sessionId: this.sessionId });
    this.unsubscribe();
    this.dead = true;
    // Unblock anyone waiting on the cold start — the wait is bounded anyway, but a
    // disposed session should never make a completion request sit out its timeout.
    this.settleReady();
  }

  /** Reply to a server→client request. We service `workspace/configuration` (how
   * basedpyright pulls `python.pythonPath` so third-party imports resolve) and ack
   * everything else with a `null` result so nothing blocks. */
  private answerServerRequest(id: number | string, method: string, params: unknown): void {
    let result: unknown = null;
    if (method === 'workspace/configuration') {
      const items = (params as { items?: { section?: string }[] } | undefined)?.items ?? [];
      result = items.map((it) => this.configFor(it?.section));
    }
    this.rpc({ jsonrpc: '2.0', id, result });
  }

  /** The value for one `workspace/configuration` section: `python` carries the session's
   * interpreter (part of its pool key) + the analysis knobs; an `analysis` section gets
   * just those; anything else is empty (the server keeps its defaults). */
  private configFor(section: string | undefined): Record<string, unknown> {
    const analysis = {
      autoSearchPaths: true,
      useLibraryCodeForTypes: true,
      diagnosticMode: 'openFilesOnly',
    };
    if (section && section.includes('analysis')) return analysis;
    if (section && section !== 'python') return {};
    return this.interpreter ? { pythonPath: this.interpreter, analysis } : { analysis };
  }

  private onMessage(msg: WsMessage): void {
    const data = (msg.data ?? {}) as { sessionId?: string; payload?: Record<string, unknown> };
    if (data.sessionId !== this.sessionId) return;
    if (msg.event === 'error' || msg.event === 'exit') {
      this.dead = true;
      this.settleReady();
      return;
    }
    if (msg.event === 'started') {
      this.rpc({
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: {
          processId: null,
          rootUri: pathToUri(this.root),
          capabilities: {
            workspace: {
              configuration: true,
              didChangeConfiguration: { dynamicRegistration: true },
            },
            textDocument: {
              synchronization: { dynamicRegistration: false },
              publishDiagnostics: { relatedInformation: false },
              completion: {
                completionItem: {
                  snippetSupport: true,
                  documentationFormat: ['markdown', 'plaintext'],
                  resolveSupport: { properties: ['documentation', 'detail'] },
                },
                contextSupport: false,
              },
              hover: { contentFormat: ['markdown', 'plaintext'] },
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
    // Initialize response → capture completion capabilities, complete the handshake,
    // then flush the didOpens that were queued while it was in flight.
    if (!this.initialized && p.id === 1 && 'result' in p) {
      const caps = (
        p.result as {
          capabilities?: {
            completionProvider?: { triggerCharacters?: string[]; resolveProvider?: boolean };
          };
        } | null
      )?.capabilities;
      const cp = caps?.completionProvider;
      if (Array.isArray(cp?.triggerCharacters)) this.triggerChars = cp.triggerCharacters;
      this.resolveProvider = cp?.resolveProvider === true;
      this.initialized = true;
      this.rpc({ jsonrpc: '2.0', method: 'initialized', params: {} });
      for (const open of this.queuedOpens) open();
      this.queuedOpens = [];
      this.settleReady();
      return;
    }
    // A server→client *request* (a `method` plus an `id`) must be answered or the server
    // stalls. Checked before the response branch because responses carry no `method`.
    if (typeof p.method === 'string' && (typeof p.id === 'number' || typeof p.id === 'string')) {
      this.answerServerRequest(p.id, p.method, p.params);
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
    // Diagnostics → route to the owning document's buffer by URI.
    if (p.method === 'textDocument/publishDiagnostics') {
      const params = p.params as { uri?: string; diagnostics?: LspDiagnostic[] } | undefined;
      if (!params?.uri) return;
      this.docs.get(params.uri)?.onDiagnostics(params.diagnostics ?? []);
    }
  }
}

// Pool of live sessions keyed by `${languageId}::${root}::${interpreter}` — every buffer
// sharing a key shares one server (indexed once).
const sessionPool = new Map<string, LspSession>();

// Keep an idle (no-documents) session warm this long before stopping its server, so
// switching tabs — which unmounts one buffer and mounts another — reuses the already-
// indexed server instead of respawning and reindexing it.
const SESSION_IDLE_MS = 60_000;

function acquireSession(
  key: string,
  languageId: string,
  root: string,
  interpreter: string | undefined,
): LspSession {
  let session = sessionPool.get(key);
  if (!session || session.dead) {
    session = new LspSession(key, languageId, root, interpreter);
    sessionPool.set(key, session);
  } else if (session.disposeTimer !== undefined) {
    // Re-acquired while idle — cancel the pending disposal and reuse the warm server.
    window.clearTimeout(session.disposeTimer);
    session.disposeTimer = undefined;
  }
  session.refcount++;
  return session;
}

function releaseSession(session: LspSession): void {
  session.refcount--;
  if (session.refcount > 0 || session.disposeTimer !== undefined) return;
  // Idle: stop the server after a grace period unless it's re-acquired first.
  session.disposeTimer = window.setTimeout(() => {
    session.disposeTimer = undefined;
    if (session.refcount > 0) return;
    if (sessionPool.get(session.key) === session) sessionPool.delete(session.key);
    session.dispose();
  }, SESSION_IDLE_MS);
}

export interface LspOptions {
  path: string;
  languageId: string;
  root: string;
  /** The buffer's source URI (`workspace-file:<path>`) — the key the diagnostics
   * store and the by-URI client registry use, so the agent tools can target this
   * buffer by the same URI the edit tools use. */
  bufferUri: string;
  /** Open another file at a 0-based line for cross-file go-to-definition (the
   * editor module supplies this; same-file jumps move the cursor directly). */
  openFile?: (path: string, line: number) => void;
  /** Explicit Python interpreter (the `editor.pythonPath` setting) reported to the
   * server as `python.pythonPath`, so third-party imports resolve. Overrides the
   * backend's auto-detected interpreter; empty/undefined falls back to that. */
  pythonPathOverride?: string;
  /** Whether to offer the curated framework-import completions (the
   * `editor.frameworkImports` setting; Python only). Defaults to on. */
  frameworkImports?: boolean;
  /** How long a completion request waits for a still-warming language server before
   * giving up and returning only the instant sources (`editor.completionWarmupMs`).
   * The cold start — spawn, `initialize`, project index — is seconds; without this
   * the first completions in a freshly-opened buffer came back empty. Defaults to
   * 2000ms; 0 disables waiting. */
  warmupMs?: number;
  /** Debounce before a buffer edit is pushed to the server as `didChange`
   * (`editor.changeDebounceMs`). Lower = fresher completions, more traffic.
   * Defaults to 300ms. */
  changeDebounceMs?: number;
  /** Whether to publish language-server diagnostics into the lint gutter
   * (`editor.diagnostics`). Defaults to on. */
  diagnostics?: boolean;
  /** Whether hovering a symbol shows the server's tooltip (`editor.hover`).
   * Defaults to on. */
  hover?: boolean;
  /** Whether to merge the indexed stdlib/package symbols (the symdex prefix index)
   * into the completion popup (`editor.indexedSymbols`). Defaults to on. */
  indexedSymbols?: boolean;
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
  // Installed framework versions for this file's interpreter, populated async once the
  // plugin resolves the environment — the framework-import source gates/annotates on them.
  // (The interpreter itself keys the shared session; see `connect`.)
  const env: { packages?: Record<string, string> } = {};

  const plugin = ViewPlugin.fromClass(
    class implements LspClient {
      version = 1;
      changeTimer: number | undefined;
      disposed = false;
      // The shared session for this buffer's (interpreter, project) — acquired async once
      // the Python environment resolves (see `connect`). Null until then, or for a buffer
      // torn down before it resolved. Transport/capabilities are read through it.
      session: LspSession | null = null;
      unregisterClient: () => void;
      // Version-keyed caches so re-opening the same dropdown / re-hovering the same spot
      // doesn't re-hit the server. All cleared on every `didChange` (see `clearCaches`),
      // the correctness boundary — results only describe the document at `this.version`.
      // `resolveCache` is keyed by the item object (unique per request), so it's scoped
      // to one dropdown.
      completionCache = new Map<string, LspCompletionItem[]>();
      resolveCache = new Map<LspCompletionItem, LspCompletionItem>();
      hoverCache = new Map<string, string>();

      constructor(readonly view: EditorView) {
        ref.plugin = this;
        // Expose this buffer's live client to the agent tools by its source URI.
        this.unregisterClient = registerLspClient(opts.bufferUri, this);
        void this.connect();
      }

      /** Resolve the environment (Python), then join — or start — the shared session for
       * this interpreter+project and open this document on it. */
      async connect(): Promise<void> {
        let root = opts.root;
        let interpreter: string | undefined;
        if (opts.languageId === 'python') {
          const resolved = await fetchPythonEnv(dirOf(opts.path));
          env.packages = resolved.packages;
          root = resolved.root || opts.root;
          // The setting override wins over the auto-detected interpreter; both key the
          // pool, so buffers only share a server when they'd analyze identically.
          interpreter = opts.pythonPathOverride || resolved.interpreter || undefined;
        }
        if (this.disposed) return; // torn down while the env fetch was in flight
        const session = acquireSession(
          `${opts.languageId}::${root}::${interpreter ?? ''}`,
          opts.languageId,
          root,
          interpreter,
        );
        this.session = session;
        session.openDocument(uri, opts.languageId, () => this.view.state.doc.toString(), {
          onDiagnostics: (raw) => this.applyDiagnostics(raw),
        });
        // The moment the server is usable, re-open the completion list if the user is
        // sitting in this buffer mid-word. Waiting on `warmupMs` covers a request made
        // *during* the cold start; this covers the popup the user already dismissed —
        // together they close the dead window where typing produced nothing.
        session.onReady(() => {
          if (this.disposed || !this.view.hasFocus) return;
          const head = this.view.state.selection.main.head;
          const word = this.view.state.wordAt(head);
          if (word && head > word.from) startCompletion(this.view);
        });
      }

      // Session-backed state the statically-configured extensions read through `ref`.
      get initialized(): boolean {
        return this.session?.ready() ?? false;
      }
      get dead(): boolean {
        return this.session?.dead ?? false;
      }
      get triggerChars(): string[] {
        return this.session?.triggerChars ?? [];
      }
      get resolveProvider(): boolean {
        return this.session?.resolveProvider ?? false;
      }

      ready(): boolean {
        return this.session?.ready() ?? false;
      }

      /** Wait out the cold start (bounded). The session itself may not exist yet — the
       * Python environment fetch precedes it — so poll briefly for it first. */
      async waitReady(timeoutMs: number): Promise<boolean> {
        const deadline = Date.now() + timeoutMs;
        while (!this.session && !this.disposed && Date.now() < deadline) {
          await new Promise((r) => window.setTimeout(r, 50));
        }
        if (!this.session || this.disposed) return false;
        return this.session.waitReady(Math.max(0, deadline - Date.now()));
      }

      flushChanges(): void {
        if (this.changeTimer !== undefined) {
          window.clearTimeout(this.changeTimer);
          this.changeTimer = undefined;
          this.sendChange();
        }
      }

      /** A JSON-RPC request over the shared session, scoped to this document by `uri`. */
      request(method: string, params: Record<string, unknown>, timeoutMs = 4000): Promise<unknown> {
        this.flushChanges();
        return this.session
          ? this.session.request(method, params, timeoutMs)
          : Promise.reject(new Error('lsp session not connected'));
      }

      /** Render the server's diagnostics for this document into the linter and stash the
       * flattened form for the agent's read tools. */
      applyDiagnostics(raw: LspDiagnostic[]): void {
        // The agent-facing record is kept even with the gutter turned off —
        // `editor.diagnostics` is about what the *editor* renders, and
        // `editor.getDiagnostics` should still see the server's findings.
        recordDiagnostics(opts.bufferUri, raw.map(toAgentDiagnostic));
        if (opts.diagnostics === false) return;
        const diags = raw.map((d) => toCmDiagnostic(this.view, d)).sort((a, b) => a.from - b.from);
        this.view.dispatch(setDiagnostics(this.view.state, diags));
      }

      /** Resolve an `LspTarget` (explicit 1-based line/column, a symbol's first
       * occurrence, or the cursor) to an LSP position, or null if unresolvable. */
      resolveTarget(target: LspTarget): LspPosition | null {
        const doc = this.view.state.doc;
        if (typeof target.line === 'number') {
          const lineNo = Math.min(Math.max(Math.trunc(target.line), 1), doc.lines);
          const character =
            typeof target.column === 'number' ? Math.max(Math.trunc(target.column) - 1, 0) : 0;
          return { line: lineNo - 1, character };
        }
        if (target.symbol) {
          const idx = doc.toString().indexOf(target.symbol);
          return idx < 0 ? null : positionAt(doc, idx);
        }
        return positionAt(doc, this.view.state.selection.main.head);
      }

      async rename(target: LspTarget, newName: string): Promise<RenameOutcome> {
        this.flushChanges();
        if (!this.ready()) return { ok: false, error: 'language server not ready' };
        const position = this.resolveTarget(target);
        if (!position) return { ok: false, error: 'could not locate the symbol to rename' };
        let result: unknown;
        try {
          result = await this.request(
            'textDocument/rename',
            { textDocument: { uri }, position, newName },
            10000,
          );
        } catch {
          return { ok: false, error: 'rename request failed or timed out' };
        }
        if (!result || typeof result !== 'object') {
          return { ok: false, error: 'the symbol at that position cannot be renamed' };
        }
        return this.applyWorkspaceEdit(result as WorkspaceEdit, newName);
      }

      /** Apply a rename's WorkspaceEdit: the live buffer is edited in place; other
       * open buffers update through their controller; closed files are loaded,
       * edited, and saved. Returns a per-file summary. */
      async applyWorkspaceEdit(edit: WorkspaceEdit, newName: string): Promise<RenameOutcome> {
        const byUri = workspaceEditChanges(edit);
        if (!byUri.size) return { ok: false, error: 'rename produced no edits' };
        const changes: NonNullable<RenameOutcome['changes']> = [];
        for (const [docUri, edits] of byUri) {
          const path = uriToPath(docUri, sep);
          if (sameUri(docUri, uri, sep)) {
            this.view.dispatch({
              changes: edits.map((e) => ({
                from: offsetAt(this.view, e.range.start),
                to: offsetAt(this.view, e.range.end),
                insert: e.newText,
              })),
            });
            changes.push({ path, edits: edits.length, open: true });
            continue;
          }
          const sourceUri = `workspace-file:${path}`;
          const buffer = getBuffer(sourceUri);
          if (buffer) {
            buffer.setContent(applyEditsToString(buffer.snapshot().content, edits));
            changes.push({ path, edits: edits.length, open: true });
            continue;
          }
          try {
            const loaded = await loadSource(sourceUri);
            await saveSource(sourceUri, applyEditsToString(loaded.content, edits));
            changes.push({ path, edits: edits.length, open: false });
          } catch (e) {
            return { ok: false, error: `failed to apply rename to ${path}: ${String(e)}`, changes };
          }
        }
        return { ok: true, newName, changes };
      }

      async references(
        target: LspTarget,
      ): Promise<{ ok: boolean; error?: string; references?: AgentReference[] }> {
        this.flushChanges();
        if (!this.ready()) return { ok: false, error: 'language server not ready' };
        const position = this.resolveTarget(target);
        if (!position) return { ok: false, error: 'could not locate the symbol' };
        let result: unknown;
        try {
          result = await this.request(
            'textDocument/references',
            { textDocument: { uri }, position, context: { includeDeclaration: true } },
            10000,
          );
        } catch {
          return { ok: false, error: 'references request failed or timed out' };
        }
        const locs = (Array.isArray(result) ? result : []) as LspLocation[];
        const references: AgentReference[] = locs.map((l) => ({
          path: uriToPath(l.uri, sep),
          line: l.range.start.line + 1,
          column: l.range.start.character + 1,
          endLine: l.range.end.line + 1,
          endColumn: l.range.end.character + 1,
        }));
        return { ok: true, references };
      }

      /** Completion candidates + hover at an offset, to ground the ghost-text
       * prompt (the LLM picks from symbols the server says are in scope). */
      async grounding(offset: number): Promise<LspGrounding | null> {
        this.flushChanges();
        if (!this.ready()) return null;
        const position = positionAt(this.view.state.doc, offset);
        const [comp, hov] = await Promise.allSettled([
          this.request('textDocument/completion', { textDocument: { uri }, position }),
          this.request('textDocument/hover', { textDocument: { uri }, position }),
        ]);
        const completions: string[] = [];
        if (comp.status === 'fulfilled') {
          const r = comp.value;
          const items = (
            Array.isArray(r) ? r : ((r as { items?: LspCompletionItem[] })?.items ?? [])
          ) as LspCompletionItem[];
          for (const it of items.slice(0, 20)) completions.push(it.label);
        }
        const hover = hov.status === 'fulfilled' ? hoverText(hov.value) : '';
        return completions.length || hover ? { completions, hover } : null;
      }

      async complete(context: CompletionContext): Promise<CompletionResult | null> {
        const word = context.matchBefore(/[\w$]+/);
        const dot = context.matchBefore(/\.[\w$]*$/);
        if (!context.explicit && !dot && !word) return null;
        const doc = this.view.state.doc;
        const line = doc.lineAt(context.pos);
        const position = { line: line.number - 1, character: context.pos - line.from };
        let completionContext: { triggerKind: number; triggerCharacter?: string } | undefined =
          undefined;
        if (context.explicit) {
          completionContext = { triggerKind: 1 };
        } else {
          const charBefore = doc.sliceString(context.pos - 1, context.pos);
          if (this.triggerChars.includes(charBefore)) {
            completionContext = { triggerKind: 2, triggerCharacter: charBefore };
          } else {
            completionContext = { triggerKind: 1 };
          }
        }
        const items = await this.completionItems(position, completionContext);
        if (!items.length) return null;
        const options: Completion[] = items.slice(0, 200).map((it) => {
          // Shared metadata; the doc pane is resolved lazily when the item is focused.
          const meta = {
            label: it.label,
            type: it.kind ? COMPLETION_KIND[it.kind] : undefined,
            detail: it.detail,
            info: () => this.completionInfo(it),
          };
          const newText = completionInsertText(it);
          // When the server specifies the range its `newText` replaces, honor that
          // start (mapped to an offset at apply time) — it can reach before the typed
          // word, so a qualified auto-import (`os.O_ASYNC` over `os.`) replaces `os.`
          // instead of doubling it. The end stays live (CodeMirror's `to`) so anything
          // typed after the list opened is still replaced. Bare inserts (no textEdit)
          // keep the simple string/snippet apply anchored at the completion `from`.
          const editStart = textEditStart(it.textEdit);
          if (it.insertTextFormat === 2) {
            const snip = snippetCompletion(lspSnippetToCm(newText), meta);
            if (!editStart) return snip;
            const expand = snip.apply as (
              v: EditorView,
              c: Completion,
              f: number,
              t: number,
            ) => void;
            return {
              ...meta,
              apply: (view: EditorView, c: Completion, _from: number, to: number) =>
                expand(view, c, offsetAt(view, editStart), to),
            };
          }
          if (!editStart) return { ...meta, apply: newText };
          return {
            ...meta,
            apply: (view: EditorView, _c: Completion, _from: number, to: number) => {
              const from = offsetAt(view, editStart);
              view.dispatch({
                changes: { from, to, insert: newText },
                selection: { anchor: from + newText.length },
                annotations: pickedCompletion.of({ label: it.label }),
              });
            },
          };
        });
        return { from: word ? word.from : context.pos, options, validFor: /^[\w$]*$/ };
      }

      /** Completion items at a position, cached for the current document version so a
       * re-opened dropdown at the same spot doesn't re-hit the server. */
      async completionItems(
        position: LspPosition,
        completionContext?: { triggerKind: number; triggerCharacter?: string },
      ): Promise<LspCompletionItem[]> {
        this.flushChanges();
        const key = `${this.version}:${position.line}:${position.character}:${completionContext?.triggerCharacter ?? ''}`;
        const cached = this.completionCache.get(key);
        if (cached) return cached;
        let result: unknown;
        try {
          result = await this.request('textDocument/completion', {
            textDocument: { uri },
            position,
            context: completionContext,
          });
        } catch {
          return []; // timed out / errored — no completions
        }
        const items = (
          Array.isArray(result)
            ? result
            : ((result as { items?: LspCompletionItem[] })?.items ?? [])
        ) as LspCompletionItem[];
        this.completionCache.set(key, items);
        return items;
      }

      /** Enrich an item with its documentation via `completionItem/resolve` (cached
       * per item). Falls back to the unresolved item if the server can't resolve. */
      async resolveItem(it: LspCompletionItem): Promise<LspCompletionItem> {
        const cached = this.resolveCache.get(it);
        if (cached) return cached;
        let resolved = it;
        try {
          const result = await this.request(
            'completionItem/resolve',
            it as unknown as Record<string, unknown>,
          );
          if (result && typeof result === 'object') resolved = result as LspCompletionItem;
        } catch {
          // resolve failed/timed out — show what the item already carries.
        }
        this.resolveCache.set(it, resolved);
        return resolved;
      }

      /** The completion doc pane: the item's `detail` signature plus its (markdown)
       * documentation, resolved on demand. Null when there's nothing to show. */
      async completionInfo(it: LspCompletionItem): Promise<Node | null> {
        const resolved = this.resolveProvider ? await this.resolveItem(it) : it;
        const body = markupText(resolved.documentation);
        const detail = resolved.detail;
        if (!body && !detail) return null;
        const dom = document.createElement('div');
        dom.className = 'cm-lsp-doc';
        if (detail) {
          const sig = document.createElement('div');
          sig.className = 'cm-lsp-doc-detail';
          sig.textContent = detail;
          dom.appendChild(sig);
        }
        if (body) dom.appendChild(renderMarkdown(body));
        return dom;
      }

      async hover(pos: number): Promise<Tooltip | null> {
        this.flushChanges();
        const key = `${this.version}:${pos}`;
        let text = this.hoverCache.get(key);
        if (text === undefined) {
          let result: unknown;
          try {
            result = await this.request('textDocument/hover', {
              textDocument: { uri },
              position: positionAt(this.view.state.doc, pos),
            });
          } catch {
            return null;
          }
          text = hoverText(result);
          this.hoverCache.set(key, text);
        }
        if (!text) return null;
        const body = text;
        return {
          pos,
          above: true,
          create: () => {
            const dom = document.createElement('div');
            dom.className = 'cm-lsp-hover';
            dom.appendChild(renderMarkdown(body));
            return { dom };
          },
        };
      }

      async definition(pos: number): Promise<void> {
        this.flushChanges();
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

      update(u: ViewUpdate): void {
        if (!u.docChanged || !this.ready()) return;
        if (this.changeTimer !== undefined) window.clearTimeout(this.changeTimer);
        this.changeTimer = window.setTimeout(() => this.sendChange(), opts.changeDebounceMs ?? 300);
      }

      sendChange(): void {
        if (!this.session?.ready()) return;
        // The document moved on — every cached result described the old version.
        this.clearCaches();
        this.session.didChange(uri, ++this.version, this.view.state.doc.toString());
      }

      clearCaches(): void {
        this.completionCache.clear();
        this.resolveCache.clear();
        this.hoverCache.clear();
      }

      destroy(): void {
        this.disposed = true;
        if (this.changeTimer !== undefined) window.clearTimeout(this.changeTimer);
        if (this.session) {
          // Close this document; the session stops the server when its last doc closes.
          this.session.closeDocument(uri);
          releaseSession(this.session);
        }
        this.unregisterClient();
        ref.plugin = null;
      }
    },
  );

  const warmupMs = opts.warmupMs ?? 2000;

  // LSP is the authoritative completion source for code buffers. If the server is
  // still coming up, wait for it (bounded by `warmupMs`) rather than returning empty
  // — the instant sources below already resolved, so this only delays the *full*
  // result on a cold buffer, and only until the server answers.
  const completionSource = async (context: CompletionContext): Promise<CompletionResult | null> => {
    const p = ref.plugin;
    if (!p || p.dead) return null;
    if (!p.initialized && !(await p.waitReady(warmupMs))) return null;
    if (context.aborted) return null;
    return p.complete(context);
  };

  /**
   * Hover documentation: the language server first, then the rest of the chain.
   *
   * The server is authoritative for a project's own code and knows nothing about
   * a package that isn't installed in the interpreter it was started with — which
   * is most of what people hover. Falling through to the offline symbol index and
   * (if enabled) the web turns "no tooltip at all" into an answer, and the popup
   * says which source produced it.
   *
   * `lsp` is dropped from the fallback order because it is *this* branch; letting
   * the chain re-enter it would ask the same server the same question twice.
   */
  const hover = hoverTooltip(async (view, pos) => {
    if (opts.hover === false) return null;
    if (ref.plugin?.initialized) {
      const tip = await ref.plugin.hover(pos);
      if (tip) return tip;
    }
    const code = view.state.doc.toString();
    const { text: symbol, from, to } = symbolAt(code, pos);
    if (!symbol) return null;
    const sources = enabledDocSources().filter((s) => s !== 'lsp');
    if (!sources.length) return null;
    const { entries } = await lookupDocs({ symbol, code, cursorPos: pos, sources });
    const entry = entries[0];
    if (!entry) return null;
    return { pos: from, end: to, above: true, create: () => ({ dom: renderDocEntry(entry) }) };
  });

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

  // F2 renames the symbol under the cursor (the same LSP rename the agent drives).
  const renameSymbol = keymap.of([
    {
      key: 'F2',
      run: (view) => {
        if (!ref.plugin?.initialized) return false;
        const word = view.state.wordAt(view.state.selection.main.head);
        const current = word ? view.state.sliceDoc(word.from, word.to) : '';
        // The dialog is async; the keymap handler resolves synchronously and the
        // rename fires once the user submits.
        void dialogs
          .prompt({
            title: 'Rename symbol',
            defaultValue: current,
            confirmLabel: 'Rename',
          })
          .then((value) => {
            const next = value?.trim();
            if (next && next !== current) void ref.plugin?.rename({}, next);
          });
        return true;
      },
    },
  ]);

  // Open the completion list the instant a server-declared trigger character is typed
  // (e.g. `.` for members), matching VS Code — CodeMirror otherwise only re-queries on
  // word characters. Only on real typing, and only for the trigger set the server
  // advertised at initialize.
  const triggerCompletion = EditorView.updateListener.of((update) => {
    const p = ref.plugin;
    if (!p?.initialized || p.dead || !p.triggerChars.length || !update.docChanged) return;
    if (!update.transactions.some((tr) => tr.isUserEvent('input.type'))) return;
    let last = '';
    update.changes.iterChanges((_fromA, _toA, _fromB, _toB, inserted) => {
      const s = inserted.toString();
      if (s) last = s[s.length - 1];
    });
    if (last && p.triggerChars.includes(last)) startCompletion(update.view);
  });

  // The DB symbol index (prefix lookup, no model) is merged into the same popup as
  // an instant identifier source — it fills in while the LSP warms up and covers
  // symbols the server doesn't surface. Ranked below the LSP's type-aware results.
  const dbSource = dbSymbolSource(() => opts.languageId);
  // Python buffers also get the curated framework-import source (basedpyright can't
  // auto-import installed libraries) — merged into the same popup, ranked below LSP.
  const useFrameworkImports = opts.languageId === 'python' && opts.frameworkImports !== false;
  const fallbackSources: CompletionSource[] = [
    ...(opts.indexedSymbols === false ? [] : [dbSource]),
    ...(useFrameworkImports ? [frameworkImportSource(() => env.packages)] : []),
  ];

  // One merged source rather than three parallel ones, so the offline sources can be
  // **deduped against the server's own results**. basedpyright ships typeshed, so once
  // it's warm it already offers `defaultdict`, `Path`, … as auto-imports — listing the
  // indexed copy beside it showed every stdlib symbol twice. The server wins any label
  // it provides (it's type-aware and scope-aware); the indexed sources only fill in
  // what it didn't offer, which is what makes them useful during the cold start and
  // for third-party packages it can't auto-import.
  const mergedSource = async (context: CompletionContext): Promise<CompletionResult | null> => {
    const [lsp, ...rest] = await Promise.all([
      completionSource(context),
      ...fallbackSources.map((src) => src(context)),
    ]);
    const extras = rest.filter((r): r is CompletionResult => r != null);
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
    // Every source anchors on the same typed word, so any non-null `from` agrees.
    return {
      from: lsp?.from ?? extras[0].from,
      options,
      validFor: /^[A-Za-z_]\w*$/,
    };
  };

  return [
    ...(opts.diagnostics === false ? [] : [lintGutter()]),
    plugin,
    autocompletion({ override: [mergedSource] }),
    hover,
    gotoDefinition,
    renameSymbol,
    triggerCompletion,
  ];
}
