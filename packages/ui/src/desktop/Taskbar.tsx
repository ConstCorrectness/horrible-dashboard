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
import { useSetting } from '@horrible/core';

import { Clock } from './taskbar/Clock';
import { DesktopSwitcher } from './taskbar/DesktopSwitcher';
import { StartButton } from './taskbar/StartButton';
import { Tray } from './taskbar/Tray';
import { WindowButtons } from './taskbar/WindowButtons';

export const TASKBAR_SETTING_KEY = 'desktop.taskbar';

export interface TaskbarConfig {
  position: 'bottom' | 'top';
  zones: string[];
  showLabels: boolean;
  autoHide: boolean;
}

export const DEFAULT_TASKBAR: TaskbarConfig = {
  position: 'bottom',
  zones: ['start', 'windows', 'spacer', 'desktops', 'tray', 'clock'],
  showLabels: true,
  autoHide: false,
};

const ZONES: Record<string, React.ComponentType<{ showLabels: boolean }>> = {
  start: StartButton,
  windows: WindowButtons,
  desktops: DesktopSwitcher,
  tray: Tray,
  clock: Clock,
  spacer: () => <div className="os-taskbar-spacer" />,
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
    >
      {config.zones.map((zone, i) => {
        const Zone = ZONES[zone];
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
 * Merged, not replaced: a stored config written by an older build has no
 * `desktops` zone, and taking it whole would silently remove a zone the user
 * never chose to remove. Only keys actually present override.
 */
export function useTaskbarConfig(): TaskbarConfig {
  const stored = useSetting<string>(TASKBAR_SETTING_KEY);
  return mergeTaskbarConfig(stored);
}

export function mergeTaskbarConfig(stored: unknown): TaskbarConfig {
  const raw = typeof stored === 'string' ? tryParse(stored) : stored;
  if (!raw || typeof raw !== 'object') return DEFAULT_TASKBAR;
  const src = raw as Record<string, unknown>;
  const zones = Array.isArray(src.zones)
    ? src.zones.filter((z): z is string => typeof z === 'string')
    : DEFAULT_TASKBAR.zones;
  return {
    position: src.position === 'top' ? 'top' : 'bottom',
    // An empty list is taken at face value — "no zones" is a legitimate way to
    // ask for a bare strip — but a malformed one falls back to the defaults.
    zones,
    showLabels: typeof src.showLabels === 'boolean' ? src.showLabels : DEFAULT_TASKBAR.showLabels,
    autoHide: typeof src.autoHide === 'boolean' ? src.autoHide : DEFAULT_TASKBAR.autoHide,
  };
}

function tryParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
