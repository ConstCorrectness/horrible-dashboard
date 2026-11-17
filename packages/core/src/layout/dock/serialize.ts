/**
 * (De)serialization of the workbench layout — frame schema v4.
 *
 * Every older blob still loads, by running the steps in order: v1/v2 tiling and
 * floating blobs become windows (`../serialize.ts` + `legacy-frame.ts`), and
 * windows become docks (`migrate.ts`). A v4 blob is read here with the same pane
 * reader the older versions use, so a renamed or uninstalled view is handled by
 * one rule whatever version wrote it.
 *
 * As with v3, a blob from a *newer* version is rejected and the caller starts
 * empty: a downgrade discards the layout rather than misreading it. The flip to
 * v4 keeps a copy of the v3 blob for exactly that case (see persistence).
 */
import type { SerializedLayout } from '../../workspace';
import { areaId, createArea } from '../model';
import { createPaneReader, deserialize, FRAME_SCHEMA, readBackdrop } from '../serialize';
import type { PaneReader } from '../serialize';
import type { AreaNode, PaneState } from '../types';
import { DEFAULT_DOCK_SIZES, MIN_DOCK_SIZE, popoutId, sanitize } from './frame';
import { dockFrameFromWindows } from './migrate';
import type { MigrateOptions } from './migrate';
import { normalize, splitId } from './tree';
import { DOCK_SIDES } from './types';
import type {
  DockFrame,
  DockSide,
  DockState,
  LayoutNode,
  PopoutBounds,
  PopoutState,
} from './types';

export const DOCK_FRAME_VERSION = 4;

/** Popouts smaller than this were never usable and come back at a sane size. */
const MIN_POPOUT = 200;
/** Deeper than any tree a person builds by hand; stops a hostile blob recursing. */
const MAX_DEPTH = 32;

export function serializeDock(frame: DockFrame): SerializedLayout {
  // Ways of looking, not layout: restoring into a maximized group or a presented
  // pane hands the user a screen they have to guess their way out of.
  const persisted: Partial<DockFrame> = { ...frame };
  delete persisted.maximizedAreaId;
  delete persisted.presentedInstanceId;
  return {
    schema: FRAME_SCHEMA,
    version: DOCK_FRAME_VERSION,
    frame: persisted as unknown as Record<string, unknown>,
  };
}

/**
 * Parse any stored frame blob into a workbench, or null for a blob that is not
 * a frame layout or is too corrupt to salvage (callers start empty).
 */
export function deserializeDock(
  blob: SerializedLayout | null | undefined,
  knownViews: ReadonlySet<string>,
  opts: MigrateOptions,
): DockFrame | null {
  if (!blob || typeof blob !== 'object' || blob.schema !== FRAME_SCHEMA) return null;
  if (typeof blob.version !== 'number' || blob.version > DOCK_FRAME_VERSION) return null;
  if (blob.version < DOCK_FRAME_VERSION) {
    const windows = deserialize(blob, knownViews);
    return windows ? dockFrameFromWindows(windows, opts) : null;
  }
  const raw = blob.frame;
  if (!raw || typeof raw !== 'object') return null;
  try {
    return readV4(
      raw as Record<string, unknown>,
      createPaneReader(raw as Record<string, unknown>, knownViews),
    );
  } catch {
    return null;
  }
}

type Raw = Record<string, unknown>;

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function readV4(src: Raw, reader: PaneReader): DockFrame {
  // Ids that are missing or malformed are minted after everything else has been
  // read, from past the highest id seen, so a fresh one cannot collide.
  const pending: ((seq: () => number) => void)[] = [];
  const idOr = (value: unknown, assign: (id: string) => void, mint: (n: number) => string) => {
    if (typeof value === 'string' && value) {
      reader.trackNodeSeq(value);
      assign(value);
    } else {
      pending.push((next) => assign(mint(next())));
    }
  };

  const readArea = (value: unknown): AreaNode => {
    const a = (value && typeof value === 'object' ? value : {}) as Raw;
    const tabs = (Array.isArray(a.tabs) ? a.tabs : [])
      .map((t) => reader.readPane(t))
      .filter((p): p is PaneState => p !== null);
    const area: AreaNode = {
      ...createArea(''),
      tabs,
      activeTab: Math.min(Math.max(num(a.activeTab) ?? 0, 0), Math.max(tabs.length - 1, 0)),
    };
    idOr(a.id, (id) => (area.id = id), areaId);
    return area;
  };

  const readNode = (value: unknown, depth: number): LayoutNode | null => {
    if (!value || typeof value !== 'object' || depth > MAX_DEPTH) return null;
    const n = value as Raw;
    if (n.kind === 'area') {
      const area = readArea(n);
      // An emptied group is dropped; the tree is never left empty (see below).
      return area.tabs.length > 0 ? area : null;
    }
    if (n.kind !== 'split' || !Array.isArray(n.children)) return null;
    const children: LayoutNode[] = [];
    const sizes: number[] = [];
    n.children.forEach((child, i) => {
      const node = readNode(child, depth + 1);
      if (!node) return;
      children.push(node);
      const size = Array.isArray(n.sizes) ? num((n.sizes as unknown[])[i]) : null;
      sizes.push(size !== null && size > 0 ? size : 1);
    });
    if (children.length === 0) return null;
    const split: LayoutNode = {
      kind: 'split',
      id: '',
      orientation: n.orientation === 'column' ? 'column' : 'row',
      children,
      sizes,
    };
    idOr(n.id, (id) => (split.id = id), splitId);
    return split;
  };

  const readDock = (side: DockSide, value: unknown): DockState => {
    const d = (value && typeof value === 'object' ? value : {}) as Raw;
    const area = readArea(d.area);
    const size = num(d.size);
    return {
      visible: d.visible === true && area.tabs.length > 0,
      size: size !== null && size >= MIN_DOCK_SIZE ? Math.round(size) : DEFAULT_DOCK_SIZES[side],
      area,
    };
  };

  const readBounds = (value: unknown): PopoutBounds | null => {
    const b = value as Raw | undefined;
    const [x, y, w, h] = [num(b?.x), num(b?.y), num(b?.w), num(b?.h)];
    if (x === null || y === null || w === null || h === null) return null;
    return w >= MIN_POPOUT && h >= MIN_POPOUT ? { x, y, w, h } : null;
  };

  // Centre first: the pane reader dedupes instances first-come, and the centre
  // is where a pane the blob names twice is most likely to have been meant.
  const centerRead = readNode(src.center, 0);
  const docksSrc = (src.docks && typeof src.docks === 'object' ? src.docks : {}) as Raw;
  const docks = Object.fromEntries(
    DOCK_SIDES.map((side) => [side, readDock(side, docksSrc[side])]),
  ) as Record<DockSide, DockState>;

  const popouts: PopoutState[] = (Array.isArray(src.popouts) ? src.popouts : []).flatMap(
    (value): PopoutState[] => {
      if (!value || typeof value !== 'object') return [];
      const p = value as Raw;
      const area = readArea(p.area);
      if (area.tabs.length === 0) return [];
      const popout: PopoutState = {
        id: '',
        area,
        bounds: readBounds(p.bounds),
        maximized: p.maximized === true,
        ...(num(p.displayId) !== null ? { displayId: num(p.displayId)! } : {}),
      };
      idOr(p.id, (id) => (popout.id = id), popoutId);
      return [popout];
    },
  );

  let seq = Math.max(num(src.paneSeq) ?? 0, reader.maxSeq() + 1);
  const next = () => seq++;
  for (const assign of pending) assign(next);
  const center = centerRead ? normalize(centerRead) : createArea(areaId(next()));

  return sanitize({
    center,
    docks,
    popouts,
    backdrop: readBackdrop(src.backdrop),
    maximizedAreaId: null,
    presentedInstanceId: null,
    focusedInstanceId: typeof src.focusedInstanceId === 'string' ? src.focusedInstanceId : null,
    // Either may name something that did not survive the read; `sanitize`
    // resolves both against what did.
    focusedAreaId: typeof src.focusedAreaId === 'string' ? src.focusedAreaId : null,
    paneSeq: seq,
  });
}
