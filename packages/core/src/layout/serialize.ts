/**
 * (De)serialization of the frame layout. The backend stores the result opaquely
 * (`SerializedLayout` blob per workspace), so this module owns the schema:
 * a versioned envelope `{ schema: 'horrible.frame', version, frame }`.
 * `deserialize` is defensive — it validates shapes, prunes panes whose view ids
 * are no longer registered (uninstalled plugin, renamed pane), and returns null
 * for anything that isn't a frame blob (e.g. a legacy dockview layout), which
 * callers treat as "reseed from preset".
 */
import type { SerializedLayout } from '../workspace';
import {
  areaId,
  createDock,
  createEmptyFrame,
  findArea,
  firstArea,
  normalize,
  windowId,
} from './model';
import { isSnapZone } from './snap';
import { DEFAULT_BACKDROP } from './types';
import type {
  AreaNode,
  BackdropRef,
  DesktopMode,
  DockSide,
  DockState,
  FrameState,
  LayoutNode,
  PaneState,
  RegionPosition,
  RegionState,
  WindowMode,
  WindowRect,
  WindowState,
} from './types';

export const FRAME_SCHEMA = 'horrible.frame';
/**
 * v2 added the desktop shell: `floating[]` (fractions of the center grid) became
 * `windows[]` (pixels against `windowViewport`), plus `mode` and `backdrop`.
 *
 * The guard in `deserialize` rejects a blob whose version is *higher* than this, so
 * an older build reading a v2 blob reseeds from the preset — i.e. **downgrading
 * discards the layout rather than corrupting it**. That is the intended trade; the
 * alternative is an old build interpreting pixel rects as fractions and putting every
 * window in the top-left few pixels.
 */
export const FRAME_VERSION = 2;

/** Where a v2 window with an unreadable rect lands. */
const DEFAULT_WINDOW_RECT: WindowRect = { x: 60, y: 48, w: 720, h: 480 };
/** The v1 default, in the old fractional basis. */
const LEGACY_RECT: WindowRect = { x: 0.2, y: 0.15, w: 0.5, h: 0.55 };

const DOCK_SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];
const REGION_POSITIONS: readonly RegionPosition[] = ['left', 'right', 'bottom'];

/**
 * Views that were merged or renamed away, mapped to what replaced them.
 *
 * A saved layout outlives the code that wrote it: without this, a workspace that
 * still names a retired view just loses that pane, and a user who had the old
 * layout opens their workspace to holes where their panes used to be. Renaming
 * here means their layout keeps working across the change.
 *
 * `games.board` / `games.loadout` became sections of the merged `games.lobby`
 * pane, and `games.thoughts` became a stream of `games.log` (see
 * modules/games/hub-section.ts). `interpretability.budget` was a compact widget
 * duplicating the header of `interpretability.context`; the budget bar now lives
 * in that panel. Duplicates that result from two old views collapsing onto one
 * are dropped by `readPane`.
 */
const RENAMED_VIEWS: Readonly<Record<string, string>> = {
  'games.board': 'games.lobby',
  'games.loadout': 'games.lobby',
  'games.thoughts': 'games.log',
  'interpretability.budget': 'interpretability.context',
};

export function serialize(frame: FrameState): SerializedLayout {
  // `presentedInstanceId` is dropped rather than written and ignored on the way
  // back. It is a momentary way of looking at a pane, not a property of the
  // workspace — and a blob carrying it would look, to anyone reading one, like a
  // state the loader is supposed to restore.
  const persisted: Partial<FrameState> = { ...frame };
  delete persisted.presentedInstanceId;
  return {
    schema: FRAME_SCHEMA,
    version: FRAME_VERSION,
    frame: persisted as unknown as Record<string, unknown>,
  };
}

/**
 * Parse a stored layout blob into a FrameState, or null when the blob is not a
 * frame layout (wrong schema — including legacy dockview blobs) or too corrupt
 * to salvage. `knownViews` filters out panes/regions whose views no longer
 * exist. A future `version` bump adds a `migrate(v)` step here.
 *
 * `undockableViews` drops **dock** entries for views that can no longer be docked
 * at all (`dockSidesOf` returns `[]`) — a view since marked `embedded`, or one
 * whose `role` changed from `tool` to `document`. Either way the saved dock entry
 * is a state today's code cannot produce: for an embedded view it would resurrect
 * exactly the second, competing home that embedding removes, and for a document
 * view it would pin a centre-only pane into a dock no opener would ever put it in
 * again. It only filters docks: a *centre* pane or a region is a placement the
 * user made deliberately and `openPaneInArea` still supports.
 */
export function deserialize(
  blob: SerializedLayout | null | undefined,
  knownViews: ReadonlySet<string>,
  undockableViews: ReadonlySet<string> = new Set(),
): FrameState | null {
  if (!blob || typeof blob !== 'object') return null;
  if (blob.schema !== FRAME_SCHEMA) return null;
  if (typeof blob.version !== 'number' || blob.version > FRAME_VERSION) return null;
  const raw = blob.frame;
  if (!raw || typeof raw !== 'object') return null;

  try {
    const src = raw as Record<string, unknown>;
    let maxSeq = 0;
    const seenInstances = new Set<string>();
    // Views a rename has already landed on, so two retired views collapsing onto
    // one replacement (games.board + games.loadout → games.lobby) leave one pane,
    // not two of the same. A pane the layout already names natively wins over a
    // renamed one — it's the instance the user actually placed.
    const nativeViews = collectViewIds(src);
    const renamedInto = new Set<string>();

    const readPane = (value: unknown): PaneState | null => {
      if (!value || typeof value !== 'object') return null;
      const p = value as Record<string, unknown>;
      if (typeof p.instanceId !== 'string' || typeof p.viewId !== 'string') return null;
      const renamed = RENAMED_VIEWS[p.viewId];
      const viewId = renamed ?? p.viewId;
      if (!knownViews.has(viewId)) return null;
      if (renamed !== undefined) {
        if (nativeViews.has(renamed) || renamedInto.has(renamed)) return null;
        renamedInto.add(renamed);
      }
      if (seenInstances.has(p.instanceId)) return null;
      seenInstances.add(p.instanceId);
      const seqPart = Number(p.instanceId.split('#').pop());
      if (Number.isFinite(seqPart)) maxSeq = Math.max(maxSeq, seqPart);
      const pane: PaneState = { instanceId: p.instanceId, viewId };
      if (p.params && typeof p.params === 'object') {
        pane.params = p.params as Record<string, unknown>;
      }
      // Optional and additive: a blob written before per-tool sizing simply has
      // none, and the pane falls back to its dock's size. No version bump needed.
      if (typeof p.dockSize === 'number' && Number.isFinite(p.dockSize) && p.dockSize >= 48) {
        pane.dockSize = p.dockSize;
      }
      const regions = readRegions(p.regions);
      if (regions) pane.regions = regions;
      // Kept as written, without checking it against the view's declarations:
      // this module is deliberately registry-free. A section that has since been
      // removed is resolved away by `activeSectionOf`, which has to handle that
      // case regardless (sections can disappear while a pane is open).
      if (typeof p.activeSection === 'string') pane.activeSection = p.activeSection;
      // Additive, like `dockSize`: an older blob simply has none and the pane
      // comes back showing. Persisted rather than cleared on load because a
      // minimized pane is still the user's arrangement — restoring the workspace
      // with everything they had put away popped back open would be the frame
      // undoing their tidying every reload.
      if (p.minimized === true) pane.minimized = true;
      return pane;
    };

    const readRegions = (value: unknown): PaneState['regions'] => {
      if (!value || typeof value !== 'object') return undefined;
      const out: PaneState['regions'] = {};
      for (const pos of REGION_POSITIONS) {
        const r = (value as Record<string, unknown>)[pos];
        if (!r || typeof r !== 'object') continue;
        const region = r as Record<string, unknown>;
        if (!Array.isArray(region.views)) continue;
        const views = [
          ...new Set(
            region.views
              .filter((v): v is string => typeof v === 'string')
              .map((v) => RENAMED_VIEWS[v] ?? v)
              .filter((v) => knownViews.has(v)),
          ),
        ];
        if (views.length === 0) continue;
        const rawActive =
          typeof region.activeView === 'string'
            ? (RENAMED_VIEWS[region.activeView] ?? region.activeView)
            : null;
        const activeView = rawActive && views.includes(rawActive) ? rawActive : views[0];
        const state: RegionState = {
          open: region.open === true,
          size: clampSize(region.size, 260),
          collapsed: region.collapsed === true,
          views,
          activeView,
        };
        out[pos] = state;
      }
      return Object.keys(out).length ? out : undefined;
    };

    const readNode = (value: unknown): LayoutNode | null => {
      if (!value || typeof value !== 'object') return null;
      const n = value as Record<string, unknown>;
      if (n.kind === 'area') {
        if (typeof n.id !== 'string') return null;
        trackNodeSeq(n.id);
        const rawTabs = Array.isArray(n.tabs) ? n.tabs : [];
        const tabs = rawTabs.map(readPane).filter((p): p is PaneState => p !== null);
        const area: AreaNode = {
          kind: 'area',
          id: n.id,
          tabs,
          activeTab: Math.min(
            typeof n.activeTab === 'number' && n.activeTab >= 0 ? n.activeTab : 0,
            Math.max(tabs.length - 1, 0),
          ),
        };
        if (n.headerCollapsed === true) area.headerCollapsed = true;
        return area;
      }
      if (n.kind === 'split') {
        if (typeof n.id !== 'string') return null;
        if (n.orientation !== 'row' && n.orientation !== 'column') return null;
        if (!Array.isArray(n.children)) return null;
        trackNodeSeq(n.id);
        const children = n.children.map(readNode).filter((c): c is LayoutNode => c !== null);
        if (children.length === 0) return null;
        const sizes = Array.isArray(n.sizes)
          ? children.map((_, i) => {
              const s = (n.sizes as unknown[])[i];
              return typeof s === 'number' && s > 0 ? s : 1 / children.length;
            })
          : children.map(() => 1 / children.length);
        return { kind: 'split', id: n.id, orientation: n.orientation, children, sizes };
      }
      return null;
    };

    // `w` joins `a`/`s`: window ids draw from the same counter, so a restored
    // layout must not mint a window id that already exists.
    const trackNodeSeq = (id: string): void => {
      const m = /^[asw](\d+)$/.exec(id);
      if (m) maxSeq = Math.max(maxSeq, Number(m[1]));
    };

    const fallback = createEmptyFrame();
    const read = readNode(src.center);
    const center = normalize((read && pruneEmptyAreas(read)) ?? fallback.center);

    const docks = {} as Record<DockSide, DockState>;
    for (const side of DOCK_SIDES) {
      const d = (src.docks as Record<string, unknown> | undefined)?.[side];
      if (!d || typeof d !== 'object') {
        docks[side] = createDock(side);
        continue;
      }
      const dock = d as Record<string, unknown>;
      const tools = (Array.isArray(dock.tools) ? dock.tools : [])
        .map(readPane)
        .filter((p): p is PaneState => p !== null && !undockableViews.has(p.viewId));
      const activeTool =
        typeof dock.activeTool === 'string' && tools.some((t) => t.instanceId === dock.activeTool)
          ? dock.activeTool
          : (tools[0]?.instanceId ?? null);
      docks[side] = {
        visible: dock.visible === true && tools.length > 0,
        size: clampSize(dock.size, createDock(side).size),
        tools,
        activeTool,
      };
    }

    const num = (v: unknown, d: number): number =>
      typeof v === 'number' && Number.isFinite(v) ? v : d;
    const readRect = (v: unknown, d: WindowRect): WindowRect => {
      const r = v as Record<string, unknown> | undefined;
      return { x: num(r?.x, d.x), y: num(r?.y, d.y), w: num(r?.w, d.w), h: num(r?.h, d.h) };
    };

    // v2 windows, or v1 `floating` migrated in. A v1 rect is a FRACTION of the old
    // center grid, so it is read as-is and paired with a 1×1 `windowViewport`: the
    // ordinary rescale-on-measure path then multiplies it up to real pixels on the
    // first `SET_WINDOW_VIEWPORT`, with no migration-only code to get wrong.
    const legacy = blob.version < 2;
    const rawWindows = Array.isArray(src.windows)
      ? src.windows
      : legacy && Array.isArray(src.floating)
        ? src.floating
        : [];
    const windows: WindowState[] = rawWindows
      .map((w, index): WindowState | null => {
        if (!w || typeof w !== 'object') return null;
        const entry = w as Record<string, unknown>;
        // v1: `{ pane, rect, z }`. v2: `{ id, area, rect, mode, snap, z }`.
        const areaSrc = entry.area as Record<string, unknown> | undefined;
        const tabs = (Array.isArray(areaSrc?.tabs) ? areaSrc.tabs : [entry.pane])
          .map(readPane)
          .filter((p): p is PaneState => p !== null);
        // A window whose every tab named an uninstalled view is dropped whole,
        // rather than left as a titlebar with nothing under it.
        if (tabs.length === 0) return null;
        // Generated ids are derived from the entry's index, not a shared counter:
        // a legacy blob supplies neither id, and two entries that both carried a
        // `z` would otherwise be handed the same one.
        const base = maxSeq + 1 + index * 2;
        const id = typeof entry.id === 'string' ? entry.id : windowId(base);
        const aid = typeof areaSrc?.id === 'string' ? areaSrc.id : areaId(base + 1);
        trackNodeSeq(id);
        trackNodeSeq(aid);
        const mode: WindowMode =
          entry.mode === 'minimized' || entry.mode === 'maximized' ? entry.mode : 'normal';
        const snap = isSnapZone(entry.snap) ? entry.snap : undefined;
        return {
          id,
          area: {
            kind: 'area',
            id: aid,
            tabs,
            activeTab: Math.min(Math.max(num(areaSrc?.activeTab, 0), 0), tabs.length - 1),
            ...(areaSrc?.headerCollapsed === true ? { headerCollapsed: true } : {}),
          },
          rect: readRect(entry.rect, legacy ? LEGACY_RECT : DEFAULT_WINDOW_RECT),
          ...(entry.restoreRect
            ? { restoreRect: readRect(entry.restoreRect, DEFAULT_WINDOW_RECT) }
            : {}),
          mode,
          ...(snap ? { snap } : {}),
          z: num(entry.z, index + 1),
        };
      })
      .filter((w): w is WindowState => w !== null);

    const rawViewport = src.windowViewport as Record<string, unknown> | undefined;
    const windowViewport = legacy
      ? // See above: unit basis, so old fractions become pixels on first measure.
        windows.length > 0
        ? { w: 1, h: 1 }
        : null
      : rawViewport && num(rawViewport.w, 0) > 0 && num(rawViewport.h, 0) > 0
        ? { w: num(rawViewport.w, 0), h: num(rawViewport.h, 0) }
        : null;

    const mode: DesktopMode = src.mode === 'floating' ? 'floating' : 'tiling';
    const backdropSrc = src.backdrop as Record<string, unknown> | undefined;
    const backdrop: BackdropRef =
      backdropSrc && typeof backdropSrc.id === 'string'
        ? {
            id: backdropSrc.id,
            ...(backdropSrc.params && typeof backdropSrc.params === 'object'
              ? { params: backdropSrc.params as Record<string, unknown> }
              : {}),
          }
        : { id: DEFAULT_BACKDROP };

    const focusedAreaId =
      typeof src.focusedAreaId === 'string' && findArea(center, src.focusedAreaId)
        ? src.focusedAreaId
        : firstArea(center).id;
    const fullscreenAreaId =
      typeof src.fullscreenAreaId === 'string' && findArea(center, src.fullscreenAreaId)
        ? src.fullscreenAreaId
        : null;
    const paneSeq = Math.max(
      typeof src.paneSeq === 'number' && Number.isFinite(src.paneSeq) ? src.paneSeq : 0,
      maxSeq + 1,
    );

    // Restoring focus is best-effort: a stored instance id that no longer exists
    // (the pane's module was uninstalled) just means nothing starts focused.
    const focusedInstanceId =
      typeof src.focusedInstanceId === 'string' ? src.focusedInstanceId : null;

    const focusedWindowId =
      typeof src.focusedWindowId === 'string' && windows.some((w) => w.id === src.focusedWindowId)
        ? src.focusedWindowId
        : null;

    return {
      center,
      docks,
      windows,
      windowViewport,
      mode,
      backdrop,
      fullscreenAreaId,
      // Never restored, and never written either (see `serializeFrame`). A
      // presented pane covers the workspace strip and the taskbar; bringing a
      // workspace back into that state hands the user a screen with no visible
      // way out and no memory of having asked for it.
      presentedInstanceId: null,
      focusedAreaId,
      focusedInstanceId,
      focusedWindowId,
      paneSeq,
    };
  } catch {
    return null;
  }
}

/** Every `viewId` named anywhere in a raw blob that is NOT itself a retired view —
 * i.e. the views the layout already places under their current name. A rename must
 * not add a second copy of one of these. */
function collectViewIds(src: unknown): Set<string> {
  const out = new Set<string>();
  const walk = (v: unknown): void => {
    if (!v || typeof v !== 'object') return;
    if (Array.isArray(v)) {
      v.forEach(walk);
      return;
    }
    const o = v as Record<string, unknown>;
    if (typeof o.viewId === 'string' && RENAMED_VIEWS[o.viewId] === undefined) {
      out.add(o.viewId);
    }
    Object.values(o).forEach(walk);
  };
  walk(src);
  return out;
}

/** Drop areas left with no tabs, so a pane pruned (or merged away) by this
 * deserializer doesn't leave an "Empty area" placeholder where it used to sit.
 * Returns null when nothing survives, letting the caller reseed from the preset;
 * a genuinely empty single-area frame is preserved by the caller's fallback. */
function pruneEmptyAreas(node: LayoutNode): LayoutNode | null {
  if (node.kind === 'area') return node.tabs.length > 0 ? node : null;
  const kept: LayoutNode[] = [];
  const sizes: number[] = [];
  node.children.forEach((child, i) => {
    const pruned = pruneEmptyAreas(child);
    if (pruned) {
      kept.push(pruned);
      sizes.push(node.sizes[i] ?? 1 / node.children.length);
    }
  });
  if (kept.length === 0) return null;
  return { ...node, children: kept, sizes: renormalizeSizes(sizes) };
}

function renormalizeSizes(sizes: number[]): number[] {
  const total = sizes.reduce((a, b) => a + b, 0);
  return total > 0 ? sizes.map((s) => s / total) : sizes.map(() => 1 / sizes.length);
}

function clampSize(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 48 ? value : fallback;
}
