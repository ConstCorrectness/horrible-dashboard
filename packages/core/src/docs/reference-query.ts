/**
 * Open the Python reference pane on a symbol, from anywhere — the hover popup's
 * "Open in reference", a palette command, a notebook.
 *
 * The `llamacpp/trace-prompt.ts` shape: a pending query read on mount, plus a
 * subscription for a pane that is already open. Either alone drops half the cases.
 * In core rather than in the docs module because the popup that calls it is core
 * infrastructure and must not import a module.
 */
import { registry } from '../registry';

let pending: string | null = null;
const listeners = new Set<(query: string) => void>();

/** Ask the reference pane to search for `query`, mounted or not. */
export function sendReferenceQuery(query: string): void {
  pending = query;
  listeners.forEach((listener) => listener(query));
}

export function takeReferenceQuery(): string | null {
  const next = pending;
  pending = null;
  return next;
}

export function subscribeReferenceQuery(listener: (query: string) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Search for `query` in the reference pane, opening it first if needed. */
export function openReference(query: string): void {
  sendReferenceQuery(query);
  void registry.openPanel('docs.reference');
}
