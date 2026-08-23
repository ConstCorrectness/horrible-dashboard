/**
 * Pinned vocabulary tokens — the lens's watch list.
 *
 * The dual of `pins.ts`. A node pin asks "what is in this tensor"; a vocabulary
 * pin asks "where is this word, everywhere" — and the second is what turns a
 * grid from a screenshot into a finding: *Paris was already rank 400 by layer
 * 12* is a claim, *layer 30 says Paris* is a picture.
 *
 * It reuses the node pins' two decisions exactly, because they were right for
 * the same reasons:
 *
 * - **Keyed by model, not by trace.** "Watch where ` Paris` is" is a question
 *   about a model, not about one run of it, and the pin becomes useful on the
 *   *second* trace — which is precisely when keying by trace would have thrown
 *   it away.
 * - **Capped and stored per browser.** Each pin is a request per grid, and this
 *   is a preference of this client rather than part of the shared layout.
 *
 * It is a separate store rather than a shared one because the two are keyed by
 * different things — a node name and a token id — and a single list of "pins"
 * holding both would need a discriminator on every read.
 */

const KEY = 'horrible.llamacpp.vocab-pins';

/** Each pin costs a full pass over the output head, so the cap is lower than the
 * node watch list's: eight streamed matmuls over a 262k-row matrix is not a
 * grid you would wait for. */
export const MAX_VOCAB_PINS = 4;

export interface VocabPin {
  id: number;
  /** The rendered text, kept so a pin still reads correctly before its track
   * loads — and so a pin survives a model whose vocabulary we cannot reach. */
  text: string;
}

type Store = Record<string, VocabPin[]>;

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== 'object') return {};
    const store: Store = {};
    for (const [model, pins] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(pins)) continue;
      store[model] = pins.filter(
        (pin): pin is VocabPin =>
          !!pin &&
          typeof pin === 'object' &&
          typeof (pin as VocabPin).id === 'number' &&
          typeof (pin as VocabPin).text === 'string',
      );
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

export function getVocabPins(model: string): VocabPin[] {
  if (!model) return [];
  return read()[model] ?? [];
}

/** Add a pin. Ignores a duplicate and refuses past {@link MAX_VOCAB_PINS};
 * returns the resulting list either way so the caller renders what happened. */
export function addVocabPin(model: string, pin: VocabPin): VocabPin[] {
  if (!model) return [];
  const store = read();
  const current = store[model] ?? [];
  if (current.some((p) => p.id === pin.id) || current.length >= MAX_VOCAB_PINS) return current;
  const next = [...current, pin];
  store[model] = next;
  write(store);
  return next;
}

export function removeVocabPin(model: string, id: number): VocabPin[] {
  if (!model) return [];
  const store = read();
  const next = (store[model] ?? []).filter((pin) => pin.id !== id);
  store[model] = next;
  write(store);
  return next;
}

export function clearVocabPins(model: string): VocabPin[] {
  if (!model) return [];
  const store = read();
  delete store[model];
  write(store);
  return [];
}
