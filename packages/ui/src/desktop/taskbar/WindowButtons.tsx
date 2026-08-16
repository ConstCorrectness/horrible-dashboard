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
  openContextMenu,
  setPaneWindowed,
  setWindowMode,
  taskbarEntries,
  type TaskbarEntry,
} from '@horrible/core';

export function WindowButtons({ showLabels }: { showLabels: boolean }) {
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const entries = taskbarEntries(frame);
  if (!entries.length) return null;
  return (
    <div className="os-taskbar-windows" role="group" aria-label="Open panes">
      {entries.map((e) => (
        <button
          key={e.instanceId}
          type="button"
          className={`os-taskbar-btn is-${e.state}`}
          // The accessible name always carries the title even when labels are
          // off — an icon-only taskbar is unusable to a screen reader otherwise.
          aria-label={e.title}
          aria-pressed={e.state === 'focused'}
          title={e.title}
          onClick={() => activateTaskbarEntry(e.instanceId)}
          onContextMenu={(ev) => {
            if (openContextMenu(ev, { kind: 'taskbar.window', instanceId: e.instanceId })) {
              ev.preventDefault();
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
  return [
    {
      id: 'taskbar.activate',
      label: entry.state === 'focused' ? 'Minimize' : 'Show',
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
    ...(windowed
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
