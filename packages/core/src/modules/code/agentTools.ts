/**
 * Read-only agent tools over the tree-sitter index (declared on the `code.outline`
 * panel so they reach the capability manifest). The agent's structural view of the
 * codebase: locate a definition without grepping, or list a file's shape without
 * reading it whole. See docs/architecture/agent-tools.md.
 */
import type { AgentToolDecl } from '../../registry';
import { fetchDocumentSymbols, findSymbols, reindexCode, searchSemantic } from './api';

export const codeAgentTools: AgentToolDecl[] = [
  {
    name: 'symbols.outline',
    description:
      "List the definitions (functions, classes, methods, interfaces, types, enums) in a source file, via the app's tree-sitter index. Read-only. Give a workspace path (absolute or root-relative). Prefer this over reading a whole file to find where things are defined.",
    params: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Path to a source file (absolute or workspace-relative).',
        },
      },
      required: ['path'],
    },
    sideEffect: false,
    handler: async (args) => {
      const path = String(args.path ?? '');
      if (!path) return { ok: false, error: 'path is required' };
      return { ok: true, ...(await fetchDocumentSymbols(path)) };
    },
  },
  {
    name: 'symbols.find',
    description:
      'Fuzzy-search symbol definitions by name across the whole workspace (tree-sitter index; exact > prefix > substring > subsequence match). Read-only. Use this to locate where a function/class/type is defined without grepping the tree.',
    params: {
      type: 'object',
      properties: {
        q: { type: 'string', description: 'Symbol name or fragment to search for.' },
        limit: { type: 'number', description: 'Max hits (default 50).' },
      },
      required: ['q'],
    },
    sideEffect: false,
    handler: async (args) => {
      const q = String(args.q ?? '');
      if (!q) return { ok: false, error: 'q is required' };
      const limit = typeof args.limit === 'number' ? args.limit : 50;
      return { ok: true, ...(await findSymbols(q, limit)) };
    },
  },
  {
    name: 'code.search',
    description:
      'Semantic code search: find definitions by *meaning* across the workspace, ranked by embedding similarity to a natural-language description. Read-only. Each hit carries a path + line range (a locus). Use this when you know what code should DO but not what it is named; use symbols.find when you know the name. If it reports `building: true`, the index is warming up — retry shortly.',
    params: {
      type: 'object',
      properties: {
        q: { type: 'string', description: 'Natural-language description of the code to find.' },
        limit: { type: 'number', description: 'Max hits (default 20).' },
      },
      required: ['q'],
    },
    sideEffect: false,
    handler: async (args) => {
      const q = String(args.q ?? '');
      if (!q) return { ok: false, error: 'q is required' };
      const limit = typeof args.limit === 'number' ? args.limit : 20;
      return { ok: true, ...(await searchSemantic(q, limit)) };
    },
  },
  {
    name: 'code.reindex',
    description:
      'Rebuild the semantic code index (re-embed every definition). Kick this after large edits so code.search reflects them. Returns immediately; indexing runs in the background.',
    params: { type: 'object', properties: {} },
    sideEffect: false,
    handler: async () => ({ ok: true, ...(await reindexCode()) }),
  },
];
