/**
 * Which notebook cell you are in, per notebook — what the Learn strip explains.
 *
 * Module-global rather than notebook state, because the reader is a region strip
 * rendered beside the notebook, not inside it, and a strip opened mid-session has
 * to know where the caret already is. Keyed like `openSession`
 * (`projectId:notebookPath`), so two notebooks open side by side keep two answers.
 */
import { useSyncExternalStore } from 'react';

const focused = new Map<string, string>();
const listeners = new Set<() => void>();

export function focusKey(projectId: string, notebookPath: string): string {
  return `${projectId}:${notebookPath}`;
}

export function noteCellFocused(key: string, cellId: string): void {
  if (focused.get(key) === cellId) return;
  focused.set(key, cellId);
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useFocusedCellId(key: string): string | null {
  return useSyncExternalStore(
    subscribe,
    () => focused.get(key) ?? null,
    () => null,
  );
}
