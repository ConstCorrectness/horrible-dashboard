/**
 * The shared **relay surface**: the catalogue of UI operations a backend tool call
 * can run against the registry + frame controller. Both the agent orchestrator
 * (orchestrator-client.ts) and the Python REPL (../repl/client.ts) relay tool
 * calls over the `/ws` socket and execute them here, so any verb one can run, the
 * other can too — one source of truth. The vocabulary is the workbench's: every
 * pane is a tab in a **group** (in the centre, an edge dock, or a popout), and
 * **regions** are the strips inside a pane. The `*_window` tool names predate the
 * workbench and are answered in its terms. See docs/modules/agent-chat.md and
 * docs/modules/repl.md.
 */
import {
  describeLayout,
  listOpenPanesDetailed,
  presentPane,
  readPaneAgentContext,
  roleOf,
  setBackdrop,
  setPaneSection,
  setRegionView,
  showTarget,
  movePaneTo,
  tearOffPane,
  toggleDock,
  toggleMaximizedGroup,
  resolveView,
  toggleRegion,
} from '../../layout/controller';
import type { PlaceTarget, SplitSide } from '../../layout/dock/types';
import { findPaneAnywhere } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import type { RegionPosition } from '../../layout/types';
import { applyTheme, isKnownTheme, THEME_SETTING_KEY } from '../../theme';
import { getSetting, setSetting } from '../../settings';
import { registry } from '../../registry';
import { executeDynamicTool, serializeManifest } from './manifest';
import { LAYOUT_VERBS, nearestToolNames } from './tool-names';

const REGION_POSITIONS: readonly RegionPosition[] = ['left', 'right', 'bottom'];

/**
 * The taskbar config setting key.
 *
 * Duplicated from packages/ui rather than imported: core must not depend on ui.
 * It is a string constant on a stored document, so the cost of the duplication
 * is bounded and the alternative is a package cycle.
 */
const TASKBAR_SETTING_KEY = 'desktop.taskbar';

/** The floating desktop's snap zones the workbench can still honour, as splits. */
const SNAP_AS_SPLIT: Record<string, SplitSide> = {
  left: 'left',
  'third-l': 'left',
  right: 'right',
  'third-r': 'right',
  top: 'above',
  bottom: 'below',
};
const SNAP_NAMES = [...Object.keys(SNAP_AS_SPLIT), 'max'];

/**
 * Place a pane the way a snap zone used to: a half becomes a split of the centre
 * group beside it, `max` maximizes its group. Null for a zone that has no
 * workbench meaning — never a guess.
 */
function snapToWorkbench(instanceId: string, snap: unknown): boolean | null {
  const frame = layoutStore.getSnapshot().frame;
  const located = findPaneAnywhere(frame, instanceId);
  if (!located) return false;
  if (snap === 'max') {
    if (frame.maximizedAreaId === located.location.areaId) return true;
    return toggleMaximizedGroup(instanceId);
  }
  const side = typeof snap === 'string' ? SNAP_AS_SPLIT[snap] : undefined;
  if (!side) return null;
  // Split the group it is in when that is a centre group with company; otherwise
  // split the focused centre group, so a docked tool snapped left lands in the
  // centre's left half rather than nowhere.
  const area =
    located.location.kind === 'center' ? located.location.areaId : frame.focusedAreaId ?? '';
  const target: PlaceTarget = { kind: 'split', areaId: area, side };
  movePaneTo(instanceId, target);
  return true;
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
      return showTarget(String(args.target ?? ''));
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
    // ----------------------------------------------------------- windows
    // Floating-desktop names, answered in workbench terms.
    case 'open_window': {
      // Give a pane a group of its own, and optionally place it.
      const instanceId = String(args.instanceId);
      if (!findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId)) {
        return { error: `unknown instanceId: ${instanceId}` };
      }
      if (args.snap != null) {
        const ok = snapToWorkbench(instanceId, args.snap);
        if (ok === null) {
          tearOffPane(instanceId);
          return { ok: true, warning: `snap must be one of ${SNAP_NAMES.join(', ')}` };
        }
        return { ok };
      }
      tearOffPane(instanceId);
      return args.rect != null
        ? { ok: true, warning: 'the workbench has no free windows; rect is ignored' }
        : { ok: true };
    }
    case 'window_state': {
      const instanceId = String(args.instanceId);
      const frame = layoutStore.getSnapshot().frame;
      const located = findPaneAnywhere(frame, instanceId);
      switch (String(args.state)) {
        case 'minimize':
          // Only a docked pane can be put away; a centre tab is a document in front.
          if (located?.location.kind !== 'dock') {
            return { ok: false, error: 'only a pane in an edge dock can be hidden' };
          }
          return { ok: toggleDock(located.location.side, false) };
        case 'maximize':
          if (!located) return { ok: false };
          return {
            ok:
              frame.maximizedAreaId === located.location.areaId ||
              toggleMaximizedGroup(instanceId),
          };
        case 'restore':
          if (!located) return { ok: false };
          if (frame.maximizedAreaId === located.location.areaId) {
            return { ok: toggleMaximizedGroup(instanceId) };
          }
          return { ok: lc?.focusPane(instanceId) ?? false };
        case 'snap': {
          const ok = snapToWorkbench(instanceId, args.snap);
          if (ok === null) return { error: `snap must be one of ${SNAP_NAMES.join(', ')}` };
          return { ok };
        }
        case 'present':
          return { ok: presentPane(instanceId) };
        case 'unpresent':
          return { ok: presentPane(null) };
        default:
          return {
            error: 'state must be minimize, maximize, restore, snap, present or unpresent',
          };
      }
    }
    case 'arrange_windows':
      return {
        error:
          'the workbench lays panes out in groups and docks, so there are no free windows to arrange; use window_state snap to split',
      };

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
