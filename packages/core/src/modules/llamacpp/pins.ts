/**
 * Pinned trace nodes — the watch list.
 *
 * **A pin is a node NAME, not a record index.** That is the whole design, and it
 * comes from the debugger analogy: a watch is an expression re-evaluated at every
 * stop, not a pointer to one evaluation of it. So a pin survives switching pass
 * (each generated token is its own forward pass with its own records under the same
 * names) and survives opening a *different trace of the same model*, which is the
 * closest thing here to re-running a program under gdb.
 *
 * **Keyed by model, not by trace.** A pin expresses interest in a model's structure
 * — "watch layer 41's residual" — which is a fact about the architecture, not about
 * one run of it. Keying by trace id would throw the watch list away exactly when it
 * became useful, on the second trace.
 *
 * Stored in localStorage with the guarded shape `layout/persistence.ts` uses for
 * workspace agent overrides, and for the same reason: this is a UI preference of
 * *this browser*, not part of the layout every client shares. A disabled or full
 * store degrades to "pins don't persist", never to a thrown error mid-render.
 */

const KEY = 'horrible.llamacpp.pins';

/**
 * Watches per trace view. Each is a request per pass change, so an uncapped list
 * turns switching pass into a request storm; the UI disables pinning past this and
 * says why rather than silently ignoring the click.
 */
export const MAX_PINS = 8;

type Store = Record<string, string[]>;

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object') return {};
    // Hand-editable storage: keep only the entries that are actually string lists
    // rather than trusting the shape and crashing a pane on a corrupt key.
    const store: Store = {};
    for (const [model, names] of Object.entries(parsed as Record<string, unknown>)) {
      if (Array.isArray(names))
        store[model] = names.filter((n): n is string => typeof n === 'string');
    }
    return store;
  } catch {
    return {}; // private mode / corrupt entry — start with no pins
  }
}

function write(store: Store): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    /* storage unavailable — the pins just don't outlive this page */
  }
}

/** The pinned node names for a model, in the order they were pinned. */
export function getPins(model: string): string[] {
  if (!model) return [];
  return read()[model] ?? [];
}

/** Add a pin. Ignores a duplicate and refuses past {@link MAX_PINS}; returns the
 * resulting list either way so a caller can render what actually happened. */
export function addPin(model: string, name: string): string[] {
  if (!model || !name) return getPins(model);
  const store = read();
  const current = store[model] ?? [];
  if (current.includes(name) || current.length >= MAX_PINS) return current;
  const next = [...current, name];
  store[model] = next;
  write(store);
  return next;
}

/** Add several at once (pinning a whole block), stopping at the cap rather than
 * dropping the ones that fit. */
export function addPins(model: string, names: readonly string[]): string[] {
  if (!model) return [];
  const store = read();
  const next = [...(store[model] ?? [])];
  for (const name of names) {
    if (next.length >= MAX_PINS) break;
    if (!next.includes(name)) next.push(name);
  }
  store[model] = next;
  write(store);
  return next;
}

export function removePin(model: string, name: string): string[] {
  if (!model) return [];
  const store = read();
  const next = (store[model] ?? []).filter((n) => n !== name);
  store[model] = next;
  write(store);
  return next;
}

export function clearPins(model: string): string[] {
  if (!model) return [];
  const store = read();
  delete store[model];
  write(store);
  return [];
}
