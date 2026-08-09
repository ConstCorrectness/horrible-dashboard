/**
 * The workspace file tree (panel `files.tree`, left dock) — a VS Code-style
 * explorer. State lives in the shared store (roots, expansion, children cache,
 * multi-selection, inline rename) so it survives a pane remount; this view
 * renders the store's flattened row list and drives interactions: click/ctrl/shift
 * multi-select, keyboard nav, F2 inline rename, and a right-click context menu.
 * Disk changes arrive live over the `files` watch channel. See
 * docs/modules/file-explorer.md.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

import { useAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { openBuffer } from '../editor';
import { openContextMenu } from '../../overlay/context-menu';
import { deleteSelection } from './actions';
import { bufferUriFor, isVirtualPath, joinPath, parentDir, renameEntry } from './api';
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

export function FileTree() {
  useSyncExternalStore(subscribeFiles, filesVersion);
  const rows = visibleRows();
  const rootsError = getRootsError();
  const selected = getSelectedPaths();
  const active = getActivePath();
  const renaming = getRenaming();
  const revealTarget = getRevealTarget();
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
    else openBuffer(bufferUriFor(row.path));
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
    // Right-clicking outside the selection retargets it first — acting on a row
    // the user cannot see highlighted is how "delete" hits the wrong thing.
    if (!selected.has(row.path)) selectSingle(row.path);
    // The row *is* the target; what can be done to it is the providers' business.
    // If nothing offered an item, fall through to the browser's own menu rather
    // than swallowing the gesture.
    if (
      openContextMenu(e, { kind: 'files.node', path: row.path, nodeKind: row.kind, name: row.name })
    ) {
      e.preventDefault();
    }
  };

  /**
   * Right-click on the empty space below the last row. Bubbling means this also
   * fires for a row click, so it bails when the row handler already opened a
   * menu — checked via `defaultPrevented` rather than a stopPropagation in the
   * row, because a row that swallowed the event would also stop the browser menu
   * from appearing on a target no provider answered for.
   */
  const onBackgroundContextMenu = (e: React.MouseEvent) => {
    if (e.defaultPrevented) return;
    if (openContextMenu(e, { kind: 'files.background' })) e.preventDefault();
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
      // Virtual roots (Drive) are read-only — the mutating shortcuts do nothing there
      // rather than firing a request the backend will 403.
      case 'F2':
        if (row && !isVirtualPath(row.path)) startRename(row.path);
        break;
      case 'Delete':
      case 'Backspace':
        if (!row || !isVirtualPath(row.path)) void deleteSelection();
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
      <div
        className="file-tree-rows"
        ref={parentRef}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onContextMenu={onBackgroundContextMenu}
      >
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
