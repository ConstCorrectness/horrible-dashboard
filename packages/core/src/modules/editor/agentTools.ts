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

function resolveUri(arg: unknown): string | null {
  if (typeof arg === 'string' && arg) return arg;
  return getActiveBufferSource() ?? listBufferUris()[0] ?? null;
}

export const editorAgentTools: AgentToolDecl[] = [
  {
    name: 'editor.applyEdit',
    description:
      'Replace the full content of an open editor buffer. Targets the given buffer URI, or the active buffer. Read the buffer first via its pane context.',
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
