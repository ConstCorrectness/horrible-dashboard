/**
 * Keybinding resolution with **focus scopes**. Bindings are either global (active
 * everywhere) or scoped to a pane view id (active only while a pane of that view
 * is focused). The shell tracks the focused pane's view id as the *active scope*
 * (set by the workspace's pane host); the keydown handler resolves a pressed key
 * to a command with this precedence:
 *
 *   1. a global binding marked `override` (must never be shadowed — the palette)
 *   2. a binding scoped to the focused pane (a focused pane shadows globals)
 *   3. a plain global binding
 *
 * So most globals are overridable by the focused pane, but an `override` global
 * always wins — the "may or may not override" the user asked for. The pure
 * `resolveKeybinding` makes this testable without the DOM. See
 * docs/architecture/layout-shell.md.
 */
import type { KeybindingDecl } from './registry';

// The view id of the pane the user is currently working in (e.g. 'editor.buffer',
// 'terminal.instance'), or null on the home view / before any pane is focused.
let activeScope: string | null = null;

/** Mark a pane view as focused, so its scoped bindings become active. */
export function setActiveScope(scope: string | null): void {
  activeScope = scope;
}

/** Clear the active scope only if it still points at `scope` (call on unmount). */
export function clearActiveScope(scope: string): void {
  if (activeScope === scope) activeScope = null;
}

/** The currently focused pane's view id, or null. */
export function getActiveScope(): string | null {
  return activeScope;
}

/**
 * Is the event target a text-entry element? Plain-letter (no-modifier) shortcuts
 * must defer to typing when one of these is focused. Shared by the global keydown
 * dispatch and any component that binds bare keys (e.g. the pane-group shell).
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
}

/** Does a keyboard event match a `mod+x` / `x` key spec? (`mod` = ctrl or cmd). */
export function matchesKeySpec(e: KeyboardEvent, key: string): boolean {
  const wantsMod = key.startsWith('mod+');
  const plain = wantsMod ? key.slice(4) : key;
  const hasMod = e.ctrlKey || e.metaKey;
  return wantsMod === hasMod && e.key.toLowerCase() === plain.toLowerCase();
}

/**
 * Resolve a keydown to a command id given the focused `scope`, or null if nothing
 * binds the key. Precedence: override-global → focused-scope → plain global.
 */
export function resolveKeybinding(
  e: KeyboardEvent,
  scope: string | null,
  bindings: readonly KeybindingDecl[],
): string | null {
  const matching = bindings.filter((b) => matchesKeySpec(e, b.key));
  if (matching.length === 0) return null;

  const overrideGlobal = matching.find((b) => !b.scope && b.override);
  if (overrideGlobal) return overrideGlobal.command;

  if (scope) {
    const scoped = matching.find((b) => b.scope === scope);
    if (scoped) return scoped.command;
  }

  const global = matching.find((b) => !b.scope);
  return global ? global.command : null;
}
