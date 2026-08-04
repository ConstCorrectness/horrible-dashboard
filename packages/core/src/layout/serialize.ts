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
import { createDock, createEmptyFrame, findArea, firstArea, normalize } from './model';
import type {
  AreaNode,
  DockSide,
  DockState,
  FloatingPane,
  FrameState,
  LayoutNode,
  PaneState,
  RegionPosition,
  RegionState,
} from './types';

export const FRAME_SCHEMA = 'horrible.frame';
export const FRAME_VERSION = 1;

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
 * modules/games/hub-section.ts). Duplicates that result from two old views
 * collapsing onto one are dropped by `readPane`.
 */
const RENAMED_VIEWS: Readonly<Record<string, string>> = {
  'games.board': 'games.lobby',
  'games.loadout': 'games.lobby',
  'games.thoughts': 'games.log',
};

export function serialize(frame: FrameState): SerializedLayout {
  return {
    schema: FRAME_SCHEMA,
    version: FRAME_VERSION,
    frame: frame as unknown as Record<string, unknown>,
  };
}

/**
 * Parse a stored layout blob into a FrameState, or null when the blob is not a
 * frame layout (wrong schema — including legacy dockview blobs) or too corrupt
 * to salvage. `knownViews` filters out panes/regions whose views no longer
 * exist. A future `version` bump adds a `migrate(v)` step here.
 *
 * `embeddedViews` drops **dock** entries for views that have since been marked
 * `embedded`. The live rule is that embedded implies never dockable
 * (`dockSidesOf` returns `[]`), so a saved dock entry for one is a state today's
 * code cannot produce — and it would resurrect exactly the second, competing home
 * that embedding a view removes. It only filters docks: a *centre* pane or a
 * region is a placement the user made deliberately and `openPaneInArea` still
 * supports.
 */
export function deserialize(
  blob: SerializedLayout | null | undefined,
  knownViews: ReadonlySet<string>,
  embeddedViews: ReadonlySet<string> = new Set(),
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

    const trackNodeSeq = (id: string): void => {
      const m = /^[as](\d+)$/.exec(id);
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
        .filter((p): p is PaneState => p !== null && !embeddedViews.has(p.viewId));
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

    const floating: FloatingPane[] = (Array.isArray(src.floating) ? src.floating : [])
      .map((f): FloatingPane | null => {
        if (!f || typeof f !== 'object') return null;
        const entry = f as Record<string, unknown>;
        const pane = readPane(entry.pane);
        if (!pane) return null;
        const rect = entry.rect as Record<string, unknown> | undefined;
        const num = (v: unknown, d: number): number =>
          typeof v === 'number' && Number.isFinite(v) ? v : d;
        return {
          pane,
          rect: {
            x: clamp01(num(rect?.x, 0.2)),
            y: clamp01(num(rect?.y, 0.15)),
            w: Math.min(Math.max(num(rect?.w, 0.5), 0.1), 1),
            h: Math.min(Math.max(num(rect?.h, 0.55), 0.1), 1),
          },
          z: num(entry.z, 1),
        };
      })
      .filter((f): f is FloatingPane => f !== null);

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

    return {
      center,
      docks,
      floating,
      fullscreenAreaId,
      focusedAreaId,
      focusedInstanceId,
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

function clamp01(value: number): number {
  return Math.min(Math.max(value, 0), 0.95);
}
