/**
 * Git agent tools (declared on the `git.provenance` panel so they reach the capability
 * manifest). The read tools let the agent inspect authorship/history; `git.commit` is
 * the write wedge that makes provenance possible — it stamps the current conversation
 * so future blame can trace a line back to it. See docs/architecture/agent-tools.md.
 */
import type { AgentToolDecl } from '../../registry';
import { commitChanges, fetchBlame, fetchLog } from './api';

export const gitAgentTools: AgentToolDecl[] = [
  {
    name: 'git.blame',
    description:
      'Per-line git blame for a file — each line tagged with its author and, for commits this agent authored, the conversation (session) that wrote it. Read-only.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'File path (absolute or workspace-relative).' },
      },
      required: ['path'],
    },
    sideEffect: false,
    handler: async (args) => {
      const path = String(args.path ?? '');
      if (!path) return { ok: false, error: 'path is required' };
      return { ok: true, ...(await fetchBlame(path)) };
    },
  },
  {
    name: 'git.log',
    description:
      'Recent commits (sha, author, date, summary). Commits with a session id were authored by the agent. Read-only.',
    params: {
      type: 'object',
      properties: { limit: { type: 'number', description: 'Max commits (default 30).' } },
    },
    sideEffect: false,
    handler: async (args) => {
      const limit = typeof args.limit === 'number' ? args.limit : 30;
      return { ok: true, ...(await fetchLog(limit)) };
    },
  },
  {
    name: 'git.commit',
    description:
      'Stage and commit changes, stamping the current conversation as a provenance trailer so the change can later be traced back to this session (git blame → conversation). WRITES to the repository — only commit when the user has explicitly asked. Optionally pass `paths` to stage a subset (else everything is staged).',
    params: {
      type: 'object',
      properties: {
        message: { type: 'string', description: 'Commit message.' },
        paths: {
          type: 'array',
          items: { type: 'string' },
          description: 'Paths to stage (optional; default all changes).',
        },
      },
      required: ['message'],
    },
    sideEffect: true,
    handler: async (args) => {
      const message = String(args.message ?? '');
      if (!message) return { ok: false, error: 'message is required' };
      const paths = Array.isArray(args.paths) ? args.paths.map(String) : undefined;
      return await commitChanges(message, paths);
    },
  },
];
