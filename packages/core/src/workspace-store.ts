/**
 * Reactive snapshot of the live workspace set + which one is active. The
 * `Workspace` component (packages/ui) owns this state and `publish`es it on every
 * change; the shell **rail** (AppShell) subscribes to render the workflow-layout
 * switcher and highlight the active one. A small observable mirroring
 * `settingsStore` — kept here in core so both ui and feature code can read it
 * without a core→ui cycle. See docs/architecture/windowing.md.
 */
import { useSyncExternalStore } from 'react';

export interface WorkspaceSummary {
  id: string;
  name: string;
}

export interface WorkspaceSnapshot {
  workspaces: WorkspaceSummary[];
  activeId: string | null;
}

// Replaced by a new reference on every publish so useSyncExternalStore sees it.
let state: WorkspaceSnapshot = { workspaces: [], activeId: null };
const listeners = new Set<() => void>();

export const workspaceStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  /** Stable reference between changes — safe for useSyncExternalStore. */
  getSnapshot(): WorkspaceSnapshot {
    return state;
  },
  /** Called by the Workspace whenever its list/active selection changes. */
  publish(next: WorkspaceSnapshot): void {
    state = next;
    for (const listener of listeners) listener();
  },
};

/** Reactive read of the workspace set + active id (for the rail). */
export function useWorkspaces(): WorkspaceSnapshot {
  return useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot);
}
