/**
 * Remembered sizes for in-pane splitters.
 *
 * Stored in localStorage with the guarded shape `modules/llamacpp/pins.ts` uses,
 * and for the same reason it gives: this is a UI preference of *this browser*, not
 * part of the layout every client shares. Putting a pane-internal width into
 * `layoutStore` would mean a new action, a reducer case, a persisted-schema
 * migration — and a width that travels inside an exported workspace, so opening
 * somebody else's workspace would silently resize your panes' insides.
 *
 * A disabled or corrupt store degrades to "the splitter opens at its default",
 * never to a thrown error mid-render.
 */

const KEY = 'horrible.split.sizes';

type Store = Record<string, number>;

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object') return {};
    const store: Store = {};
    for (const [id, value] of Object.entries(parsed as Record<string, unknown>)) {
      // Hand-editable storage: keep only finite positive numbers rather than
      // trusting the shape and handing `NaN` to a style property.
      if (typeof value === 'number' && Number.isFinite(value) && value > 0) store[id] = value;
    }
    return store;
  } catch {
    return {};
  }
}

export function getSplitSize(id: string): number | null {
  return read()[id] ?? null;
}

export function setSplitSize(id: string, size: number): void {
  try {
    const store = read();
    store[id] = Math.round(size);
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    // A full or disabled store means the size doesn't persist. It is not worth a
    // toast and it is certainly not worth throwing out of a pointerup handler.
  }
}

/**
 * The size the measured side may actually take.
 *
 * Both minimums are honoured, but they can conflict: a container narrower than
 * `min + minOther` cannot satisfy both. When it does, **`min` wins** — the measured
 * side is the one the user is dragging and the one whose content the caller chose a
 * floor for, so collapsing it to satisfy the other side would fight the pointer.
 * The caller's `narrowBelow` is the real answer to a container this small; this is
 * only the arithmetic that keeps the value finite until it fires.
 */
export function clampSize(raw: number, extent: number, min: number, minOther: number): number {
  if (!Number.isFinite(raw)) return min;
  const ceiling = Math.max(min, extent - minOther);
  return Math.max(min, Math.min(raw, ceiling));
}
