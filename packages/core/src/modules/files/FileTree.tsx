/**
 * The workspace file tree (panel `files.tree`, left dock). Shows the backend's
 * configured roots; directories lazy-load and expand on click, files open as
 * `workspace-file:` editor buffers. Mutations re-list via the shared store's
 * refresh counter (live watch events are B1b). See docs/modules/file-explorer.md.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';

import { useAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { openBuffer } from '../editor';
import { listDir, listRoots, type FileEntry, type RootInfo } from './api';
import {
  filesVersion,
  getRevealTarget,
  getSelection,
  getTreeRefresh,
  setSelection,
  subscribeFiles,
} from './store';

function sep(path: string): string {
  return path.includes('\\') ? '\\' : '/';
}

function TreeNode({
  name,
  path,
  kind,
  depth,
  refresh,
  selectedPath,
  revealTarget,
}: {
  name: string;
  path: string;
  kind: 'file' | 'dir';
  depth: number;
  refresh: number;
  selectedPath: string | undefined;
  revealTarget: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FileEntry[] | null>(null);

  // Auto-expand when a reveal target lives under this directory.
  useEffect(() => {
    if (kind === 'dir' && revealTarget && revealTarget.startsWith(path + sep(path))) {
      setExpanded(true);
    }
  }, [revealTarget, kind, path]);

  // (Re)load children when expanded or after a tree refresh.
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    void listDir(path)
      .then((r) => {
        if (!cancelled) setChildren(r.entries);
      })
      .catch(() => {
        if (!cancelled) setChildren([]);
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, refresh, path]);

  const onClick = () => {
    setSelection({ path, kind });
    if (kind === 'dir') {
      setExpanded((e) => !e);
    } else {
      openBuffer(`workspace-file:${path}`);
    }
  };

  return (
    <div>
      <div
        className={`file-row${selectedPath === path ? ' selected' : ''}`}
        style={{ paddingLeft: depth * 12 + 8 }}
        onClick={onClick}
        title={path}
      >
        <span className="file-caret">{kind === 'dir' ? (expanded ? '▾' : '▸') : ''}</span>
        <span className="file-name">{name}</span>
      </div>
      {expanded &&
        children?.map((c) => (
          <TreeNode
            key={c.path}
            name={c.name}
            path={c.path}
            kind={c.kind}
            depth={depth + 1}
            refresh={refresh}
            selectedPath={selectedPath}
            revealTarget={revealTarget}
          />
        ))}
    </div>
  );
}

export function FileTree() {
  useSyncExternalStore(subscribeFiles, filesVersion);
  const [roots, setRoots] = useState<RootInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = getTreeRefresh();

  useEffect(() => {
    let cancelled = false;
    void listRoots()
      .then((r) => {
        if (!cancelled) {
          setRoots(r);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const selection = getSelection();
  const revealTarget = getRevealTarget();

  // Expose the tree's current selection + roots for the agent to read on demand.
  useAgentContext(() => ({
    roots: roots.map((r) => r.path),
    selection: selection ? { path: selection.path, kind: selection.kind } : null,
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
        <button title="Rename" onClick={() => void registry.runCommand('files.rename')}>
          ✎
        </button>
        <button title="Delete" onClick={() => void registry.runCommand('files.delete')}>
          🗑
        </button>
        <button
          title="Open terminal here"
          onClick={() => void registry.runCommand('files.openTerminalHere')}
        >
          ▤
        </button>
        <button title="Refresh" onClick={() => void registry.runCommand('files.refresh')}>
          ⟳
        </button>
      </div>
      {error && <div className="file-tree-error">{error}</div>}
      {!error && roots.length === 0 && (
        <div className="file-tree-empty">No workspace roots — configure them in Settings.</div>
      )}
      {roots.map((r) => (
        <TreeNode
          key={r.path}
          name={r.name}
          path={r.path}
          kind="dir"
          depth={0}
          refresh={refresh}
          selectedPath={selection?.path}
          revealTarget={revealTarget}
        />
      ))}
    </div>
  );
}
