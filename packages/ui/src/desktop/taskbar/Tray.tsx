/**
 * The tray: small always-on indicators and the two switches that belong at the
 * edge of the screen rather than buried in a menu.
 *
 * Deliberately short. A tray that accumulates one icon per module becomes the
 * thing nobody reads; anything richer belongs in a pane.
 */
import { useSyncExternalStore } from 'react';
import {
  backendHealth,
  hasCapability,
  layoutStore,
  THEMES,
  useThemeId,
  setSetting,
  THEME_SETTING_KEY,
  openContextMenu,
  type ContextMenuItem,
} from '@horrible/core';

import { useAppFullscreen } from '../../hooks/useAppFullscreen';

export function Tray({ showLabels }: { showLabels: boolean }) {
  const health = useSyncExternalStore(backendHealth.subscribe, backendHealth.getSnapshot);
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const themeId = useThemeId();
  const tiling = frame.mode === 'tiling';
  // The tray is where fullscreen becomes discoverable. F11 exists only on the
  // native shell (the browser owns that key), and the titlebar cluster only
  // renders under `chrome.workspaceTabs` — so without this button the browser
  // layout has no fullscreen affordance at all.
  const canFullscreen = hasCapability('window.fullscreen');
  const { fullscreen, toggle: toggleFullscreen } = useAppFullscreen();

  // Three states, not two. `null` is "the first probe has not answered yet",
  // which is not the same fact as "the backend is down" and must not be painted
  // like one — the app shows a red light for a second on every cold start
  // otherwise.
  const backend = health.reachable === null ? 'unknown' : health.reachable ? 'online' : 'offline';

  return (
    <div className="os-taskbar-tray" role="group" aria-label="Status">
      {/* An indicator, not a toggle.
       *
       * The paradigm is a property of the **workspace** now — you pick it when you
       * make a desktop (Start ▸ New tiled / New floating), and you switch paradigms
       * by switching desktops. This used to be a one-click flip, which invited the
       * one operation the shell cannot do well: `explodeToWindows` cannot express
       * split ratios as anything but the rects they occupy and `tileWindows` cannot
       * recover the ratios you dragged, so every round trip quietly degraded the
       * arrangement. Converting in place is still possible, but it now lives in the
       * command palette where a destructive verb belongs rather than under a button
       * sitting next to the clock.
       */}
      <button
        type="button"
        className="os-tray-btn"
        aria-label={tiling ? 'Tiling desktop' : 'Floating desktop'}
        title={
          tiling
            ? 'Tiling desktop — new desktops are made from the Start menu'
            : 'Floating desktop — new desktops are made from the Start menu'
        }
        aria-haspopup="menu"
        onClick={(ev) => {
          const r = ev.currentTarget.getBoundingClientRect();
          openContextMenu({ clientX: r.left, clientY: r.top }, { kind: 'taskbar.mode' });
        }}
      >
        {tiling ? '▦' : '❐'}
      </button>
      <button
        type="button"
        className="os-tray-btn"
        aria-label="Theme"
        title={`Theme: ${THEMES.find((t) => t.id === themeId)?.title ?? themeId}`}
        // Left-click opens the same menu a right-click would: the picker is this
        // button's only job, and a tray icon that needs a right-click to do its
        // one thing is a tray icon nobody finds.
        onClick={(ev) => {
          openContextMenu(ev, { kind: 'taskbar.theme' });
        }}
      >
        ◐
      </button>
      {canFullscreen && (
        <button
          type="button"
          className="os-tray-btn"
          aria-label={fullscreen ? 'Leave fullscreen' : 'Fullscreen'}
          aria-pressed={fullscreen}
          title={fullscreen ? 'Leave fullscreen' : 'Fullscreen'}
          onClick={toggleFullscreen}
        >
          {fullscreen ? '⤡' : '⛶'}
        </button>
      )}
      <span
        className={`os-tray-health is-${backend}`}
        title={
          backend === 'unknown'
            ? 'Checking the backend…'
            : backend === 'online'
              ? `Backend ${health.version ?? ''} online`
              : `Backend unreachable: ${health.error ?? 'no response'}`
        }
      >
        <span className="os-tray-dot" aria-hidden="true" />
        {showLabels && <span className="os-tray-text">{backend}</span>}
        <span className="visually-hidden">Backend {backend}</span>
      </span>
    </div>
  );
}

/**
 * The theme picker, shown from the tray's ◐ button and from the app menu.
 *
 * The description goes in `detail`, not `hint`: a hint shares the row with the
 * label, and a full sentence there took the whole width and left the theme's
 * *name* — the one thing you are choosing by — ellipsized away.
 */
export function themeMenuItems(currentId: string): ContextMenuItem[] {
  return THEMES.map((t) => ({
    id: `theme:${t.id}`,
    label: t.title,
    detail: t.description,
    checked: t.id === currentId,
    run: () => void setSetting(THEME_SETTING_KEY, t.id),
  }));
}
