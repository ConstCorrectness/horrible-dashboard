/**
 * The shared **relay surface**: the catalogue of UI operations a backend tool call
 * can run against the registry + frame controller. Both the agent orchestrator
 * (orchestrator-client.ts) and the Python REPL (../repl/client.ts) relay tool
 * calls over the `/ws` socket and execute them here, so any verb one can run, the
 * other can too — one source of truth. The vocabulary is the workbench's: every
 * pane is a tab in a **group** (in the centre, an edge dock, or a popout), and
 * **regions** are the strips inside a pane. The floating desktop's `*_window` names
 * are still answered, mapped onto `place_pane` / `pane_state` / `arrange_panes`. See
 * docs/modules/agent-chat.md and docs/modules/repl.md.
 */
import {
  arrangePanes,
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
import { ARRANGE_STYLES, type ArrangeStyle } from '../../layout/dock/arrange';
import type { PlaceTarget, SplitSide } from '../../layout/dock/types';
import { findAreaAnywhere, findPaneAnywhere } from '../../layout/model';
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

/** `place_pane`'s destinations, one flat word each: a small model picks from a list
 * far more reliably than it composes a kind and a side. */
const PLACES: Record<string, PlaceTarget | 'split' | 'group'> = {
  center: { kind: 'center' },
  'dock-left': { kind: 'dock', side: 'left' },
  'dock-right': { kind: 'dock', side: 'right' },
  'dock-bottom': { kind: 'dock', side: 'bottom' },
  'split-left': 'split',
  'split-right': 'split',
  'split-above': 'split',
  'split-below': 'split',
  group: 'group',
};
export const PLACE_WORDS = Object.keys(PLACES);

const PANE_STATES = ['maximize', 'restore', 'hide', 'show', 'present', 'unpresent'] as const;

/**
 * The floating desktop's snap zones, as `place_pane` words. Corners and thirds had
 * no workbench meaning beyond their side, so they keep that much; `max` is a state.
 */
const SNAP_AS_PLACE: Record<string, string> = {
  left: 'split-left',
  'third-l': 'split-left',
  tl: 'split-left',
  bl: 'split-left',
  right: 'split-right',
  'third-r': 'split-right',
  tr: 'split-right',
  br: 'split-right',
  top: 'split-above',
  bottom: 'split-below',
};

type Reply = Record<string, unknown>;

/**
 * Move an open pane. A split is made beside `group` when given, else beside the
 * pane's own centre group, else beside the focused centre group — so a docked tool
 * sent `split-left` lands in the centre's left half rather than nowhere.
 */
function placePane(instanceId: string, to: string, group?: string): Reply {
  const frame = layoutStore.getSnapshot().frame;
  const located = findPaneAnywhere(frame, instanceId);
  if (!located) return { error: `unknown instanceId: ${instanceId}` };
  const place = PLACES[to];
  if (!place) return { error: `to must be one of ${PLACE_WORDS.join(', ')}` };

  let target: PlaceTarget;
  if (place === 'group') {
    if (!group || !findAreaAnywhere(frame, group)) {
      return { error: 'group must be a group id from get_layout' };
    }
    target = { kind: 'area', areaId: group };
  } else if (place === 'split') {
    const side = to.slice('split-'.length) as SplitSide;
    const beside =
      group ??
      (located.location.kind === 'center' ? located.location.areaId : (frame.focusedAreaId ?? ''));
    if (!findAreaAnywhere(frame, beside)) {
      return { error: 'group must be a group id from get_layout' };
    }
    target = { kind: 'split', areaId: beside, side };
  } else {
    target = place;
  }
  const moved = movePaneTo(instanceId, target);
  const after = findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId);
  return {
    ok: true,
    moved,
    ...(after ? { location: after.location } : {}),
    // A no-op is not a failure, but the model should know why nothing changed,
    // and the commonest one has a next step.
    ...(moved
      ? {}
      : {
          note:
            place === 'split' && !group
              ? 'it is alone in its group, so there is nothing to split off; pass `group` to split beside another group'
              : 'already there',
        }),
  };
}

function setPaneState(instanceId: string, state: string): Reply {
  const frame = layoutStore.getSnapshot().frame;
  if (state === 'unpresent') return { ok: presentPane(null) };
  const located = findPaneAnywhere(frame, instanceId);
  if (!located) return { error: `unknown instanceId: ${instanceId}` };
  const { location } = located;
  switch (state) {
    case 'maximize':
      return {
        ok: frame.maximizedAreaId === location.areaId || toggleMaximizedGroup(instanceId),
      };
    case 'restore':
      if (frame.maximizedAreaId === location.areaId) {
        return { ok: toggleMaximizedGroup(instanceId) };
      }
      return { ok: true, note: 'its group was not maximized' };
    case 'hide':
      // Only a dock can be put away; a centre tab is a document in front of the user.
      if (location.kind !== 'dock') {
        return { ok: false, error: 'only a pane in an edge dock can be hidden' };
      }
      return {
        ok: !frame.docks[location.side].visible || toggleDock(location.side, false),
      };
    case 'show':
      return { ok: registry.layoutController?.focusPane(instanceId) ?? false };
    case 'present':
      return { ok: presentPane(instanceId) };
    default:
      return { error: `state must be one of ${PANE_STATES.join(', ')}` };
  }
}

function arrange(style: string): Reply {
  if (!(ARRANGE_STYLES as readonly string[]).includes(style)) {
    return { error: `style must be one of ${ARRANGE_STYLES.join(', ')}` };
  }
  return { ok: true, changed: arrangePanes(style as ArrangeStyle) };
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
    // ------------------------------------------------------- arrangement
    case 'place_pane':
      return placePane(
        String(args.instanceId),
        String(args.to),
        typeof args.group === 'string' ? args.group : undefined,
      );
    case 'pane_state':
      return setPaneState(String(args.instanceId), String(args.state));
    case 'arrange_panes':
      return arrange(String(args.style));

    // The floating desktop's names. Kept because transcripts, saved prompts and
    // older REPL code call them, and a case here costs no schema: the model is
    // only ever shown the names above.
    case 'open_window': {
      const instanceId = String(args.instanceId);
      if (args.snap === 'max') return setPaneState(instanceId, 'maximize');
      const word = typeof args.snap === 'string' ? SNAP_AS_PLACE[args.snap] : undefined;
      if (word) return placePane(instanceId, word);
      if (!findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId)) {
        return { error: `unknown instanceId: ${instanceId}` };
      }
      // "A window of its own": a group of its own beside the one it was in.
      tearOffPane(instanceId);
      return { ok: true };
    }
    case 'window_state': {
      const instanceId = String(args.instanceId);
      const state = String(args.state);
      if (state === 'minimize') return setPaneState(instanceId, 'hide');
      if (state === 'snap') {
        if (args.snap === 'max') return setPaneState(instanceId, 'maximize');
        const word = typeof args.snap === 'string' ? SNAP_AS_PLACE[args.snap] : undefined;
        return word ? placePane(instanceId, word) : { error: 'unknown snap zone' };
      }
      if (state === 'restore') {
        // The desktop's restore also un-minimized; here that is showing the pane.
        const res = setPaneState(instanceId, 'restore');
        return res.note ? setPaneState(instanceId, 'show') : res;
      }
      return setPaneState(instanceId, state);
    }
    case 'arrange_windows': {
      // Cascade overlapped windows so each title showed: tabs are that, here.
      const style = String(args.style);
      return arrange(style === 'cascade' ? 'tabs' : style);
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
