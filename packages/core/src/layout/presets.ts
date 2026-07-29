/**
 * Frame presets: the declarative full-frame seeds behind each predefined
 * workflow workspace (and `layout.reset`). Unlike the old pane-list presets, a
 * FramePreset describes the whole picture — the center tree AND the docks —
 * because workspaces persist everything (Blender model). `seedFromPreset` is
 * pure: registry-derived data (which views exist, their declared regions)
 * arrives via arguments.
 */
import { areaId, createDock, createEmptyFrame, firstArea, instanceId, normalize } from './model';
import type {
  DockSide,
  DockState,
  FrameState,
  LayoutNode,
  PaneState,
  RegionPosition,
  RegionState,
} from './types';

/** One node of a preset's center tree. */
export type PresetNode =
  /** An interior split; `sizes` are fractions (defaults to equal). */
  | { split: 'row' | 'column'; sizes?: number[]; children: PresetNode[] }
  /** A tabbed document area. */
  | { tabs: string[]; active?: number; headerCollapsed?: boolean }
  /** A single-pane area (widget, or a lone document). */
  | { pane: string; params?: Record<string, unknown>; headerCollapsed?: boolean };

export interface PresetDock {
  /** Tool view ids stacked in this dock, in order. */
  tools: string[];
  /** View id of the visible tool; defaults to the first. */
  activeTool?: string;
  size?: number;
  /** Defaults to true when the dock has tools. */
  visible?: boolean;
}

/** A predefined workflow workspace: a complete frame, seeded on first open. */
export interface FramePreset {
  id: string;
  name: string;
  /** Workspace-tab glyph (emoji/letter); falls back to the name's first character. */
  icon?: string;
  /**
   * The roster agent this workspace's chat talks to by default — a *role* for the
   * layout, not just furniture: the persona, its system prompt and its tool scope
   * switch with the workspace. Just an id (resolved against the backend roster,
   * `GET /api/agent/roster`) rather than an inline spec, so agents stay defined in
   * one place (backend `roster.py` / `host.add_agent`). An unknown id falls back to
   * `main`, the same way `seedFromPreset` skips unknown views.
   */
  agent?: string;
  frame: {
    center: PresetNode;
    docks?: Partial<Record<DockSide, PresetDock>>;
  };
}

export interface SeedOptions {
  /** Views that exist; preset entries referencing anything else are skipped. */
  knownViews: ReadonlySet<string>;
  /** Initial region strips for a view (from its declarations), if any. */
  regionsFor?: (viewId: string) => Partial<Record<RegionPosition, RegionState>> | undefined;
  /**
   * A view's declared starting dock extent (`defaultDockSize`), if any. Kept as a
   * callback for the same reason `regionsFor` is: this module stays free of the
   * registry. Without it a preset-seeded tool would ignore the width its module
   * declared, and only tools opened from the rail would honor it.
   */
  dockSizeFor?: (viewId: string) => number | undefined;
}

/** Materialize a preset into a FrameState. Unknown views are skipped silently. */
export function seedFromPreset(preset: FramePreset, opts: SeedOptions): FrameState {
  let seq = 0;
  const makePane = (viewId: string, params?: Record<string, unknown>): PaneState | null => {
    if (!opts.knownViews.has(viewId)) return null;
    const pane: PaneState = { instanceId: instanceId(viewId, seq++), viewId };
    if (params) pane.params = params;
    const regions = opts.regionsFor?.(viewId);
    if (regions && Object.keys(regions).length) pane.regions = regions;
    return pane;
  };

  const buildNode = (node: PresetNode): LayoutNode | null => {
    if ('split' in node) {
      const children = node.children.map(buildNode).filter((c): c is LayoutNode => c !== null);
      if (children.length === 0) return null;
      if (children.length === 1) return children[0];
      const sizes =
        node.sizes && node.sizes.length === children.length
          ? node.sizes
          : children.map(() => 1 / children.length);
      return { kind: 'split', id: `s${seq++}`, orientation: node.split, children, sizes };
    }
    if ('tabs' in node) {
      const tabs = node.tabs.map((v) => makePane(v)).filter((p): p is PaneState => p !== null);
      return {
        kind: 'area',
        id: areaId(seq++),
        tabs,
        activeTab: Math.min(node.active ?? 0, Math.max(tabs.length - 1, 0)),
        ...(node.headerCollapsed ? { headerCollapsed: true } : {}),
      };
    }
    const pane = makePane(node.pane, node.params);
    return {
      kind: 'area',
      id: areaId(seq++),
      tabs: pane ? [pane] : [],
      activeTab: 0,
      ...(node.headerCollapsed ? { headerCollapsed: true } : {}),
    };
  };

  const center = buildNode(preset.frame.center);
  const empty = createEmptyFrame();
  if (!center) return empty;

  const docks = {} as Record<DockSide, DockState>;
  for (const side of ['left', 'right', 'bottom'] as const) {
    const decl = preset.frame.docks?.[side];
    const base = createDock(side);
    if (!decl) {
      docks[side] = base;
      continue;
    }
    const tools = decl.tools
      .map((v) => {
        const pane = makePane(v);
        if (!pane) return null;
        // An explicit preset `size` is the author's call for the whole dock and
        // wins; otherwise the view's own declared width applies.
        const declared = decl.size === undefined ? opts.dockSizeFor?.(v) : undefined;
        if (declared !== undefined) pane.dockSize = declared;
        return pane;
      })
      .filter((p): p is PaneState => p !== null);
    const active = tools.find((t) => t.viewId === decl.activeTool) ?? tools[0];
    docks[side] = {
      visible: (decl.visible ?? true) && tools.length > 0,
      size: decl.size ?? base.size,
      tools,
      activeTool: active?.instanceId ?? null,
    };
  }

  const normalized = normalize(center);
  return {
    center: normalized,
    docks,
    floating: [],
    fullscreenAreaId: null,
    focusedAreaId: firstArea(normalized).id,
    focusedInstanceId: null,
    paneSeq: seq,
  };
}
