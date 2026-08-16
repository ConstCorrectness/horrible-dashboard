/**
 * The shared **relay surface**: the catalogue of UI operations a backend tool call
 * can run against the registry + frame controller. Both the agent orchestrator
 * (orchestrator-client.ts) and the Python REPL (../repl/client.ts) relay tool
 * calls over the `/ws` socket and execute them here, so any verb one can run, the
 * other can too — one source of truth. The vocabulary is the frame engine's:
 * center **areas** hold documents/widgets, **docks** hold tools, **regions** are
 * the strips inside a pane. See docs/modules/agent-chat.md and docs/modules/repl.md.
 */
import {
  arrangeDesktop,
  describeLayout,
  fullscreenArea,
  joinAreaDirection,
  listOpenPanesDetailed,
  movePaneTo,
  moveWindowToDesktop,
  openToolInDock,
  readPaneAgentContext,
  resizeAreaPx,
  resolveView,
  roleOf,
  setBackdrop,
  setDesktopMode,
  setPaneSection,
  setPaneWindowed,
  setRegionView,
  setWindowMode,
  showTarget,
  snapWindow,
  splitAreaBy,
  toggleDock,
  toggleRegion,
} from '../../layout/controller';
import { windowOfInstance } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import type {
  DesktopMode,
  DockSide,
  NavDirection,
  RegionPosition,
  SnapZone,
  WindowRect,
} from '../../layout/types';
import { applyTheme, isKnownTheme, THEME_SETTING_KEY } from '../../theme';
import { getSetting, setSetting } from '../../settings';
import { registry, type SplitDirection } from '../../registry';
import { executeDynamicTool, serializeManifest } from './manifest';
import { LAYOUT_VERBS, nearestToolNames } from './tool-names';

const SPLIT_DIRS: readonly SplitDirection[] = ['left', 'right', 'above', 'below'];
const NAV_DIRS: readonly NavDirection[] = ['left', 'right', 'up', 'down'];
const DOCK_SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];
const REGION_POSITIONS: readonly RegionPosition[] = ['left', 'right', 'bottom'];
const SNAP_ZONES: readonly SnapZone[] = [
  'left',
  'right',
  'top',
  'bottom',
  'tl',
  'tr',
  'bl',
  'br',
  'max',
];
const ARRANGE_STYLES = ['grid', 'cascade', 'columns', 'rows'] as const;

/**
 * The taskbar config setting key.
 *
 * Duplicated from packages/ui rather than imported: core must not depend on ui.
 * It is a string constant on a stored document, so the cost of the duplication
 * is bounded and the alternative is a package cycle.
 */
const TASKBAR_SETTING_KEY = 'desktop.taskbar';

/** A snap zone from the wire, or null - never a guess. */
function resolveSnapZone(value: unknown): SnapZone | null {
  const v = String(value);
  return (SNAP_ZONES as readonly string[]).includes(v) ? (v as SnapZone) : null;
}

/**
 * A pixel rect from the wire. Every field must be a finite number: a partial
 * rect would place a window somewhere nobody asked for.
 */
function resolveRect(value: unknown): WindowRect | null {
  if (!value || typeof value !== 'object') return null;
  const r = value as Record<string, unknown>;
  const nums = ['x', 'y', 'w', 'h'].map((k) => Number(r[k]));
  return nums.every((n) => Number.isFinite(n))
    ? { x: nums[0], y: nums[1], w: nums[2], h: nums[3] }
    : null;
}

/**
 * Orientation aliases the agent may pass instead of a concrete side: `vertical`
 * means side-by-side areas (split toward the right), `horizontal` means stacked
 * areas (split below). The UI corner-grip still uses the four concrete sides; this
 * only widens what `split_area` accepts so the model can reason in the simpler
 * vertical/horizontal terms. See docs/architecture/agent-tools.mdx.
 */
const SPLIT_ALIASES: Record<string, SplitDirection> = { vertical: 'right', horizontal: 'below' };

/** Resolve a split arg (a concrete side or a vertical/horizontal alias) to a side. */
function resolveSplitDirection(raw: unknown): SplitDirection | null {
  const v = String(raw);
  if (SPLIT_DIRS.includes(v as SplitDirection)) return v as SplitDirection;
  return SPLIT_ALIASES[v] ?? null;
}

/** Viewport nav direction; accepts the split-style `above`/`below` synonyms. */
function resolveNavDirection(raw: unknown): NavDirection | null {
  const v = String(raw);
  if (NAV_DIRS.includes(v as NavDirection)) return v as NavDirection;
  if (v === 'above') return 'up';
  if (v === 'below') return 'down';
  return null;
}

function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}

/** Display title for a pane id (panel or widget), falling back to the id. */
export function paneTitle(id: string): string {
  return resolveView(id)?.title ?? id;
}

/** Execute one relayed tool call against the registry/frame controller. */
export async function executeTool(name: string, args: Record<string, unknown>): Promise<unknown> {
  const lc = registry.layoutController;
  switch (name) {
    // ------------------------------------------------------------- reads
    case 'list_available_panes':
      return {
        // Embedded views get no row of their own — they are listed under the host
        // that owns them, below. Every one of them is still reachable by name via
        // `show`, which is the invariant that makes merging a pane safe: the list
        // gets shorter, the agent's vocabulary does not.
        views: [...registry.panels, ...registry.widgets]
          .filter((v) => !v.embedded)
          .map((v) => ({
            id: v.id,
            title: v.title,
            role: roleOf(v.id),
            ...(roleOf(v.id) === 'tool' ? { defaultDock: v.defaultDock ?? 'left' } : {}),
            ...(v.sections?.length
              ? { sections: v.sections.map((s) => ({ id: s.id, label: s.label })) }
              : {}),
            ...(v.regions?.length
              ? {
                  regions: v.regions.map((r) => ({
                    id: r.id,
                    label: r.label,
                    position: r.position ?? 'right',
                  })),
                }
              : {}),
          })),
      };
    case 'show':
      return showTarget(String(args.target ?? ''), args.where as 'here' | 'beside' | 'dock');
    case 'list_workspaces':
      return lc ? await lc.listWorkspaces() : { error: 'workspace not ready' };
    case 'list_open_panes':
      return { panes: listOpenPanesDetailed() };
    case 'get_layout':
      return describeLayout();
    case 'get_pane_context': {
      const instanceId = String(args.instanceId);
      // Reading a *named* section switches the pane to it first. Only the visible
      // section is mounted, so its provider is the only one that exists — there is
      // no way to read a background section without bringing it forward, and
      // pretending otherwise would return the wrong section's data as if it were
      // the one asked for.
      const section = typeof args.section === 'string' ? args.section : null;
      if (section && !setPaneSection(instanceId, section)) {
        return { error: `pane ${instanceId} has no section "${section}"` };
      }
      const snapshot = readPaneAgentContext(instanceId);
      return snapshot === null
        ? { error: `no agent context for pane: ${instanceId}` }
        : { context: snapshot };
    }

    // ------------------------------------------------------------- panes
    case 'open_pane': {
      // Optional params thread through to the pane instance (e.g. a training
      // notebook's {projectId, notebook}); read by the pane via usePaneParams.
      const params =
        args.params && typeof args.params === 'object'
          ? (args.params as Record<string, unknown>)
          : undefined;
      registry.openPanel(String(args.id), params ? { params } : undefined);
      return { ok: true, opened: args.id };
    }
    case 'close_pane':
      return { closed: lc?.closePane(String(args.id)) ?? false };
    case 'focus_pane':
      return { focused: lc?.focusPane(String(args.instanceId)) ?? false };
    case 'move_pane': {
      const direction = args.direction != null ? resolveNavDirection(args.direction) : null;
      const areaId = args.areaId != null ? String(args.areaId) : undefined;
      // `edge` splits the destination and drops the pane into the new half. This
      // is what closes the long-standing gap where a pane opened into the wrong
      // place could not be moved beside another one at all — `move_pane` could
      // only tab into an existing area or step to an adjacent one.
      // Same vocabulary as `split_area` — including its `vertical`/`horizontal`
      // aliases — so one concept has one spelling across the whole tool surface.
      const edge = args.edge != null ? resolveSplitDirection(args.edge) : null;
      if (args.edge != null && !edge) {
        return { error: `edge must be one of ${SPLIT_DIRS.join(', ')}, vertical, or horizontal` };
      }
      // `edge` counts as having named a destination. It is checked here rather
      // than only alongside areaId/direction because `edge` ALONE is the natural
      // call — "split where this pane already is" — and `movePaneTo` resolves the
      // host from the pane's own area. Requiring a companion argument made the
      // most useful form of the verb unreachable.
      if (!areaId && !direction && !edge) {
        return { error: `pass areaId, direction (${NAV_DIRS.join(', ')}), or edge` };
      }
      const ok = movePaneTo(String(args.instanceId), {
        areaId,
        direction: direction ?? undefined,
        edge: edge ?? undefined,
      });
      return ok ? { ok } : { error: 'unknown pane/area, or the move breaks area rules' };
    }
    // ----------------------------------------------------------- windows
    case 'open_window': {
      const instanceId = String(args.instanceId);
      if (!setPaneWindowed(instanceId, true)) {
        return { error: 'already a window, or unknown instanceId' };
      }
      // Placement is applied after the window exists, so a bad snap/rect leaves
      // an ordinary window rather than failing the whole call.
      if (args.snap != null) {
        const zone = resolveSnapZone(args.snap);
        if (!zone) return { ok: true, warning: `unknown snap zone "${String(args.snap)}"` };
        snapWindow(instanceId, zone);
      } else if (args.rect != null) {
        const rect = resolveRect(args.rect);
        if (!rect) return { ok: true, warning: 'rect needs finite x, y, w and h' };
        const win = windowOfInstance(layoutStore.getSnapshot().frame, instanceId);
        if (win) layoutStore.dispatch({ type: 'SET_WINDOW_RECT', windowId: win.id, rect });
      }
      return { ok: true };
    }
    case 'dock_window': {
      const ok = setPaneWindowed(String(args.instanceId), false);
      return ok ? { ok } : { error: 'not a window, or unknown instanceId' };
    }
    case 'window_state': {
      const instanceId = String(args.instanceId);
      switch (String(args.state)) {
        case 'minimize':
          return { ok: setWindowMode(instanceId, 'minimized') };
        case 'maximize':
          return { ok: setWindowMode(instanceId, 'maximized') };
        case 'restore':
          return { ok: setWindowMode(instanceId, 'normal') };
        case 'snap': {
          const zone = resolveSnapZone(args.snap);
          if (!zone) return { error: `snap must be one of ${SNAP_ZONES.join(', ')}` };
          return { ok: snapWindow(instanceId, zone) };
        }
        case 'move_to_desktop': {
          const workspaceId = args.workspaceId != null ? String(args.workspaceId) : '';
          if (!workspaceId) return { error: 'move_to_desktop needs workspaceId' };
          // Async: it edits another workspace's stored blob. Reported as started
          // rather than awaited, matching the other cross-workspace verbs.
          void moveWindowToDesktop(instanceId, workspaceId);
          return { ok: true };
        }
        default:
          return {
            error: 'state must be minimize, maximize, restore, snap or move_to_desktop',
          };
      }
    }
    case 'arrange_windows': {
      const style = String(args.style);
      if (!(ARRANGE_STYLES as readonly string[]).includes(style)) {
        return { error: `style must be one of ${ARRANGE_STYLES.join(', ')}` };
      }
      const ok = arrangeDesktop(style as (typeof ARRANGE_STYLES)[number]);
      return ok ? { ok } : { error: 'no open windows to arrange' };
    }

    // ------------------------------------------------------- appearance
    case 'desktop.set_backdrop': {
      const id = String(args.id);
      if (!registry.backdrop(id)) {
        return {
          error: `unknown backdrop "${id}"`,
          available: registry.backdrops.map((b) => b.id),
        };
      }
      const params =
        args.params && typeof args.params === 'object'
          ? (args.params as Record<string, unknown>)
          : undefined;
      return { ok: setBackdrop(params ? { id, params } : { id }) };
    }
    case 'desktop.set_theme': {
      const id = String(args.id);
      if (!isKnownTheme(id)) return { error: `unknown theme "${id}"` };
      // Applied AND persisted: applying alone reverts on the next settings
      // publish, persisting alone leaves the current page on the old theme.
      applyTheme(id);
      void setSetting(THEME_SETTING_KEY, id);
      return { ok: true };
    }
    case 'desktop.set_mode': {
      const mode = String(args.mode);
      if (mode !== 'tiling' && mode !== 'floating') {
        return { error: 'mode must be tiling or floating' };
      }
      return { ok: setDesktopMode(mode as DesktopMode) };
    }
    case 'desktop.configure_taskbar': {
      // Merged over what is stored, not replaced: the tool documents omitted
      // fields as "left alone", and a whole-object write would silently reset
      // every zone the caller did not happen to mention.
      let current: Record<string, unknown> = {};
      try {
        const stored = getSetting<string>(TASKBAR_SETTING_KEY);
        current = stored ? (JSON.parse(stored) as Record<string, unknown>) : {};
      } catch {
        current = {};
      }
      const next = { ...current };
      for (const key of ['position', 'zones', 'showLabels', 'autoHide']) {
        if (args[key] !== undefined) next[key] = args[key];
      }
      void setSetting(TASKBAR_SETTING_KEY, JSON.stringify(next));
      return { ok: true, taskbar: next };
    }

    // ------------------------------------------------------------- areas
    case 'split_area': {
      const direction = resolveSplitDirection(args.direction);
      if (!direction) {
        return {
          error: `direction must be one of ${SPLIT_DIRS.join(', ')}, vertical, or horizontal`,
        };
      }
      // viewId is optional: omitted → duplicate the split area's own view.
      const viewId = args.viewId != null ? String(args.viewId) : undefined;
      const target = String(args.instanceId ?? args.areaId ?? '');
      const newInstanceId = splitAreaBy(target, direction, viewId);
      return newInstanceId === null
        ? { error: 'unknown area/instanceId or viewId' }
        : { ok: true, newInstanceId };
    }
    case 'join_area': {
      const direction = resolveNavDirection(args.direction);
      if (!direction) return { error: `direction must be one of ${NAV_DIRS.join(', ')}` };
      const ok = joinAreaDirection(String(args.instanceId ?? args.areaId ?? ''), direction);
      return ok ? { ok } : { error: 'no aligned neighbor to join in that direction' };
    }
    case 'resize_area': {
      const ok = resizeAreaPx(String(args.instanceId ?? args.areaId ?? ''), {
        width: num(args.width),
        height: num(args.height),
      });
      return ok ? { ok } : { error: 'unknown area/instanceId (or no resizable axis)' };
    }
    case 'fullscreen_area': {
      const on = args.on !== false;
      const target = args.instanceId ?? args.areaId;
      const ok = fullscreenArea(on ? String(target ?? '') : null, on);
      return ok ? { ok, fullscreen: on } : { error: 'unknown area/instanceId' };
    }

    // ----------------------------------------------------------- regions
    case 'toggle_region': {
      const position = String(args.position) as RegionPosition;
      if (!REGION_POSITIONS.includes(position)) {
        return { error: `position must be one of ${REGION_POSITIONS.join(', ')}` };
      }
      const open = typeof args.open === 'boolean' ? args.open : undefined;
      const ok = toggleRegion(String(args.instanceId), position, open);
      return ok ? { ok } : { error: 'unknown instanceId, or the view declares no such region' };
    }
    case 'set_region_view': {
      const ok = setRegionView(String(args.instanceId), String(args.viewId));
      return ok ? { ok } : { error: 'unknown instanceId, or viewId is not one of its regions' };
    }

    // ------------------------------------------------------------- docks
    case 'open_tool_in_dock': {
      const dock = args.dock != null ? (String(args.dock) as DockSide) : undefined;
      if (dock && !DOCK_SIDES.includes(dock)) {
        return { error: `dock must be one of ${DOCK_SIDES.join(', ')}` };
      }
      const instanceId = openToolInDock(String(args.id), dock);
      return instanceId ? { ok: true, instanceId } : { error: 'unknown id, or not a tool view' };
    }
    case 'toggle_dock': {
      const dock = String(args.dock) as DockSide;
      if (!DOCK_SIDES.includes(dock)) {
        return { error: `dock must be one of ${DOCK_SIDES.join(', ')}` };
      }
      const visible = typeof args.visible === 'boolean' ? args.visible : undefined;
      const ok = toggleDock(dock, visible);
      return ok ? { ok } : { error: 'dock has no tools to show' };
    }

    // -------------------------------------------------------- workspaces
    case 'create_workspace':
      return lc ? await lc.createWorkspace(String(args.name)) : { error: 'workspace not ready' };
    case 'switch_workspace':
      registry.switchWorkspace(String(args.id));
      return { ok: true, switched: args.id };

    default: {
      // Not a layout verb — try the dynamic tools the manifest advertised
      // (per-widget/panel agentTools and agent-exposed commands).
      const dynamic = await executeDynamicTool(name, args);
      if (dynamic.handled) return dynamic.result;
      // serializeManifest() rather than the raw decls: it is exactly the catalog the
      // backend was told about, so a suggestion can never name something uncallable.
      const near = nearestToolNames(name, [
        ...LAYOUT_VERBS,
        ...serializeManifest().map((t) => t.name),
      ]);
      return {
        error: `unknown tool: ${name}`,
        ...(near.length ? { didYouMean: near } : {}),
      };
    }
  }
}
