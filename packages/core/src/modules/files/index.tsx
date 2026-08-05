/**
 * File explorer module: a tree over the workspace roots (panel `files.tree`) plus
 * CRUD commands. Opening a file routes through the editor's public `openBuffer`
 * service; "open terminal here" / desktop reveal are B5/B6. See
 * docs/modules/file-explorer.md.
 */
import { dialogs } from '../../dialogs';
import { revealSection } from '../../layout/controller';
import { toastsStore } from '../../toasts';
import type { ModuleManifest } from '../../registry';
import { getActiveBufferSource, openBuffer } from '../editor';
import { openTerminal } from '../terminal';
import type { ContextMenuItem, ContextTarget } from '../../overlay/context-menu';
import { deleteSelection, selectionPaths } from './actions';
import { filesAgentTools } from './agentTools';
import { bufferUriFor, createEntry, isVirtualPath, joinPath, listRoots, parentDir } from './api';
import { FileTree } from './FileTree';
import {
  getActivePath,
  getSelection,
  refreshTree,
  setRevealTarget,
  setSelection,
  startRename,
} from './store';

const FILE_URI = 'workspace-file:';

/**
 * The file tree's own right-click items.
 *
 * The shape the old inline menu established and this keeps: a virtual root (a
 * mounted Drive) is **read-only and has no local directory behind it**, so
 * everything that writes or shells out is *omitted* rather than shown disabled —
 * there is no state in which those become available, and a permanently greyed row
 * only invites the user to keep trying it.
 */
function filesNodeItems(target: ContextTarget): ContextMenuItem[] {
  const path = String(target.path ?? '');
  if (!path) return [];
  const isDir = target.nodeKind === 'dir';
  const readOnly = isVirtualPath(path);
  const count = selectionPaths().length;
  const items: ContextMenuItem[] = [];

  if (!isDir) {
    items.push({ id: 'files.open', label: 'Open', run: () => openBuffer(bufferUriFor(path)) });
  }
  if (!readOnly) {
    items.push(
      { id: 'files.newFile', label: 'New File', run: newFile },
      { id: 'files.newFolder', label: 'New Folder', run: newFolder },
      { id: 'files.rename', label: 'Rename', hint: 'F2', run: () => startRename(path) },
      {
        id: 'files.delete',
        // The label counts, because a multi-selection right-clicked on one of its
        // rows deletes all of them — the menu is the last place to say so.
        label: count > 1 ? `Delete ${count} items` : 'Delete',
        danger: true,
        run: () => void deleteSelection(),
      },
    );
  }
  items.push({
    id: 'files.copyPath',
    label: 'Copy Path',
    hint: parentDir(path).split(/[\\/]/).pop(),
    run: () => void navigator.clipboard?.writeText(path),
  });
  if (!readOnly) {
    items.push({
      id: 'files.openTerminalHere',
      label: 'Open Terminal Here',
      run: openTerminalHere,
    });
  }
  return items;
}

/** The directory new entries should be created in: the selected dir, the selected
 * file's parent, or the first workspace root. */
async function targetDir(): Promise<string | null> {
  const sel = getSelection();
  // A selection inside a read-only virtual root (Drive) can't host a new file, so fall
  // through to a real root rather than issuing a create the backend will 403.
  if (sel && !isVirtualPath(sel.path)) {
    return sel.kind === 'dir' ? sel.path : parentDir(sel.path);
  }
  const roots = await listRoots();
  return roots.find((r) => !isVirtualPath(r.path))?.path ?? null;
}

async function newFile(): Promise<void> {
  const dir = await targetDir();
  if (!dir) return;
  const name = await dialogs.prompt({
    title: 'New file',
    defaultValue: 'untitled.md',
    confirmLabel: 'Create',
  });
  if (!name) return;
  const path = joinPath(dir, name);
  await createEntry(path, 'file');
  refreshTree();
  openBuffer(`${FILE_URI}${path}`);
}

async function newFolder(): Promise<void> {
  const dir = await targetDir();
  if (!dir) return;
  const name = await dialogs.prompt({
    title: 'New folder',
    defaultValue: 'folder',
    confirmLabel: 'Create',
  });
  if (!name) return;
  await createEntry(joinPath(dir, name), 'dir');
  refreshTree();
}

/** Palette entry: start the tree's inline rename on the active selection (the
 * same in-place edit F2 and the context menu use). */
function renameSelected(): void {
  const active = getActivePath();
  if (!active) {
    toastsStore.add('warning', 'Nothing selected', 'Select a file or folder to rename.');
    return;
  }
  revealSection('files', 'explorer.home');
  startRename(active);
}

/**
 * Palette entry: delete the whole multi-selection. The Delete key and the context
 * menu run the same `deleteSelection`; the only thing this adds is the toast for
 * an empty selection, which a palette invocation needs (there is no row under the
 * cursor to explain itself) and the other two do not.
 *
 * This used to be a second copy of the confirm-and-delete loop, drifting from the
 * tree's own by exactly one behaviour: it ignored the active row when nothing was
 * multi-selected, so the palette and the menu disagreed about what "the selection"
 * meant.
 */
async function deleteSelected(): Promise<void> {
  if (selectionPaths().length === 0) {
    toastsStore.add('warning', 'Nothing selected', 'Select a file or folder to delete.');
    return;
  }
  await deleteSelection();
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
  revealSection('files', 'explorer.home');
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
      role: 'tool',
      icon: '🗀',
      defaultDock: 'left',
      singleton: true,
      // A section of Explorer now, not a dock strip of its own. Still registered
      // so `show("files.tree")` and `openPaneInArea` keep working; the tools stay
      // declared here because they are the *files* module's tools, and the
      // backend groups by name prefix regardless of which view declares them.
      embedded: true,
      agentTools: filesAgentTools,
    },
  ],
  explorerSources: [
    { id: 'files', label: 'Files', icon: '🗀', view: 'files.tree', key: 'f', default: true },
  ],
  // `order: 0` — the owning module's items come first; other modules append.
  contextMenu: [{ kind: 'files.node', order: 0, items: filesNodeItems }],
  commands: [
    {
      id: 'files.open',
      title: 'Files: Open file explorer',
      run: () => {
        revealSection('files', 'explorer.home');
      },
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
