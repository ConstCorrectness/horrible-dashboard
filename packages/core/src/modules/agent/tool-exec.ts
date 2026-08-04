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
  describeLayout,
  fullscreenArea,
  joinAreaDirection,
  listOpenPanesDetailed,
  movePaneTo,
  openToolInDock,
  readPaneAgentContext,
  resizeAreaPx,
  resolveView,
  roleOf,
  setPaneSection,
  setRegionView,
  showTarget,
  splitAreaBy,
  toggleDock,
  toggleRegion,
} from '../../layout/controller';
import type { DockSide, NavDirection, RegionPosition } from '../../layout/types';
import { registry, type SplitDirection } from '../../registry';
import { executeDynamicTool, serializeManifest } from './manifest';
import { LAYOUT_VERBS, nearestToolNames } from './tool-names';

const SPLIT_DIRS: readonly SplitDirection[] = ['left', 'right', 'above', 'below'];
const NAV_DIRS: readonly NavDirection[] = ['left', 'right', 'up', 'down'];
const DOCK_SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];
const REGION_POSITIONS: readonly RegionPosition[] = ['left', 'right', 'bottom'];

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
      if (!areaId && !direction) {
        return { error: `pass areaId, or direction (${NAV_DIRS.join(', ')})` };
      }
      const ok = movePaneTo(String(args.instanceId), { areaId, direction: direction ?? undefined });
      return ok ? { ok } : { error: 'unknown pane/area, or the move breaks area rules' };
    }
    case 'float_pane':
    case 'dock_pane': {
      if (!lc) return { error: 'workspace not ready' };
      const ok = lc.setPaneFloating(String(args.instanceId), name === 'float_pane');
      return ok ? { ok } : { error: 'pane already in that state, or unknown instanceId' };
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
