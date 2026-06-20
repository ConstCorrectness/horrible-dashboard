/**
 * Per-buffer LSP state the editor's agent tools read without reaching into
 * CodeMirror: the latest diagnostics published for a buffer, and a thin client
 * handle the agent's rename / find-references / ghost-text-grounding tools call.
 *
 * Both are keyed by the buffer's **source URI** (`workspace-file:<path>`) — the
 * same key the buffer-controller registry (buffers.ts) and the agent edit tools
 * already use — so a tool targets a buffer by the URI it knows. lsp.ts registers
 * and updates these for a live server session and clears them when the buffer
 * closes. Kept out of lsp.ts so agentTools.ts can read LSP state without pulling
 * in CodeMirror. See docs/modules/editor.md.
 */

/** A diagnostic flattened for the agent: 1-based line/column, plain severity. */
export interface AgentDiagnostic {
  severity: 'error' | 'warning' | 'info' | 'hint';
  message: string;
  source?: string;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
}

/** A symbol location find-references reports (1-based, like the diagnostics). */
export interface AgentReference {
  path: string;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
}

/** Outcome of an LSP rename: the files touched and how many edits each got. */
export interface RenameOutcome {
  ok: boolean;
  error?: string;
  newName?: string;
  changes?: { path: string; edits: number; open: boolean }[];
}

/** The extra context LSP feeds into the ghost-text completion prompt. */
export interface LspGrounding {
  /** Completion-item labels available at the cursor (the symbols in scope). */
  completions: string[];
  /** Hover text at the cursor (the expected type / signature), if any. */
  hover: string;
}

/**
 * Where in a buffer an LSP request acts: an explicit 1-based line/column, the
 * first occurrence of a symbol name, or (when none is given) the current cursor.
 */
export interface LspTarget {
  line?: number;
  column?: number;
  symbol?: string;
}

/** The slice of a live LSP client the editor's agent tools drive by buffer URI. */
export interface LspBufferClient {
  ready(): boolean;
  rename(target: LspTarget, newName: string): Promise<RenameOutcome>;
  references(
    target: LspTarget,
  ): Promise<{ ok: boolean; error?: string; references?: AgentReference[] }>;
  /** Completion + hover context at a document offset for the ghost-text prompt. */
  grounding(offset: number): Promise<LspGrounding | null>;
}

const diagnosticsByUri = new Map<string, AgentDiagnostic[]>();
const clientsByUri = new Map<string, LspBufferClient>();

export function recordDiagnostics(uri: string, diags: AgentDiagnostic[]): void {
  diagnosticsByUri.set(uri, diags);
}

/** The diagnostics last published for a buffer (empty if none / no server). */
export function readDiagnostics(uri: string): AgentDiagnostic[] {
  return diagnosticsByUri.get(uri) ?? [];
}

/** Register a live LSP client for a buffer; the cleanup also clears its
 * diagnostics so a closed buffer leaves no stale state behind. */
export function registerLspClient(uri: string, client: LspBufferClient): () => void {
  clientsByUri.set(uri, client);
  return () => {
    if (clientsByUri.get(uri) === client) clientsByUri.delete(uri);
    diagnosticsByUri.delete(uri);
  };
}

export function getLspClient(uri: string): LspBufferClient | undefined {
  return clientsByUri.get(uri);
}
