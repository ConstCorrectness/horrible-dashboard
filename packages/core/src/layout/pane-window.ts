/**
 * Which **OS window** a pane is in.
 *
 * A popped-out group is rendered by the main page into a child window's document
 * (`PopoutHost.tsx`): one JavaScript realm, two documents. So the globals a pane
 * reaches for — `window`, `document`, `document.body`, `innerWidth` — name the
 * *main* window even while the pane is on screen in another. A pane that portals a
 * dropdown to `document.body`, measures against `window.innerWidth`, or listens on
 * `window` for a drag would do all of it in the wrong window.
 *
 * `usePaneWindow()` is the answer for a pane: the window it is in now. Outside a
 * pane, or in a build without pop-outs, it is `window`. `windowOf(node)` is the
 * same question asked of a DOM node, for code with an element but no React.
 *
 * The registry below is the set of pop-out windows open right now, for the few
 * shell-wide listeners (the keymap, focus tracking) that must attach to each.
 */
import { createContext, useContext } from 'react';

export interface PaneWindow {
  win: Window;
  /** The pop-out the pane is in, or null for the main window. */
  popoutId: string | null;
}

export const PaneWindowContext = createContext<PaneWindow | null>(null);

/** The window the calling pane is in. `window` outside a pop-out. */
export function usePaneWindow(): Window {
  return useContext(PaneWindowContext)?.win ?? window;
}

/** The pop-out the calling pane is in, or null in the main window. */
export function usePanePopoutId(): string | null {
  return useContext(PaneWindowContext)?.popoutId ?? null;
}

/** The window a node is in — its document's, else the main one. */
export function windowOf(node: Node | null | undefined): Window {
  return node?.ownerDocument?.defaultView ?? window;
}

// --- open pop-out windows ------------------------------------------------------

type Attacher = (win: Window) => () => void;

const popouts = new Map<string, Window>();
const attachers = new Set<Attacher>();
/** Per window, what each attacher left to undo there. */
const detachers = new Map<Window, Map<Attacher, () => void>>();

/**
 * Record an open pop-out window and run every {@link onEachPopoutWindow} attacher
 * on it. Returns the unregister, which runs their detachers.
 */
export function registerPopoutWindow(id: string, win: Window): () => void {
  popouts.set(id, win);
  detachers.set(win, new Map([...attachers].map((attach) => [attach, attach(win)])));
  return () => {
    if (popouts.get(id) === win) popouts.delete(id);
    for (const detach of detachers.get(win)?.values() ?? []) detach();
    detachers.delete(win);
  };
}

/** The pop-out window for `id`, if open. */
export function popoutWindow(id: string): Window | null {
  const win = popouts.get(id);
  return win && !win.closed ? win : null;
}

/**
 * Run `attach` on every pop-out window, now and as they open; its return value
 * runs when that window goes. Returns a stop that detaches from all of them.
 */
export function onEachPopoutWindow(attach: Attacher): () => void {
  attachers.add(attach);
  for (const win of popouts.values()) detachers.get(win)?.set(attach, attach(win));
  return () => {
    attachers.delete(attach);
    for (const perWindow of detachers.values()) {
      perWindow.get(attach)?.();
      perWindow.delete(attach);
    }
  };
}

/** The main document, then every open pop-out's. */
export function allDocuments(): Document[] {
  if (typeof document === 'undefined') return [];
  return [document, ...[...popouts.values()].filter((w) => !w.closed).map((w) => w.document)];
}

/**
 * The document holding keyboard focus: a pop-out's when one has it, else the
 * main one. `document.activeElement` alone reads `<body>` while the user types in
 * a pop-out, which would let bare-letter shortcuts fire into a text field there.
 */
export function focusedDocument(): Document {
  if (typeof document === 'undefined') return undefined as unknown as Document;
  if (document.hasFocus()) return document;
  for (const win of popouts.values()) {
    if (!win.closed && win.document.hasFocus()) return win.document;
  }
  return document;
}
