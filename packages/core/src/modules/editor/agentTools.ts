/**
 * Agent tools the editor exposes (declared on the `editor.buffer` panel). The read
 * path is `getAgentContext` (active buffer snapshot); these are the **gated** write
 * tools, named to match the permission engine's edit-safe set (`editor.applyEdit`,
 * `editor.save` auto-allow under `acceptEdits`). They target a buffer by URI,
 * defaulting to the most recently focused one. See docs/architecture/agent-tools.md.
 */
import type { AgentToolDecl } from '../../registry';
import { getBuffer, listBufferUris } from './buffers';
import { getActiveBufferSource } from './index';
import { getLspClient, readDiagnostics, type LspTarget } from './lsp-registry';

function resolveUri(arg: unknown): string | null {
  if (typeof arg === 'string' && arg) return arg;
  // `getActiveBufferSource` is sticky and isn't reset when a buffer closes, so it
  // can point at a buffer that's no longer open (its controller is gone). Only trust
  // it when it's still a registered buffer; otherwise fall back to any open buffer —
  // an agent edit must land on a live buffer, not silently no-op on a dead URI.
  const active = getActiveBufferSource();
  if (active && getBuffer(active)) return active;
  return listBufferUris()[0] ?? null;
}

/** Build an `LspTarget` from a tool call's optional symbol / line / column args. */
function targetFrom(args: Record<string, unknown>): LspTarget {
  const target: LspTarget = {};
  if (typeof args.symbol === 'string' && args.symbol) target.symbol = args.symbol;
  if (typeof args.line === 'number') target.line = args.line;
  if (typeof args.column === 'number') target.column = args.column;
  return target;
}

/** Shared schema for the position args the rename / references tools take. */
const POSITION_PROPS = {
  uri: { type: 'string', description: 'Buffer source URI; defaults to the active buffer' },
  symbol: {
    type: 'string',
    description: 'Symbol name to locate (its first occurrence in the buffer is used)',
  },
  line: { type: 'number', description: '1-based line of the symbol (alternative to symbol)' },
  column: { type: 'number', description: '1-based column of the symbol' },
} as const;

export const editorAgentTools: AgentToolDecl[] = [
  {
    name: 'editor.proposeEdit',
    description:
      'Propose replacing the full content of an open editor buffer with new content the user reviews as a diff and accepts or declines. PREFER THIS over editor.applyEdit for any code change (format, rewrite, fix). Targets the given buffer URI, or the active buffer. Read the buffer first via its pane context.',
    params: {
      type: 'object',
      properties: {
        uri: { type: 'string', description: 'Buffer source URI (note:/workspace-file:)' },
        content: { type: 'string', description: 'The proposed new full content of the buffer' },
      },
      required: ['content'],
    },
    sideEffect: true,
    specifierTemplate: '{uri}',
    handler: (args) => {
      const uri = resolveUri(args.uri);
      const buffer = uri ? getBuffer(uri) : undefined;
      if (!uri || !buffer) return { ok: false, error: 'no open buffer for that uri' };
      buffer.propose(String(args.content ?? ''));
      return { ok: true, uri, status: 'awaiting user accept/decline' };
    },
  },
  {
    name: 'editor.applyEdit',
    description:
      'Replace the full content of an open editor buffer outright (no review). Prefer editor.proposeEdit for code changes. Targets the given buffer URI, or the active buffer. Read the buffer first via its pane context.',
    params: {
      type: 'object',
      properties: {
        uri: { type: 'string', description: 'Buffer source URI (note:/workspace-file:)' },
        content: { type: 'string', description: 'The new full content of the buffer' },
      },
      required: ['content'],
    },
    sideEffect: true,
    specifierTemplate: '{uri}',
    handler: (args) => {
      const uri = resolveUri(args.uri);
      const buffer = uri ? getBuffer(uri) : undefined;
      if (!uri || !buffer) return { ok: false, error: 'no open buffer for that uri' };
      buffer.setContent(String(args.content ?? ''));
      return { ok: true, uri };
    },
  },
  {
    name: 'editor.getDiagnostics',
    description:
      'List the language-server diagnostics (errors, warnings, hints) currently reported for an open code buffer. Read-only — call it after an edit to see problems you introduced and self-correct. Line/column are 1-based. Targets the given buffer URI, or the active buffer.',
    params: {
      type: 'object',
      properties: {
        uri: { type: 'string', description: 'Buffer source URI; defaults to the active buffer' },
      },
    },
    handler: (args) => {
      const uri = resolveUri(args.uri);
      if (!uri) return { ok: false, error: 'no open buffer' };
      return { ok: true, uri, diagnostics: readDiagnostics(uri) };
    },
  },
  {
    name: 'editor.findReferences',
    description:
      'Find every reference to a symbol in an open code buffer via the language server (correct, scope-aware cross-file results — not a text search). Read-only. Give a `symbol` name or an explicit 1-based `line`/`column`. Targets the given buffer URI, or the active buffer.',
    params: { type: 'object', properties: { ...POSITION_PROPS } },
    handler: async (args) => {
      const uri = resolveUri(args.uri);
      const client = uri ? getLspClient(uri) : undefined;
      if (!uri || !client) {
        return { ok: false, error: 'no language server for that buffer (open a code file)' };
      }
      return client.references(targetFrom(args));
    },
  },
  {
    name: 'editor.rename',
    description:
      'Rename a symbol across the workspace via the language server (textDocument/rename) — correct, scope-aware edits across every file, not a text replace. Applies the edits directly (gated like editor.applyEdit). Give a `symbol` name or an explicit 1-based `line`/`column`, plus the `newName`. Targets the given buffer URI, or the active buffer.',
    params: {
      type: 'object',
      properties: {
        ...POSITION_PROPS,
        newName: { type: 'string', description: 'The new name for the symbol' },
      },
      required: ['newName'],
    },
    sideEffect: true,
    specifierTemplate: '{uri}',
    handler: async (args) => {
      const uri = resolveUri(args.uri);
      const client = uri ? getLspClient(uri) : undefined;
      if (!uri || !client) {
        return { ok: false, error: 'no language server for that buffer (open a code file)' };
      }
      const newName = String(args.newName ?? '').trim();
      if (!newName) return { ok: false, error: 'newName is required' };
      return client.rename(targetFrom(args), newName);
    },
  },
  {
    name: 'editor.save',
    description: 'Save an open editor buffer to its backing note/file.',
    params: {
      type: 'object',
      properties: {
        uri: { type: 'string', description: 'Buffer source URI; defaults to the active buffer' },
      },
    },
    sideEffect: true,
    specifierTemplate: '{uri}',
    handler: async (args) => {
      const uri = resolveUri(args.uri);
      const buffer = uri ? getBuffer(uri) : undefined;
      if (!uri || !buffer) return { ok: false, error: 'no open buffer for that uri' };
      await buffer.save();
      return { ok: true, uri };
    },
  },
];
