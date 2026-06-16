/**
 * File explorer module: a tree over the workspace roots (panel `files.tree`) plus
 * CRUD commands. Opening a file routes through the editor's public `openBuffer`
 * service; "open terminal here" / desktop reveal are B5/B6. See
 * docs/modules/file-explorer.md.
 */
import { registry, type ModuleManifest } from '../../registry';
import { getActiveBufferSource, openBuffer } from '../editor';
import { openTerminal } from '../terminal';
import { filesAgentTools } from './agentTools';
import { createEntry, deleteEntry, joinPath, listRoots, parentDir, renameEntry } from './api';
import { FileTree } from './FileTree';
import { getSelection, refreshTree, setRevealTarget, setSelection } from './store';

const FILE_URI = 'workspace-file:';

/** The directory new entries should be created in: the selected dir, the selected
 * file's parent, or the first workspace root. */
async function targetDir(): Promise<string | null> {
  const sel = getSelection();
  if (sel) return sel.kind === 'dir' ? sel.path : parentDir(sel.path);
  const roots = await listRoots();
  return roots[0]?.path ?? null;
}

async function newFile(): Promise<void> {
  const dir = await targetDir();
  if (!dir) return;
  const name = window.prompt('New file name', 'untitled.md');
  if (!name) return;
  const path = joinPath(dir, name);
  await createEntry(path, 'file');
  refreshTree();
  openBuffer(`${FILE_URI}${path}`);
}

async function newFolder(): Promise<void> {
  const dir = await targetDir();
  if (!dir) return;
  const name = window.prompt('New folder name', 'folder');
  if (!name) return;
  await createEntry(joinPath(dir, name), 'dir');
  refreshTree();
}

async function renameSelected(): Promise<void> {
  const sel = getSelection();
  if (!sel) {
    window.alert('Select a file or folder to rename.');
    return;
  }
  const current = sel.path.split(/[\\/]/).pop() ?? sel.path;
  const name = window.prompt('Rename to', current);
  if (!name || name === current) return;
  await renameEntry(sel.path, joinPath(parentDir(sel.path), name));
  setSelection(null);
  refreshTree();
}

async function deleteSelected(): Promise<void> {
  const sel = getSelection();
  if (!sel) {
    window.alert('Select a file or folder to delete.');
    return;
  }
  if (!window.confirm(`Delete ${sel.path}?`)) return;
  await deleteEntry(sel.path, sel.kind === 'dir');
  setSelection(null);
  refreshTree();
}

/** Open a terminal rooted at the selected directory (or the first root). */
async function openTerminalHere(): Promise<void> {
  const cwd = await targetDir();
  if (cwd) openTerminal({ cwd });
}

/** Reveal the most recently focused workspace-file buffer in the tree. */
function revealActiveBuffer(): void {
  const source = getActiveBufferSource();
  if (!source || !source.startsWith(FILE_URI)) return;
  const path = source.slice(FILE_URI.length);
  registry.openPanel('files.tree');
  setRevealTarget(path);
  setSelection({ path, kind: 'file' });
}

export const filesModule: ModuleManifest = {
  id: 'files',
  title: 'Files',
  panels: [
    {
      id: 'files.tree',
      title: 'Files',
      component: FileTree,
      defaultPlacement: 'left',
      singleton: true,
      agentTools: filesAgentTools,
    },
  ],
  commands: [
    {
      id: 'files.open',
      title: 'Files: Open file explorer',
      run: () => registry.openPanel('files.tree'),
    },
    { id: 'files.newFile', title: 'Files: New file', run: newFile },
    { id: 'files.newFolder', title: 'Files: New folder', run: newFolder },
    { id: 'files.rename', title: 'Files: Rename', run: renameSelected },
    { id: 'files.delete', title: 'Files: Delete', run: deleteSelected },
    { id: 'files.refresh', title: 'Files: Refresh tree', run: () => refreshTree() },
    {
      id: 'files.openTerminalHere',
      title: 'Files: Open terminal here',
      run: openTerminalHere,
    },
    {
      id: 'files.revealActiveBuffer',
      title: 'Files: Reveal active buffer',
      run: revealActiveBuffer,
    },
  ],
};
