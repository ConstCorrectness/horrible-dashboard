/**
 * The shared **relay surface**: the catalogue of UI operations a backend tool call
 * can run against the registry + frame controller. Both the agent orchestrator
 * (orchestrator-client.ts) and the Python REPL (../repl/client.ts) relay tool
 * calls over the `/ws` socket and execute them here, so any verb one can run, the
 * other can too — one source of truth. The vocabulary is the desktop's: every
 * pane lives in a **window** (several merged panes are its tabs), and **regions**
 * are the strips inside a pane. See docs/modules/agent-chat.md and
 * docs/modules/repl.md.
 */
import {
  arrangeDesktop,
  describeLayout,
  listOpenPanesDetailed,
  presentPane,
  readPaneAgentContext,
  roleOf,
  setBackdrop,
  setPaneSection,
  setRegionView,
  setWindowMode,
  showTarget,
  snapWindow,
  tearOffPane,
  resolveView,
  toggleRegion,
} from '../../layout/controller';
import { windowOfInstance } from '../../layout/model';
import { isSnapZone, SNAP_ZONES } from '../../layout/snap';
import { layoutStore } from '../../layout/store';
import type { RegionPosition, SnapZone, WindowRect } from '../../layout/types';
import { applyTheme, isKnownTheme, THEME_SETTING_KEY } from '../../theme';
import { getSetting, setSetting } from '../../settings';
import { registry } from '../../registry';
import { executeDynamicTool, serializeManifest } from './manifest';
import { LAYOUT_VERBS, nearestToolNames } from './tool-names';

const REGION_POSITIONS: readonly RegionPosition[] = ['left', 'right', 'bottom'];
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
  return isSnapZone(value) ? value : null;
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
    case 'open_window': {
      // Give a pane a window of its own — tearing it out of a tabbed window if it
      // shares one — and optionally place that window.
      const instanceId = String(args.instanceId);
      if (!windowOfInstance(layoutStore.getSnapshot().frame, instanceId)) {
        return { error: `unknown instanceId: ${instanceId}` };
      }
      tearOffPane(instanceId);
      // Placement is applied after the tear, so a bad snap/rect leaves an ordinary
      // window rather than failing the whole call.
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
