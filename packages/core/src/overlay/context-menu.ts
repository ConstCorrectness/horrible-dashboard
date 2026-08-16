/**
 * The context-menu model: **what** was right-clicked, and who gets to add items
 * to it.
 *
 * Before this there were four hand-rolled menus (the file tree, the activity rail,
 * the workspace tabs, the game panel), each with its own dismissal effect, its own
 * positioning, and a fixed item list baked into the component that owned the
 * pixels. That makes "right-click acts on what is under the cursor" a thing every
 * component reimplements, and it means a module can never offer an action on
 * another module's surface — the file tree cannot know that the notebook module
 * would like an "Open as notebook" entry on a `.ipynb`.
 *
 * So a right-click reports a **target** (a `kind` plus whatever that kind carries)
 * and the menu is assembled from every provider registered for that kind, in
 * module registration order, one group each. The component that owns the pixels
 * decides what the target *is*; it no longer decides what can be done to it.
 *
 * Rendering, placement, and keyboard handling live in `packages/ui` — see
 * `ContextMenu.tsx` and `placement.ts`.
 */
import type { ReactNode } from 'react';

import { registry } from '../registry';

/**
 * What was right-clicked. `kind` selects the providers; everything else is that
 * kind's payload, read only by providers that understand it.
 *
 * Kinds are strings rather than a closed union so a plugin can introduce its own
 * surface. The built-in ones are listed in `docs/architecture/context-menus.mdx`.
 */
export interface ContextTarget {
  kind: string;
  [key: string]: unknown;
}

export interface ContextMenuItem {
  id: string;
  label: ReactNode;
  /** Runs on click/Enter. The menu closes first, so a dialog can take focus. */
  run: () => void | Promise<void>;
  /** Rendered muted and unselectable; keyboard nav skips it. */
  disabled?: boolean;
  /** Destructive styling — delete, disconnect, reset. */
  danger?: boolean;
  /** Draws a check column. `undefined` means "not a toggle" and draws nothing. */
  checked?: boolean;
  /** A short right-aligned hint: a keybinding, a target path. */
  hint?: string;
  /**
   * A sentence about what this item *is*, rendered on its own line under the
   * label rather than beside it.
   *
   * Distinct from `hint` because the two are different shapes and the row can
   * only lay out one of them well: a hint is a few characters and shares the
   * row, while a detail is prose. Passing prose as a hint is what put a theme's
   * whole description in the right-hand column and squeezed "Midnight" down to
   * an ellipsis — the picker then named none of the things it was picking from.
   */
  detail?: string;
  /** Nested items. A submenu with no items is dropped rather than shown empty. */
  submenu?: ContextMenuItem[];
}

/** One module's contribution to one or more target kinds. */
export interface ContextMenuProvider {
  /** Target kind(s) this provider answers for. */
  kind: string | string[];
  /**
   * Items for a specific target. Return `[]` (not a disabled item) for something
   * that can never apply to this target — the file tree's virtual roots have no
   * directory behind them, so "New File" is absent rather than greyed, because
   * there is no state in which it becomes available.
   */
  items: (target: ContextTarget) => ContextMenuItem[];
  /**
   * Ordering hint; lower sorts first. The owning module's own items should come
   * first (0), foreign contributions after. Ties keep registration order.
   */
  order?: number;
}

type Listener = () => void;

interface Registration extends ContextMenuProvider {
  seq: number;
}

const providers: Registration[] = [];
let seq = 0;

/** Register a provider. Returns an unregister function. */
export function addContextMenuProvider(provider: ContextMenuProvider): () => void {
  const reg: Registration = { ...provider, seq: seq++ };
  providers.push(reg);
  return () => {
    const i = providers.indexOf(reg);
    if (i >= 0) providers.splice(i, 1);
  };
}

/** Test-only: drop every provider. */
export function resetContextMenuProviders(): void {
  providers.length = 0;
}

function matches(provider: ContextMenuProvider, kind: string): boolean {
  return Array.isArray(provider.kind) ? provider.kind.includes(kind) : provider.kind === kind;
}

/**
 * Every item for `target`, as groups — one per contributing provider, in
 * `order` then registration order. Empty groups are dropped, so a provider that
 * declines a particular target costs nothing and never leaves a stray separator.
 *
 * Grouping is returned rather than flattened because the separator between two
 * modules' items is the only cue that they came from different places, and a
 * flattened list makes "delete" from one module sit flush against "open" from
 * another as though they belonged together.
 */
export function itemsForTarget(target: ContextTarget): ContextMenuItem[][] {
  // Module manifests first (the declarative path, and the order modules registered
  // in), then anything added imperatively — a pane registering for its own live
  // instance, or a plugin loaded after boot.
  const all: Registration[] = [
    ...registry.contextMenuProviders.map((p, i) => ({ ...p, seq: i - 1e6 })),
    ...providers,
  ];
  return all
    .filter((p) => matches(p, target.kind))
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.seq - b.seq)
    .map((p) => {
      try {
        return p.items(target).filter((item) => !(item.submenu && item.submenu.length === 0));
      } catch (err) {
        // One module's bad provider must not take down the whole menu — the user
        // still gets everyone else's items, and the failure is visible in console.
        console.error(`context menu provider for "${target.kind}" threw`, err);
        return [];
      }
    })
    .filter((group) => group.length > 0);
}

// --- the open menu ----------------------------------------------------------

export interface OpenContextMenu {
  /** Viewport coordinates of the click. */
  x: number;
  y: number;
  target: ContextTarget;
  groups: ContextMenuItem[][];
}

let open: OpenContextMenu | null = null;
const listeners = new Set<Listener>();

function emit(): void {
  listeners.forEach((l) => l());
}

export const contextMenuStore = {
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot(): OpenContextMenu | null {
    return open;
  },
};

/**
 * Open the context menu for `target` at a click position.
 *
 * Resolves items **now** rather than at render, so the menu describes the state at
 * the moment of the click. Returns false and opens nothing when no provider
 * offered an item — the caller should then let the event through to the browser's
 * native menu rather than swallowing the gesture and showing an empty box.
 */
export function openContextMenu(
  at: { clientX: number; clientY: number },
  target: ContextTarget,
): boolean {
  const groups = itemsForTarget(target);
  if (groups.length === 0) {
    open = null;
    emit();
    return false;
  }
  open = { x: at.clientX, y: at.clientY, target, groups };
  emit();
  return true;
}

export function closeContextMenu(): void {
  if (!open) return;
  open = null;
  emit();
}
