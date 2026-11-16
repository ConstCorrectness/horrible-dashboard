/**
 * One button per open pane. The list, the labels and the four states all come
 * from `taskbarEntries` — this file decides only how they look and which verbs a
 * click and a right-click run.
 */
import { useSyncExternalStore } from 'react';
import {
  activateTaskbarEntry,
  closePaneGuarded,
  findPaneAnywhere,
  layoutStore,
  openContextMenu,
  taskbarEntries,
  toggleDock,
  toggleMaximizedGroup,
  type TaskbarEntry,
} from '@horrible/core';

import { useHorizontalWheel } from '../../hooks/useHorizontalWheel';

export function WindowButtons({ showLabels }: { showLabels: boolean }) {
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const entries = taskbarEntries(frame);
  // Called before the early return: a hook after a conditional return is a
  // different hook count between renders.
  const wheelRef = useHorizontalWheel<HTMLDivElement>();
  if (!entries.length) return null;
  return (
    <div className="os-taskbar-windows" ref={wheelRef} role="group" aria-label="Open panes">
      {entries.map((e) => (
        <button
          key={e.instanceId}
          data-taskbar-instance={e.instanceId}
          type="button"
          className={`os-taskbar-btn is-${e.state}${showLabels ? '' : ' is-iconic'}${e.attention ? ' wants-attention' : ''}`}
          // The accessible name always carries the title even when labels are
          // off — an icon-only taskbar is unusable to a screen reader otherwise.
          // A flashing button is invisible to a screen reader, so the state that
          // flash conveys is said out loud instead.
          aria-label={e.attention ? `${e.title} — finished` : e.title}
          aria-pressed={e.state === 'focused'}
          title={e.title}
          onClick={() => activateTaskbarEntry(e.instanceId)}
          // Double-click puts a docked pane away, whatever the first click did:
          // the single click toggles (show ⇄ hide), so on a pane that was not
          // showing the pair reads as "bring it up, then put it away".
          onDoubleClick={() => hideDockOf(e.instanceId)}
          onContextMenu={(ev) => {
            if (openContextMenu(ev, { kind: 'taskbar.window', instanceId: e.instanceId })) {
              ev.preventDefault();
              // `preventDefault` only stops the *browser* menu. Without stopping
              // propagation the event carried on to the taskbar container, whose own
              // handler opened the taskbar menu on top of this one — so a right-click
              // on a window button showed Position / Auto-hide / Reset, and Close was
              // unreachable even though it has always been in this menu.
              ev.stopPropagation();
            }
          }}
          onAuxClick={(ev) => {
            // Middle-click closes, the browser-tab convention. Guarded, so a
            // dirty pane still gets to prompt.
            if (ev.button === 1) {
              ev.preventDefault();
              void closePaneGuarded(e.instanceId);
            }
          }}
        >
          <span className="os-taskbar-icon" aria-hidden="true">
            {e.icon}
          </span>
          {showLabels && <span className="os-taskbar-label">{e.title}</span>}
        </button>
      ))}
    </div>
  );
}

/** Hide the dock holding `instanceId`. A centre pane has nowhere to be put away to. */
function hideDockOf(instanceId: string): boolean {
  const found = findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId);
  return found?.location.kind === 'dock' ? toggleDock(found.location.side, false) : false;
}

/** The per-button right-click menu, contributed by the `desktop` module. */
export function windowButtonMenu(entry: TaskbarEntry) {
  // Showing (focused or merely on screen) offers Hide for a docked pane; out of
  // sight (a hidden dock, or a background tab) offers Show.
  const onScreen = entry.state === 'focused' || entry.state === 'open';
  const found = findPaneAnywhere(layoutStore.getSnapshot().frame, entry.instanceId);
  const docked = found?.location.kind === 'dock';
  return [
    ...(onScreen
      ? docked
        ? [{ id: 'taskbar.minimize', label: 'Hide dock', run: () => void hideDockOf(entry.instanceId) }]
        : []
      : [
          {
            id: 'taskbar.activate',
            label: 'Show',
            run: () => void activateTaskbarEntry(entry.instanceId),
          },
        ]),
    {
      id: 'taskbar.maximize',
      label: 'Maximize',
      run: () => {
        activateTaskbarEntry(entry.instanceId);
        toggleMaximizedGroup(entry.instanceId);
      },
    },
    {
      id: 'taskbar.close',
      label: 'Close',
      danger: true,
      run: () => void closePaneGuarded(entry.instanceId),
    },
  ];
}
