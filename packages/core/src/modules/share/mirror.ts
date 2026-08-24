/**
 * The redactor: turning this node's live `FrameState` into the projection a guest
 * is allowed to see.
 *
 * This is the security boundary of the semantic mirror, and it runs in the
 * **host's own browser** — which is the only place it can. Pane declarations live
 * in the frontend registry, so the backend has no idea what `editor.buffer` is or
 * whether it may be shared; and the guest's browser is the untrusted end, so
 * asking it to hide things would be asking the wrong machine.
 *
 * The rule is deny-by-default, applied twice:
 *
 * 1. A pane with no `share` declaration becomes a **redacted tile**. Its view id,
 *    its params and its instance title never enter the returned object, so they
 *    never reach the wire. What survives is the module's *manifest* title —
 *    "Editor", "Terminal" — which comes from the registry and so can never be a
 *    filename, a URL or a row id the way an instance title can.
 * 2. Params are an **allowlist**. A declared pane still gets nothing unless it
 *    named the keys, because params are routinely where the sensitive part lives.
 *
 * Everything here is pure and takes the registry lookup as an argument, so the
 * redaction can be tested without a registry, a socket or a DOM.
 */
import type { DockSide, PaneShareDecl } from '@horribledashboard/sdk';

import type {
  AreaNode,
  DesktopMode,
  FrameState,
  LayoutNode,
  PaneState,
  WindowRect,
} from '../../layout/types';

/** What a guest is told about one pane. */
export interface MirrorPane {
  instanceId: string;
  /** The module's manifest title. Never an instance title. */
  title: string;
  /**
   * `redacted` is the default and means exactly that: this pane exists and takes
   * up this much room, and that is all you are being told about it.
   */
  mode: 'collab' | 'mirror' | 'pixels' | 'redacted';
  /** Present only for a declared pane — a redacted tile has no view id at all. */
  viewId?: string;
  /** Only the keys the pane's declaration allowlisted. */
  params?: Record<string, unknown>;
  minimized?: boolean;
}

export interface MirrorArea {
  kind: 'area';
  id: string;
  tabs: MirrorPane[];
  activeTab: number;
}

export interface MirrorSplit {
  kind: 'split';
  id: string;
  orientation: 'row' | 'column';
  children: MirrorNode[];
  sizes: number[];
}

export type MirrorNode = MirrorSplit | MirrorArea;

export interface MirrorDock {
  visible: boolean;
  tools: MirrorPane[];
  activeTool: string | null;
}

export interface MirrorWindow {
  id: string;
  area: MirrorArea;
  rect: WindowRect;
  minimized: boolean;
}

/** The host's workspace as a guest sees it. */
export interface MirrorFrame {
  center: MirrorNode;
  docks: Record<DockSide, MirrorDock>;
  windows: MirrorWindow[];
  windowViewport: { w: number; h: number } | null;
  mode: DesktopMode;
  /** What the host is looking at, for follow mode. */
  focusedInstanceId: string | null;
  /** How many panes were withheld — shown to the guest as a fact, not hidden. */
  redactedCount: number;
}

/** What the redactor needs to know about a view. Supplied by the caller. */
export interface ViewShareInfo {
  title: string;
  share?: PaneShareDecl;
}

export type ViewLookup = (viewId: string) => ViewShareInfo | undefined;

/**
 * A pane whose view is not registered at all.
 *
 * Reached when a workspace holds a pane from a module that has since been
 * removed. It is redacted like anything else — an unknown view is exactly the
 * case where we cannot know whether it may be shared, and "unknown" must resolve
 * to "no".
 */
const UNKNOWN_TITLE = 'Pane';

/**
 * An opaque, stable stand-in for a redacted pane's instance id.
 *
 * A pane's instance id is built as viewId + "#" + n (see `layout/model.ts`), so
 * dropping the `viewId` field while forwarding the id leaves the view id in the
 * payload anyway — exactly the leak this module exists to prevent, hiding in the
 * one field that looked like plumbing.
 *
 * Hashing keeps what the guest actually needs: an id that is **stable across
 * projections**, so React keys do not churn and `focusedInstanceId` still matches
 * a tile. FNV-1a because this is obfuscation, not a secret — the module *title*
 * is disclosed by design, so a determined guest already knows which modules the
 * host runs. What it buys is that the invariant holds literally, and that
 * tightening the disclosure later needs no second fix.
 */
function opaqueId(instanceId: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < instanceId.length; i += 1) {
    hash ^= instanceId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return `r:${(hash >>> 0).toString(36)}`;
}

function pickParams(
  params: Record<string, unknown> | undefined,
  allow: string[] | undefined,
): Record<string, unknown> | undefined {
  if (!params || !allow || allow.length === 0) return undefined;
  const out: Record<string, unknown> = {};
  for (const key of allow) {
    if (Object.prototype.hasOwnProperty.call(params, key)) out[key] = params[key];
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Accumulates the redacted count and the instance-id remapping as we walk. */
interface Walk {
  n: number;
  /** Real instance id -> the id the guest sees. Only redacted panes are remapped. */
  ids: Map<string, string>;
}

function redactPane(pane: PaneState, lookup: ViewLookup, walk: Walk): MirrorPane {
  const info = lookup(pane.viewId);
  const decl = info?.share;
  const title = info?.title ?? UNKNOWN_TITLE;

  if (!decl) {
    walk.n += 1;
    const id = opaqueId(pane.instanceId);
    walk.ids.set(pane.instanceId, id);
    // Deliberately constructed field by field rather than spread-and-delete: a
    // spread would carry every future `PaneState` field into the payload by
    // default, which is the failure mode this whole function exists to prevent.
    return { instanceId: id, title, mode: 'redacted' };
  }

  walk.ids.set(pane.instanceId, pane.instanceId);
  return {
    instanceId: pane.instanceId,
    title,
    mode: decl.mode,
    viewId: pane.viewId,
    params: pickParams(pane.params, decl.params),
    minimized: pane.minimized,
  };
}

function redactArea(area: AreaNode, lookup: ViewLookup, walk: Walk): MirrorArea {
  return {
    kind: 'area',
    id: area.id,
    tabs: area.tabs.map((t) => redactPane(t, lookup, walk)),
    activeTab: area.activeTab,
  };
}

function redactNode(node: LayoutNode, lookup: ViewLookup, walk: Walk): MirrorNode {
  if (node.kind === 'area') return redactArea(node, lookup, walk);
  return {
    kind: 'split',
    id: node.id,
    orientation: node.orientation,
    children: node.children.map((c) => redactNode(c, lookup, walk)),
    sizes: node.sizes,
  };
}

/**
 * Project a frame into what a guest may see.
 *
 * `backdrop` is dropped rather than forwarded: a backdrop ref can carry params
 * (a wallpaper path), and a guest looking at a structural map has no use for it.
 * `presentedInstanceId` is dropped because it is not serialized anywhere else
 * either — it is a way of looking at a pane for a moment, not a property of the
 * workspace.
 */
export function redactFrame(frame: FrameState, lookup: ViewLookup): MirrorFrame {
  const walk: Walk = { n: 0, ids: new Map() };
  const center = redactNode(frame.center, lookup, walk);

  const docks = {} as Record<DockSide, MirrorDock>;
  for (const side of ['left', 'right', 'bottom'] as const) {
    const dock = frame.docks[side];
    docks[side] = {
      visible: dock.visible,
      tools: dock.tools.map((t) => redactPane(t, lookup, walk)),
      // `activeTool` is an instance id too, and so carries the same view id.
      activeTool: dock.activeTool ? (walk.ids.get(dock.activeTool) ?? null) : null,
    };
  }

  const windows = frame.windows.map((w) => ({
    id: w.id,
    area: redactArea(w.area, lookup, walk),
    rect: w.rect,
    minimized: w.mode === 'minimized',
  }));

  return {
    center,
    docks,
    windows,
    windowViewport: frame.windowViewport,
    mode: frame.mode,
    // Mapped through the same table: the host may well be looking at a pane the
    // guest cannot see, and forwarding the raw id there would put the view id
    // back into the payload by the side door.
    focusedInstanceId: frame.focusedInstanceId
      ? (walk.ids.get(frame.focusedInstanceId) ?? null)
      : null,
    redactedCount: walk.n,
  };
}

/**
 * What the host is told about their own projection.
 *
 * Counted here and sent, rather than derived on the server: the backend holds
 * the projection opaquely on purpose, and a server that walks its tree to count
 * panes is one refactor away from a server that filters them.
 */
export interface MirrorSummary {
  panes: number;
  hidden: number;
}

/** Summarize a projection for the host's own session pane. */
export function summarize(frame: MirrorFrame): MirrorSummary {
  return { panes: mirrorPanes(frame).length, hidden: frame.redactedCount };
}

/** Every pane in a projection, flattened — for counting and for tests. */
export function mirrorPanes(frame: MirrorFrame): MirrorPane[] {
  const out: MirrorPane[] = [];
  const walk = (node: MirrorNode): void => {
    if (node.kind === 'area') out.push(...node.tabs);
    else node.children.forEach(walk);
  };
  walk(frame.center);
  for (const side of ['left', 'right', 'bottom'] as const) out.push(...frame.docks[side].tools);
  for (const win of frame.windows) out.push(...win.area.tabs);
  return out;
}

/**
 * Whether two projections differ in anything a guest would see.
 *
 * The layout store fires on every pixel of a drag, and a resize that moves a
 * split by 0.4% is not news. Comparing the *projection* rather than the frame
 * also means the common case — the host typing in a pane the guest cannot see —
 * produces no traffic at all.
 */
export function mirrorChanged(a: MirrorFrame | null, b: MirrorFrame): boolean {
  if (a === null) return true;
  return JSON.stringify(a) !== JSON.stringify(b);
}
