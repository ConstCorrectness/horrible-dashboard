/**
 * Which conversation the Messages section is showing.
 *
 * A three-line store rather than a prop, because the two sides live in different
 * *sections* of the People pane: the Friends list decides ("message Andrew") and
 * the Messages section renders, and section bodies are siblings mounted by the
 * shell — there is no parent between them to hold the state.
 *
 * Deliberately not persisted: which thread you had open is not something to
 * restore three days later, and a stale selection pointing at a removed friend is
 * a bug with no upside.
 */
let selected: string | null = null;
const listeners = new Set<() => void>();

export function getConversation(): string | null {
  return selected;
}

export function subscribeConversation(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Select a conversation by `person_id`. Pass `null` to clear. */
export function openConversation(personId: string | null): void {
  if (selected === personId) return;
  selected = personId;
  for (const listener of listeners) listener();
}
