/**
 * The repo file tree. Virtualized like the workspace file explorer, because a
 * recursive tree of a real repository is routinely thousands of rows.
 */
import { useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';

import type { TreeRow } from './tree';

const ROW_HEIGHT = 22;

export function RepoTree({
  rows,
  expanded,
  activePath,
  onToggle,
  onOpenFile,
}: {
  rows: TreeRow[];
  expanded: ReadonlySet<string>;
  activePath: string | null;
  onToggle: (path: string) => void;
  onOpenFile: (path: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  return (
    <div className="gh-tree" ref={scrollRef}>
      <div className="gh-tree-inner" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((item) => {
          const row = rows[item.index];
          const isDir = row.kind === 'dir';
          const isOpen = isDir && expanded.has(row.path);
          return (
            <button
              key={row.path}
              className={`gh-tree-row${row.path === activePath ? ' active' : ''}`}
              style={{
                transform: `translateY(${item.start}px)`,
                paddingLeft: `${row.depth * 12 + 8}px`,
              }}
              onClick={() => (isDir ? onToggle(row.path) : onOpenFile(row.path))}
              title={row.path}
            >
              <span className="gh-tree-caret">{isDir ? (isOpen ? '▾' : '▸') : ''}</span>
              <span className="gh-tree-icon">{isDir ? '📁' : '📄'}</span>
              <span className="gh-tree-name">{row.name}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
