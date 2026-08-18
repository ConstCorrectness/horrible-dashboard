/**
 * The taskbar: the strip along the bottom of the desktop.
 *
 * It is a **zone container** and nothing more — the zones are named by the
 * `desktop.taskbar` setting, rendered in the order given, and each zone is a
 * component that knows only its own job. Adding a zone is one entry in `ZONES`
 * plus a component; reordering one is a setting the user edits.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import { openContextMenu, useSetting } from '@horrible/core';

import { AgentButton } from './taskbar/AgentButton';
import { Clock } from './taskbar/Clock';
import { MxButton } from './taskbar/MxButton';
import { StartButton } from './taskbar/StartButton';
import { Tray } from './taskbar/Tray';
import { WindowButtons } from './taskbar/WindowButtons';

export const TASKBAR_SETTING_KEY = 'desktop.taskbar';

export interface TaskbarConfig {
  position: 'bottom' | 'top';
  zones: string[];
  showLabels: boolean;
  autoHide: boolean;
  /** Which generation of the zone list this config was written against. */
  zonesVersion?: number;
}

/**
 * The version each zone first shipped in, and the current generation.
 *
 * A stored `zones` array is taken **whole** — it is an explicit choice, and
 * quietly adding to it would defeat the point of letting anyone hide a zone. But
 * that also means a config saved today can never receive a zone added tomorrow:
 * the user would be left with a strip missing a feature they never declined,
 * with nothing to tell them why. So a zone introduced *after* the stored version
 * is inserted, and one the user actually removed is not — the difference the
 * version number exists to record.
 *
 * Bump {@link ZONES_VERSION} and add the new zone to {@link ZONE_SINCE} in the
 * same change that adds it to {@link ZONES}.
 */
export const ZONES_VERSION = 3;

const ZONE_SINCE: Record<string, number> = { mx: 2, agent: 3 };

/**
 * The strip as shipped: launcher, what's open, then the status end of the bar.
 *
 * There is deliberately **no workspace switcher here**. A desktop is a workspace,
 * and switching between them is a rare, deliberate act, not a thing to keep six
 * labels on screen for — the Start menu's Desktops group both switches *and*
 * manages (rename, create, reset, delete), which one strip of pips never could,
 * and a tiling desktop still has the top strip. The taskbar's own width belongs
 * to the panes you actually have open.
 *
 * `agent` is last, past the clock: the orchestrator is the one thing you reach
 * for from anywhere, so it gets the corner every OS reserves for the control you
 * can always hit without looking.
 */
export const DEFAULT_TASKBAR: TaskbarConfig = {
  position: 'bottom',
  zones: ['start', 'windows', 'spacer', 'mx', 'tray', 'clock', 'agent'],
  showLabels: true,
  autoHide: false,
  zonesVersion: ZONES_VERSION,
};

/**
 * Every zone that can appear, and the label the customization menu gives it.
 *
 * The menu is generated from this map, so contributing a zone is still one entry
 * here plus a component — adding a second list of names to keep in step is how a
 * zone ends up renderable but unreachable from the UI that turns zones on.
 */
export const ZONES: Record<
  string,
  { title: string; component: React.ComponentType<{ showLabels: boolean }> }
> = {
  start: { title: 'Start button', component: StartButton },
  windows: { title: 'Open panes', component: WindowButtons },
  mx: { title: 'M-x and echo area', component: MxButton },
  tray: { title: 'Tray', component: Tray },
  clock: { title: 'Clock', component: Clock },
  spacer: { title: 'Spacer', component: () => <div className="os-taskbar-spacer" /> },
  // Last, so `zoneRank` places it past the clock when an older config receives it.
  agent: { title: 'Agent button', component: AgentButton },
};

export function Taskbar() {
  const config = useTaskbarConfig();
  return (
    <footer
      className={`os-taskbar os-taskbar--${config.position}${config.autoHide ? ' is-autohide' : ''}`}
      // Not a `role="toolbar"`: the zones are independent groups with their own
      // semantics, and a single toolbar role would promise one arrow-key ring
      // across all of them.
      aria-label="Taskbar"
      // Right-click the strip itself to customize it. The config has always been
      // a setting; until now the only way to edit it was to hand-write JSON into
      // the settings page, which is not a thing anyone discovers about their own
      // taskbar. A right-click is where every OS puts this.
      onContextMenu={(e) => {
        if (openContextMenu(e, { kind: 'taskbar' })) e.preventDefault();
      }}
    >
      {config.zones.map((zone, i) => {
        const Zone = ZONES[zone]?.component;
        // An unknown zone name — a typo in the setting, or a zone from a newer
        // build — is skipped silently rather than rendered as an error strip.
        // The rest of the taskbar keeps working, which is the point.
        if (!Zone) return null;
        return <Zone key={`${zone}:${i}`} showLabels={config.showLabels} />;
      })}
    </footer>
  );
}

/**
 * The taskbar config, merged over the defaults.
 *
 * Merged, not replaced: a stored config written by an older build is missing keys
 * that exist today, and taking it whole would silently drop them. Only keys
 * actually present override.
 */
export function useTaskbarConfig(): TaskbarConfig {
  const stored = useSetting<string>(TASKBAR_SETTING_KEY);
  return mergeTaskbarConfig(stored);
}

export function mergeTaskbarConfig(stored: unknown): TaskbarConfig {
  const raw = typeof stored === 'string' ? tryParse(stored) : stored;
  if (!raw || typeof raw !== 'object') return DEFAULT_TASKBAR;
  const src = raw as Record<string, unknown>;
  const storedVersion = typeof src.zonesVersion === 'number' ? src.zonesVersion : 1;
  const zones = Array.isArray(src.zones)
    ? withZonesAddedSince(
        src.zones.filter((z): z is string => typeof z === 'string'),
        storedVersion,
      )
    : DEFAULT_TASKBAR.zones;
  return {
    position: src.position === 'top' ? 'top' : 'bottom',
    // An empty list is taken at face value — "no zones" is a legitimate way to
    // ask for a bare strip — but a malformed one falls back to the defaults.
    zones,
    zonesVersion: ZONES_VERSION,
    showLabels: typeof src.showLabels === 'boolean' ? src.showLabels : DEFAULT_TASKBAR.showLabels,
    autoHide: typeof src.autoHide === 'boolean' ? src.autoHide : DEFAULT_TASKBAR.autoHide,
  };
}

/**
 * Add the default zones that did not exist when this config was written, each at
 * its default position. See {@link ZONE_SINCE}.
 */
function withZonesAddedSince(zones: string[], storedVersion: number): string[] {
  const missing = DEFAULT_TASKBAR.zones.filter(
    (z) => (ZONE_SINCE[z] ?? 1) > storedVersion && !zones.includes(z),
  );
  if (missing.length === 0) return zones;
  // Each new zone is spliced in; the existing order is never re-sorted. Sorting
  // the whole array by default rank would silently undo a user's own arrangement
  // as the price of receiving one new zone.
  const out = [...zones];
  for (const zone of missing) {
    // Only *known* zones can decide the insertion point. A stored config often
    // still names a zone this build has removed (`desktops`, until recently),
    // and `zoneRank` scores an unknown name last by definition — so ranking
    // against it put every newly added zone immediately *before* the dead one,
    // which is wherever the user happened to have had it. The name itself is
    // kept: a config written by a newer build must survive a round trip through
    // an older one, and the renderer already skips what it cannot resolve.
    const at = out.findIndex((z) => z in ZONES && zoneRank(z) > zoneRank(zone));
    out.splice(at < 0 ? out.length : at, 0, zone);
  }
  return out;
}

/**
 * A zone's position in the canonical strip order — {@link ZONES}' own key order.
 *
 * Used to place a zone that is being switched *on*, whether by the customization
 * menu or by a version bump. Ranking against `DEFAULT_TASKBAR.zones` instead
 * would send every non-default zone to the far end of the strip, past the clock,
 * which is nobody's idea of where most of them belong.
 */
export function zoneRank(zone: string): number {
  const keys = Object.keys(ZONES);
  const at = keys.indexOf(zone);
  return at < 0 ? keys.length : at;
}

function tryParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
