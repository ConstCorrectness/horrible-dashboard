/**
 * The workbench's agent tools.
 *
 * Every name is `ide.*` and that is not decoration: the orchestrator groups tools
 * by **name prefix**, so a tool named anything else here would land in another
 * group or none.
 *
 * What is deliberately absent matters as much as what is here. There is no
 * commit tool (`git.commit` exists and stamps the session provenance trailers the
 * blame view reads — a second one would lose them), no terminal tool
 * (`terminal.exec`), no file-write tool (`files.write`), no symbol tool
 * (`symbols.*`). This group is the *workbench*, not a re-export of everything
 * reachable from it.
 */
import type { AgentToolDecl } from '../../registry';
import { setLocus } from '../../locus';
import { isSourceDirty } from '../editor/unsaved';
import { listDiagnostics } from '../editor/lsp-registry';
import { fetchScmStatus, fetchWorkingDiff, searchFiles, stagePaths, unstagePaths } from './api';
import { closeTab, readTabs } from './openBuffers';

// `../editor` and `./IdeWorkbench` are imported *inside* the handlers rather than
// at the top. The editor's manifest opens a WebSocket at module scope, so a
// top-level import makes this file unloadable outside a browser — and these
// declarations are exactly the kind of thing that should be unit-tested. The
// handlers only ever run in the app, where the dynamic import is already warm.
const editor = () => import('../editor');
const workbench = () => import('./IdeWorkbench');

const FILE_URI = 'workspace-file:';

/** The open workbench, or a failure the model can act on. */
async function host(): Promise<{ ok: true; id: string } | { ok: false; error: string }> {
  const id = (await workbench()).workbenchInstanceId();
  return id ? { ok: true, id } : { ok: false, error: 'no workbench is open — run ide.open first' };
}

export const ideAgentTools: AgentToolDecl[] = [
  {
    name: 'ide.open',
    description:
      'Open the IDE workbench (the editor with its explorer, search, source-control and terminal strips), optionally on a file. Use before the other ide.* tools, which need it open.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Optional file to open (workspace path).' },
      },
    },
    sideEffect: true,
    handler: async (args) => {
      const { openInWorkbench } = await import('./index');
      const path = args.path ? String(args.path) : undefined;
      openInWorkbench(path ? `${FILE_URI}${path}` : undefined);
      return { ok: true };
    },
  },
  {
    name: 'ide.openFile',
    description:
      'Open a file in the workbench and scroll it to a line. Navigation only — nothing is changed, so this is safe to use freely while reading code.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Workspace path (absolute or root-relative).' },
        line: { type: 'number', description: '1-based line to reveal.' },
        column: { type: 'number', description: '1-based column (default 1).' },
      },
      required: ['path'],
    },
    sideEffect: false,
    handler: async (args) => {
      const path = String(args.path ?? '');
      if (!path) return { ok: false, error: 'path is required' };
      (await editor()).openBuffer(`${FILE_URI}${path}`);
      if (typeof args.line === 'number') {
        const column = typeof args.column === 'number' ? args.column : 1;
        const at = { line: args.line, column };
        setLocus({ path, range: { start: at, end: at } }, 'ide.agent');
      }
      return { ok: true, path };
    },
  },
  {
    name: 'ide.listOpenFiles',
    description:
      'List the files open as tabs in the workbench, which is showing, and which have unsaved changes. Read-only.',
    params: { type: 'object', properties: {} },
    sideEffect: false,
    handler: async () => {
      const found = await host();
      if (!found.ok) return found;
      const { sourceTitle } = await editor();
      const { tabs, active } = readTabs(found.id);
      return {
        ok: true,
        active,
        files: tabs.map((uri) => ({
          uri,
          title: sourceTitle(uri),
          dirty: isSourceDirty(uri),
        })),
      };
    },
  },
  {
    name: 'ide.closeFile',
    description:
      'Close a file’s tab in the workbench. Gated because it can discard unsaved changes — pass save:true to write them first.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Workspace path of the open file.' },
        save: { type: 'boolean', description: 'Save before closing (default false).' },
      },
      required: ['path'],
    },
    sideEffect: true,
    specifierTemplate: '{path}',
    handler: async (args) => {
      const found = await host();
      if (!found.ok) return found;
      const uri = `${FILE_URI}${String(args.path ?? '')}`;
      if (!readTabs(found.id).tabs.includes(uri)) return { ok: false, error: 'not open' };
      if (args.save === true) {
        const { getBuffer } = await import('../editor/buffers');
        await getBuffer(uri)?.save();
      } else if (isSourceDirty(uri)) {
        return { ok: false, error: 'file has unsaved changes; pass save:true to write them' };
      }
      closeTab(found.id, uri);
      return { ok: true };
    },
  },
  {
    name: 'ide.findInFiles',
    description:
      'Search the text of every file under the workspace roots (the IDE Search panel). Read-only. Use this for "where does this string appear"; use symbols.find for "where is this defined".',
    params: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Text or regular expression to find.' },
        regex: { type: 'boolean', description: 'Treat the query as a regex (default false).' },
        caseSensitive: { type: 'boolean', description: 'Default false.' },
        wholeWord: { type: 'boolean', description: 'Default false.' },
        include: {
          type: 'array',
          items: { type: 'string' },
          description: 'Globs to search, e.g. ["*.ts"].',
        },
        exclude: { type: 'array', items: { type: 'string' }, description: 'Globs to skip.' },
        maxResults: { type: 'number', description: 'Cap on matches (default 200 here).' },
      },
      required: ['query'],
    },
    sideEffect: false,
    handler: async (args) => {
      const query = String(args.query ?? '');
      if (!query) return { ok: false, error: 'query is required' };
      const result = await searchFiles({
        query,
        regex: args.regex === true,
        caseSensitive: args.caseSensitive === true,
        wholeWord: args.wholeWord === true,
        include: Array.isArray(args.include) ? args.include.map(String) : [],
        exclude: Array.isArray(args.exclude) ? args.exclude.map(String) : [],
        // A tool call has no scrollbar, so its default cap is far below the
        // panel's 500.
        maxResults: typeof args.maxResults === 'number' ? args.maxResults : 200,
      });
      return { ok: !result.error, ...result };
    },
  },
  {
    name: 'ide.problems',
    description:
      'Language-server diagnostics for the files open in the workbench. Read-only. Note the scope: this is not a project-wide check — a file that has never been opened contributes nothing.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Only this file.' },
        severity: {
          type: 'string',
          description: 'Filter: error | warning | info | hint.',
        },
      },
    },
    sideEffect: false,
    handler: async (args) => {
      const path = args.path ? String(args.path) : null;
      const severity = args.severity ? String(args.severity) : null;
      const files = listDiagnostics()
        .filter((entry) => !path || entry.uri === `${FILE_URI}${path}`)
        .map((entry) => ({
          uri: entry.uri,
          diagnostics: entry.diagnostics.filter((d) => !severity || d.severity === severity),
        }))
        .filter((entry) => entry.diagnostics.length > 0);
      return {
        ok: true,
        // Stated in the response, not just the description: a model that reports
        // "no problems" should be able to say what was actually checked.
        scope: 'files opened in this session that have a language server; not a project-wide check',
        files,
      };
    },
  },
  {
    name: 'ide.scmStatus',
    description:
      'The git working tree grouped as source control sees it: branch, ahead/behind, and the staged, unstaged, untracked and conflicted files. Read-only.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'A workspace path to locate the repo.' },
      },
    },
    sideEffect: false,
    handler: async (args) => {
      const status = await fetchScmStatus(args.path ? String(args.path) : undefined);
      return { ok: true, ...status };
    },
  },
  {
    name: 'ide.diff',
    description:
      'The unified diff of a file’s uncommitted changes. Read-only. Pass staged:true for what is already in the index rather than what is not.',
    params: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Workspace path.' },
        staged: { type: 'boolean', description: 'Diff the index instead (default false).' },
      },
      required: ['path'],
    },
    sideEffect: false,
    handler: async (args) => {
      const path = String(args.path ?? '');
      if (!path) return { ok: false, error: 'path is required' };
      const res = await fetchWorkingDiff(path, args.staged === true);
      return { ok: true, diff: res.diff };
    },
  },
  {
    name: 'ide.stage',
    description: 'Stage files for the next commit (git add). Commit them with git.commit.',
    params: {
      type: 'object',
      properties: {
        paths: { type: 'array', items: { type: 'string' }, description: 'Workspace paths.' },
      },
      required: ['paths'],
    },
    sideEffect: true,
    specifierTemplate: '{paths}',
    handler: async (args) => {
      const paths = Array.isArray(args.paths) ? args.paths.map(String) : [];
      if (paths.length === 0) return { ok: false, error: 'paths is required' };
      return stagePaths(paths);
    },
  },
  {
    name: 'ide.unstage',
    description:
      'Remove files from the index without touching their contents (git restore --staged).',
    params: {
      type: 'object',
      properties: {
        paths: { type: 'array', items: { type: 'string' }, description: 'Workspace paths.' },
      },
      required: ['paths'],
    },
    sideEffect: true,
    specifierTemplate: '{paths}',
    handler: async (args) => {
      const paths = Array.isArray(args.paths) ? args.paths.map(String) : [];
      if (paths.length === 0) return { ok: false, error: 'paths is required' };
      return unstagePaths(paths);
    },
  },
];
