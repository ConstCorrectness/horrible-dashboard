/**
 * The workspace file tree (panel `files.tree`, left dock) — a VS Code-style
 * explorer. State lives in the shared store (roots, expansion, children cache,
 * multi-selection, inline rename) so it survives a dockview remount; this view
 * renders the store's flattened row list and drives interactions: click/ctrl/shift
 * multi-select, keyboard nav, F2 inline rename, and a right-click context menu.
 * Disk changes arrive live over the `files` watch channel. See
 * docs/modules/file-explorer.md.
 */
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { useVirtualizer } from '@tanstack/react-virtual';

import { useAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { openBuffer } from '../editor';
import { deleteEntry, joinPath, parentDir, renameEntry } from './api';
import { fileIcon } from './icons';
import {
  cancelRename,
  collapse,
  expand,
  filesVersion,
  getActivePath,
  getGitBranch,
  getRenaming,
  getRevealTarget,
  getRoots,
  getRootsError,
  getSelectedPaths,
  gitChangeCount,
  gitDirChanged,
  gitStatusFor,
  initFilesWatch,
  isExpanded,
  isLoading,
  kindFor,
  loadRoots,
  refreshTree,
  reloadGit,
  selectRange,
  selectSingle,
  setRevealTarget,
  setSelection,
  startRename,
  subscribeFiles,
  toggleExpanded,
  toggleSelect,
  visibleRows,
  type Row,
} from './store';
import type { GitStatusKind } from './api';

/** Single-letter badge + color class for a git status (VS Code-ish). */
const GIT_BADGE: Record<GitStatusKind, string> = {
  modified: 'M',
  added: 'A',
  deleted: 'D',
  untracked: 'U',
  renamed: 'R',
  conflict: '!',
};

function sep(path: string): string {
  return path.includes('\\') ? '\\' : '/';
}

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** Expand every ancestor directory of `target` so a reveal can scroll to it. */
function expandAncestors(target: string): void {
  const s = sep(target);
  const root = getRoots().find((r) => target === r.path || target.startsWith(r.path + s));
  if (!root) return;
  expand(root.path);
  const rest = target.slice(root.path.length).split(s).filter(Boolean);
  let cur = root.path;
  for (let i = 0; i < rest.length - 1; i++) {
    cur = `${cur}${s}${rest[i]}`;
    expand(cur);
  }
  selectSingle(target);
}

interface MenuState {
  x: number;
  y: number;
  row: Row;
}

export function FileTree() {
  useSyncExternalStore(subscribeFiles, filesVersion);
  const rows = visibleRows();
  const rootsError = getRootsError();
  const selected = getSelectedPaths();
  const active = getActivePath();
  const renaming = getRenaming();
  const revealTarget = getRevealTarget();
  const [menu, setMenu] = useState<MenuState | null>(null);
  const typeahead = useRef<{ buf: string; at: number }>({ buf: '', at: 0 });
  const parentRef = useRef<HTMLDivElement>(null);

  // Virtualize the flat row list so large trees stay smooth.
  const rowVirt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 22,
    overscan: 12,
  });

  useEffect(() => {
    initFilesWatch();
    void loadRoots().then(reloadGit);
  }, []);

  // Keep the active row in view as the keyboard selection or a reveal moves it.
  useEffect(() => {
    if (!active) return;
    const i = rows.findIndex((r) => r.path === active);
    if (i >= 0) rowVirt.scrollToIndex(i, { align: 'auto' });
    // Intentionally keyed on the active path only, not every row-list recompute.
  }, [active]);

  // Reveal: expand the path's ancestors and select it, then clear the request.
  useEffect(() => {
    if (!revealTarget) return;
    expandAncestors(revealTarget);
    setRevealTarget(null);
  }, [revealTarget]);

  const order = rows.map((r) => r.path);

  const openRow = (row: Row) => {
    if (row.kind === 'dir') toggleExpanded(row.path);
    else openBuffer(`workspace-file:${row.path}`);
  };

  const onRowClick = (e: React.MouseEvent, row: Row) => {
    if (e.metaKey || e.ctrlKey) {
      toggleSelect(row.path);
      return;
    }
    if (e.shiftKey) {
      selectRange(row.path, order);
      return;
    }
    selectSingle(row.path);
    openRow(row);
  };

  const onRowContextMenu = (e: React.MouseEvent, row: Row) => {
    e.preventDefault();
    if (!selected.has(row.path)) selectSingle(row.path);
    setMenu({ x: e.clientX, y: e.clientY, row });
  };

  const deleteSelection = async () => {
    const paths = selected.size ? [...selected] : active ? [active] : [];
    if (paths.length === 0) return;
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
  };

  const commitRename = (row: Row, value: string) => {
    const name = value.trim();
    cancelRename();
    if (!name || name === row.name) return;
    const dest = joinPath(parentDir(row.path), name);
    void renameEntry(row.path, dest)
      .then(() => {
        selectSingle(dest);
        refreshTree();
      })
      .catch(() => refreshTree());
  };

  const typeaheadJump = (ch: string) => {
    const now = Date.now();
    const ta = typeahead.current;
    ta.buf = now - ta.at > 700 ? ch : ta.buf + ch;
    ta.at = now;
    const from = active ? rows.findIndex((r) => r.path === active) : -1;
    const n = rows.length;
    for (let i = 1; i <= n; i++) {
      const row = rows[(from + i) % n];
      if (row.name.toLowerCase().startsWith(ta.buf.toLowerCase())) {
        selectSingle(row.path);
        return;
      }
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (renaming) return; // the rename input owns its keys
    const idx = active ? rows.findIndex((r) => r.path === active) : -1;
    const row = idx >= 0 ? rows[idx] : undefined;
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (rows[idx + 1]) selectSingle(rows[idx + 1].path);
        else if (idx < 0 && rows[0]) selectSingle(rows[0].path);
        break;
      case 'ArrowUp':
        e.preventDefault();
        if (idx > 0) selectSingle(rows[idx - 1].path);
        break;
      case 'ArrowRight':
        if (row?.kind === 'dir') {
          if (!isExpanded(row.path)) expand(row.path);
          else if (rows[idx + 1] && rows[idx + 1].depth > row.depth)
            selectSingle(rows[idx + 1].path);
        }
        break;
      case 'ArrowLeft':
        if (row) {
          if (row.kind === 'dir' && isExpanded(row.path)) collapse(row.path);
          else {
            const parent = rows
              .slice(0, idx)
              .reverse()
              .find((r) => r.depth < row.depth);
            if (parent) selectSingle(parent.path);
          }
        }
        break;
      case 'Enter':
        if (row) openRow(row);
        break;
      case 'F2':
        if (row) startRename(row.path);
        break;
      case 'Delete':
      case 'Backspace':
        void deleteSelection();
        break;
      default:
        if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) typeaheadJump(e.key);
    }
  };

  // Agent context: roots, the active + multi-selection, and a git summary.
  useAgentContext(() => ({
    roots: getRoots().map((r) => r.path),
    selection: active ? { path: active, kind: kindFor(active) } : null,
    selectedPaths: [...selected],
    git: { branch: getGitBranch(), changeCount: gitChangeCount() },
  }));

  return (
    <div className="file-tree">
      <div className="file-tree-toolbar">
        <button title="New file" onClick={() => void registry.runCommand('files.newFile')}>
          ＋
        </button>
        <button title="New folder" onClick={() => void registry.runCommand('files.newFolder')}>
          ＋📁
        </button>
        <button
          title="Refresh"
          onClick={() => {
            refreshTree();
            void reloadGit();
          }}
        >
          ⟳
        </button>
        {getGitBranch() && (
          <span className="file-git-branch" title={`On branch ${getGitBranch()}`}>
            ⎇ {getGitBranch()}
          </span>
        )}
      </div>
      {rootsError && <div className="file-tree-error">{rootsError}</div>}
      {!rootsError && rows.length === 0 && (
        <div className="file-tree-empty">No workspace roots — configure them in Settings.</div>
      )}
      <div className="file-tree-rows" ref={parentRef} tabIndex={0} onKeyDown={onKeyDown}>
        <div style={{ height: rowVirt.getTotalSize(), position: 'relative', width: '100%' }}>
          {rowVirt.getVirtualItems().map((vi) => {
            const row = rows[vi.index];
            return (
              <div
                key={row.path}
                data-index={vi.index}
                ref={rowVirt.measureElement}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  transform: `translateY(${vi.start}px)`,
                }}
              >
                <FileRow
                  row={row}
                  selected={selected.has(row.path)}
                  active={row.path === active}
                  renaming={renaming === row.path}
                  onClick={(e) => onRowClick(e, row)}
                  onContextMenu={(e) => onRowContextMenu(e, row)}
                  onCommitRename={(v) => commitRename(row, v)}
                  onCancelRename={cancelRename}
                />
              </div>
            );
          })}
        </div>
      </div>
      {menu && (
        <FileContextMenu
          state={menu}
          onClose={() => setMenu(null)}
          onRename={() => startRename(menu.row.path)}
          onDelete={() => void deleteSelection()}
        />
      )}
    </div>
  );
}

function FileRow({
  row,
  selected,
  active,
  renaming,
  onClick,
  onContextMenu,
  onCommitRename,
  onCancelRename,
}: {
  row: Row;
  selected: boolean;
  active: boolean;
  renaming: boolean;
  onClick: (e: React.MouseEvent) => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onCommitRename: (value: string) => void;
  onCancelRename: () => void;
}) {
  // Git decoration: a file shows its own status; a folder dims if it contains one.
  const git = row.kind === 'file' ? gitStatusFor(row.path) : undefined;
  const dirChanged = row.kind === 'dir' && gitDirChanged(row.path);
  const cls = [
    'file-row',
    selected ? 'selected' : '',
    active ? 'active' : '',
    git ? `git-${git}` : '',
    dirChanged ? 'git-dir-changed' : '',
  ]
    .filter(Boolean)
    .join(' ');
  const icon = row.kind === 'file' ? fileIcon(row.name) : null;
  const loading = row.kind === 'dir' && isExpanded(row.path) && isLoading(row.path);

  return (
    <div
      className={cls}
      style={{ paddingLeft: row.depth * 12 + 8 }}
      onClick={renaming ? undefined : onClick}
      onContextMenu={onContextMenu}
      title={row.path}
    >
      <span className="file-caret">
        {row.kind === 'dir' ? (isExpanded(row.path) ? '▾' : '▸') : ''}
      </span>
      {row.kind === 'dir' ? (
        <span className="file-icon ic-dir">{isExpanded(row.path) ? '📂' : '📁'}</span>
      ) : (
        <span className={`file-icon ${icon!.cls}`}>{icon!.label}</span>
      )}
      {renaming ? (
        <input
          className="file-rename-input"
          defaultValue={row.name}
          autoFocus
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === 'Enter') onCommitRename((e.target as HTMLInputElement).value);
            else if (e.key === 'Escape') onCancelRename();
          }}
          onBlur={(e) => onCommitRename(e.target.value)}
        />
      ) : (
        <span className="file-name">{row.name}</span>
      )}
      {git && !renaming && <span className={`git-badge git-${git}`}>{GIT_BADGE[git]}</span>}
      {loading && <span className="file-loading">…</span>}
    </div>
  );
}

function FileContextMenu({
  state,
  onClose,
  onRename,
  onDelete,
}: {
  state: MenuState;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const { x, y, row } = state;
  const targetDir = row.kind === 'dir' ? row.path : parentDir(row.path);

  useEffect(() => {
    const onDown = (e: globalThis.MouseEvent) => {
      if (!(e.target as Element).closest?.('.file-ctx-menu')) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const run = (fn: () => void) => () => {
    onClose();
    fn();
  };

  // Clamp within the viewport (flip up if near the bottom).
  const style: CSSProperties = {
    left: Math.min(x, window.innerWidth - 200),
    ...(y > window.innerHeight - 260 ? { bottom: window.innerHeight - y } : { top: y }),
  };

  return createPortal(
    <div className="file-ctx-menu" style={style}>
      {row.kind === 'file' && (
        <button onClick={run(() => openBuffer(`workspace-file:${row.path}`))}>Open</button>
      )}
      <button onClick={run(() => void registry.runCommand('files.newFile'))}>New File</button>
      <button onClick={run(() => void registry.runCommand('files.newFolder'))}>New Folder</button>
      <div className="file-ctx-sep" />
      <button onClick={run(onRename)}>Rename</button>
      <button className="danger" onClick={run(onDelete)}>
        Delete
      </button>
      <div className="file-ctx-sep" />
      <button onClick={run(() => void navigator.clipboard?.writeText(row.path))}>Copy Path</button>
      <button onClick={run(() => void registry.runCommand('files.openTerminalHere'))}>
        Open Terminal Here
      </button>
      <span className="file-ctx-target" title={targetDir}>
        {basename(targetDir)}
      </span>
    </div>,
    document.body,
  );
}
