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
import { createEntry, deleteEntry, joinPath, listRoots, parentDir } from './api';
import { FileTree } from './FileTree';
import {
  getActivePath,
  getSelectedPaths,
  getSelection,
  kindFor,
  refreshTree,
  setRevealTarget,
  setSelection,
  startRename,
} from './store';

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

/** Palette entry: start the tree's inline rename on the active selection (the
 * same in-place edit F2 and the context menu use). */
function renameSelected(): void {
  const active = getActivePath();
  if (!active) {
    window.alert('Select a file or folder to rename.');
    return;
  }
  registry.openPanel('files.tree');
  startRename(active);
}

/** Palette entry: delete the whole multi-selection (the tree's context menu /
 * Delete key share this behavior via the view). */
async function deleteSelected(): Promise<void> {
  const paths = [...getSelectedPaths()];
  if (paths.length === 0) {
    window.alert('Select a file or folder to delete.');
    return;
  }
  const label = paths.length === 1 ? paths[0] : `${paths.length} items`;
  if (!window.confirm(`Delete ${label}?`)) return;
  for (const p of paths) {
    try {
      await deleteEntry(p, kindFor(p) === 'dir');
    } catch {
      /* surfaced by the watch re-list; skip */
    }
  }
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
