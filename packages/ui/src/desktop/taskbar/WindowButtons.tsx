/**
 * One button per open pane. The list, the labels and the four states all come
 * from `taskbarEntries` — this file decides only how they look and which verbs a
 * click and a right-click run.
 */
import { useSyncExternalStore } from 'react';
import {
  activateTaskbarEntry,
  closePaneGuarded,
  layoutStore,
  minimizePane,
  openContextMenu,
  setPaneWindowed,
  setWindowMode,
  taskbarEntries,
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
          // Double-click always minimizes, whatever the first click did. The
          // single click toggles (show ⇄ hide), so on a pane that was not
          // showing the pair reads as "bring it up, then put it away" — which
          // is what a user double-clicking a taskbar button is asking for.
          onDoubleClick={() => minimizePane(e.instanceId)}
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

/** The per-button right-click menu, contributed by the `desktop` module. */
export function windowButtonMenu(entry: TaskbarEntry) {
  const windowed = entry.location.kind === 'window';
  // Showing (focused or merely on screen) offers Minimize; out of sight
  // (minimized, or a background tab) offers Show. Naming the verb by the state
  // rather than by focus is what makes Minimize reachable for a window you can
  // see but have not clicked into.
  const onScreen = entry.state === 'focused' || entry.state === 'open';
  return [
    onScreen
      ? {
          id: 'taskbar.minimize',
          label: 'Minimize',
          run: () => void minimizePane(entry.instanceId),
        }
      : {
          id: 'taskbar.activate',
          label: 'Show',
          run: () => void activateTaskbarEntry(entry.instanceId),
        },
    windowed
      ? {
          id: 'taskbar.maximize',
          label: 'Maximize',
          run: () => void setWindowMode(entry.windowId!, 'maximized'),
        }
      : {
          id: 'taskbar.window',
          label: 'Open in a window',
          run: () => void setPaneWindowed(entry.instanceId, true),
        },
    // Dock-back only on a tiling desktop. On a floating one the frame is retained
    // but never drawn, so "dock back" and "minimize" have the same visible effect
    // and the same recovery — and the row above already offers Minimize by name.
    // See the matching note on the window's ⤵ button.
    ...(windowed && layoutStore.getSnapshot().frame.mode !== 'floating'
      ? [
          {
            id: 'taskbar.dock',
            label: 'Dock back into the frame',
            run: () => void setPaneWindowed(entry.instanceId, false),
          },
        ]
      : []),
    {
      id: 'taskbar.close',
      label: 'Close',
      danger: true,
      run: () => void closePaneGuarded(entry.instanceId),
    },
  ];
}
