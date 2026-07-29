/**
 * Transient chrome — popovers, context menus, dropdowns — registered so the
 * shell's Escape ladder can close the top one.
 *
 * Each of these used to install its own `window` keydown listener that closed on
 * *any* key. That worked, but it meant Escape had four uncoordinated owners and
 * no rung could tell whether an earlier one had already handled the press. A
 * stack keeps the obvious rule: Escape closes the thing you opened last.
 */
const stack: { id: symbol; close: () => void }[] = [];

/** Register an open popover. Call the returned function when it closes. */
export function registerTransient(close: () => void): () => void {
  const id = Symbol('transient');
  stack.push({ id, close });
  return () => {
    const at = stack.findIndex((t) => t.id === id);
    if (at >= 0) stack.splice(at, 1);
  };
}

/** Close the most recently opened transient. Returns false when none is open. */
export function closeTransientChrome(): boolean {
  const top = stack.pop();
  if (!top) return false;
  top.close();
  return true;
}

export function hasTransientChrome(): boolean {
  return stack.length > 0;
}
