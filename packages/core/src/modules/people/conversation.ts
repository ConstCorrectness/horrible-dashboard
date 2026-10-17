/**
 * Which conversations are open, and which one is in front.
 *
 * A small store rather than a prop, because three places read it and none of
 * them is the others' parent: the Friends list opens a chat, the chat dock beside
 * it renders the open ones as tabs, and the Messages section shows the active one.
 *
 * Steam's model: opening a chat adds a tab and brings it forward; closing a tab
 * forgets it without touching the history, which lives on the backend.
 *
 * Deliberately not persisted: which threads you had open is not something to
 * restore three days later, and a stale tab pointing at a removed friend is a bug
 * with no upside.
 */
interface ConversationState {
  /** Open conversations, by `person_id`, in tab order. */
  open: string[];
  /** The one in front, or null when none is. Always a member of `open`. */
  active: string | null;
}

let state: ConversationState = { open: [], active: null };
const listeners = new Set<() => void>();

function set(next: ConversationState): void {
  state = next;
  for (const listener of listeners) listener();
}

export function getConversations(): ConversationState {
  return state;
}

/** The conversation in front — what the Messages section shows. */
export function getConversation(): string | null {
  return state.active;
}

export function subscribeConversation(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Open (or bring forward) a conversation by `person_id`. `null` hides the dock. */
export function openConversation(personId: string | null): void {
  if (personId === null) {
    if (state.active !== null) set({ ...state, active: null });
    return;
  }
  if (state.active === personId) return;
  const open = state.open.includes(personId) ? state.open : [...state.open, personId];
  set({ open, active: personId });
}

/** Close one tab. The neighbour to its left (else right) comes forward. */
export function closeConversation(personId: string): void {
  const i = state.open.indexOf(personId);
  if (i < 0) return;
  const open = state.open.filter((id) => id !== personId);
  const active = state.active === personId ? (open[Math.max(0, i - 1)] ?? null) : state.active;
  set({ open, active });
}

/** Drop tabs for people no longer in the roster (removed, blocked). */
export function pruneConversations(known: ReadonlySet<string>): void {
  const open = state.open.filter((id) => known.has(id));
  if (open.length === state.open.length) return;
  const active = state.active && known.has(state.active) ? state.active : (open[0] ?? null);
  set({ open, active });
}
