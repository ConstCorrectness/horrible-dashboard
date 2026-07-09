/**
 * Notebook session store: a thin binding of the shared generic SessionStore to
 * the `notebook` ws channel. The backend session key is `nb:{path}`, which the
 * store id must match so `opened`/`error` events resolve to the right pane.
 */
import { openSession, type SessionStore } from '../../notebook/SessionStore';

export const NOTEBOOK_CHANNEL = 'notebook';

export function sessionKeyFor(path: string): string {
  return `nb:${path.replace(/\\/g, '/')}`;
}

/** Open (or reattach to) the kernel session for a notebook path. */
export function openNotebookSession(path: string): SessionStore {
  return openSession(NOTEBOOK_CHANNEL, sessionKeyFor(path), { path });
}
