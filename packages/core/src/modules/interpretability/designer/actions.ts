/**
 * How a keybinding reaches the canvas.
 *
 * Module commands are declared at module load and run outside React, but the things
 * a Blender-style shortcut does — mute the selected node, enter the group under the
 * cursor, frame everything — live in the designer's component state. Karaoke solves
 * the same problem by keeping its state on the server; there is no server state to
 * reach here, so the pane publishes a handle instead and the commands call through
 * it.
 *
 * Two consequences worth stating, because both are load-bearing:
 *
 * - **The pane is a singleton**, so there is exactly one handle. A second designer
 *   would silently overwrite the first's, which is why the panel declares
 *   `singleton: true` and this file assumes it.
 * - **An unmounted designer means the commands do nothing.** That is the correct
 *   behaviour rather than a gap: the bindings are scoped to the pane anyway, and the
 *   architecture pane also hosts Inspect mode, where none of these verbs mean
 *   anything. The handle being absent *is* how "we are not in Design mode" is
 *   expressed — no mode flag to keep in step with the truth.
 *
 * Never install a `keydown` listener in a component: `packages/core/src/keymap/` is
 * the one keyboard authority. See docs/architecture/keybindings.mdx.
 */

export interface DesignerActions {
  /** Blender's Shift-A: focus the palette's search so a node can be added by name. */
  addNode(): void;
  /** Blender's M: ablation — the node emits nothing and its input passes through. */
  toggleMute(): void;
  /** Blender's X: remove the selected node and the wires into and out of it. */
  deleteSelected(): void;
  /** Blender's H: collapse the selected node to its title bar. */
  toggleCollapse(): void;
  /** Blender's Home: fit the whole graph in view. */
  frameAll(): void;
  /** Blender's Tab: step into the selected group. */
  enterGroup(): void;
  /** Blender's Tab again: back out to the level above. */
  exitGroup(): void;
  /** Blender's Ctrl-G: fold the selection into a group. */
  groupSelection(): void;
}

let live: DesignerActions | null = null;

/** Called by the designer on mount, and with `null` on unmount. */
export function bindDesigner(actions: DesignerActions | null): void {
  live = actions;
}

/** Run one action if a designer is mounted. A no-op otherwise, by design. */
export function designerAction(name: keyof DesignerActions): void {
  live?.[name]();
}

/** Whether a designer is currently listening — for tests, not for branching. */
export function designerIsLive(): boolean {
  return live !== null;
}
