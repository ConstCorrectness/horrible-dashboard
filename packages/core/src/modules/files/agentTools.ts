/**
 * Agent tools the file explorer exposes (declared on the `files.tree` panel, so
 * they reach the capability manifest). Read tools are ungated; the mutating ones
 * carry `sideEffect` + a `{path}` specifier so the backend permission engine gates
 * them (filesystem rules; `files.create`/`files.write` are edit-safe under
 * `acceptEdits`, delete/rename keep prompting). See docs/architecture/agent-tools.md.
 */
import type { AgentToolDecl } from '../../registry';
import {
  createEntry,
  deleteEntry,
  gitStatus,
  listDir,
  readFile,
  renameEntry,
  writeFile,
} from './api';
import { refreshTree } from './store';

const PATH_DESC =
  'Path within a workspace root — absolute, or relative to a root (e.g. "notes.txt" or "src/app.py"); a leading segment matching a root name selects that root.';

const pathParam = {
  type: 'object' as const,
  properties: {
    path: { type: 'string' as const, description: PATH_DESC },
  },
  required: ['path'],
};

export const filesAgentTools: AgentToolDecl[] = [
  {
    name: 'files.list',
    description: 'List the entries of a directory under a workspace root.',
    params: pathParam,
    sideEffect: false,
    handler: (args) => listDir(String(args.path)),
  },
  {
    name: 'files.read',
    description: 'Read the UTF-8 contents of a file under a workspace root.',
    params: pathParam,
    sideEffect: false,
    handler: (args) => readFile(String(args.path)),
  },
  {
    name: 'files.gitStatus',
    description:
      "List the git working-tree changes for a workspace root (the branch and each changed path's status). Returns is_repo:false if the root isn't a git repo.",
    params: pathParam,
    sideEffect: false,
    handler: (args) => gitStatus(String(args.path)),
  },
  {
    name: 'files.create',
    description: 'Create a new file or directory under a workspace root.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: PATH_DESC },
        kind: { type: 'string', enum: ['file', 'dir'], description: 'Defaults to file' },
        content: { type: 'string', description: 'Initial content for a file' },
      },
      required: ['path'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: async (args) => {
      const kind = args.kind === 'dir' ? 'dir' : 'file';
      const entry = await createEntry(String(args.path), kind, String(args.content ?? ''));
      refreshTree();
      return entry;
    },
  },
  {
    name: 'files.write',
    description: 'Overwrite (or create) a file with the given content.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: PATH_DESC },
        content: { type: 'string', description: 'New file content' },
      },
      required: ['path', 'content'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: async (args) => {
      const entry = await writeFile(String(args.path), String(args.content ?? ''));
      refreshTree();
      return entry;
    },
  },
  {
    name: 'files.rename',
    description: 'Rename or move a file/directory within the workspace roots.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Existing absolute path' },
        new_path: { type: 'string', description: 'New absolute path' },
      },
      required: ['path', 'new_path'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: async (args) => {
      const entry = await renameEntry(String(args.path), String(args.new_path));
      refreshTree();
      return entry;
    },
  },
  {
    name: 'files.delete',
    description: 'Delete a file or directory (recursive for non-empty directories).',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Absolute path to delete' },
        recursive: { type: 'boolean', description: 'Required for non-empty directories' },
      },
      required: ['path'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: async (args) => {
      const result = await deleteEntry(String(args.path), Boolean(args.recursive));
      refreshTree();
      return result;
    },
  },
];
