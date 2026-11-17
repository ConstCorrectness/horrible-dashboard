/**
 * One pop-out: a tab group drawn into its own OS window.
 *
 * The window is opened and styled by ./popout-window.ts; this renders the group's
 * strip and the active pane's {@link PaneSlot} into it through a portal, and wires
 * the window into the shell-wide listeners (`registerPopoutWindow`: the keymap,
 * focus tracking) and the drag-event forwarder.
 *
 * What a pop-out does **not** have, on purpose: dialogs and Spotlight stay in the
 * main window (they are the app's, not the pane's), and tabs here cannot be
 * dragged — `resolveDrop` knows only the main window's geometry. "Dock back" and
 * the OS close button both return the panes to the workbench.
 */
import { memo, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  dockPopoutBack,
  focusGroup,
  forwardPointerEvents,
  paneDisplayTitle,
  PaneWindowContext,
  registerPopoutWindow,
  type PaneWindow,
  type PopoutState,
} from '@horrible/core';

import { ContextMenuLayer } from '../overlay/ContextMenu';
import { GroupHeader } from './GroupHeader';
import { PaneSlot } from './pane-hosts';
import { acquirePopoutWindow, releasePopoutWindow, type PopoutHandle } from './popout-window';

const NO_DRAG = () => {};
const NO_SUPPRESS = () => false;

export const PopoutHost = memo(function PopoutHost({
  popout,
  focused,
  presentedInstanceId,
  appTitle,
  onWindow,
}: {
  popout: PopoutState;
  focused: boolean;
  presentedInstanceId: string | null;
  appTitle: string;
  /** Tell the workbench which window this pop-out's panes are in (null: gone). */
  onWindow: (id: string, pw: PaneWindow | null) => void;
}) {
  const [handle, setHandle] = useState<PopoutHandle | null>(null);
  const { id } = popout;

  // Open once per pop-out id. The bounds it opens with are the remembered ones;
  // afterwards the shell reports moves, and nothing here re-applies them.
  useEffect(() => {
    const h = acquirePopoutWindow(id, popout.bounds);
    if (!h) {
      // Refused (no pop-outs here, or blocked): the panes must not vanish.
      queueMicrotask(() => dockPopoutBack(id));
      return;
    }
    setHandle(h);
    onWindow(id, { win: h.win, popoutId: id });
    const unregister = registerPopoutWindow(id, h.win);
    const stopForward = forwardPointerEvents(h.win);
    return () => {
      stopForward();
      unregister();
      onWindow(id, null);
      releasePopoutWindow(id);
    };
    // `popout.bounds` is read once, at open, on purpose (see above).
  }, [id, onWindow]);

  const area = popout.area;
  const active = area.tabs[area.activeTab];
  const title = active ? paneDisplayTitle(active) : 'Empty';

  useEffect(() => {
    if (handle) handle.win.document.title = `${title} — ${appTitle}`;
  }, [handle, title, appTitle]);

  const paneWindow = useMemo<PaneWindow | null>(
    () => (handle ? { win: handle.win, popoutId: id } : null),
    [handle, id],
  );

  if (!handle || !paneWindow) return null;
  return createPortal(
    <PaneWindowContext.Provider value={paneWindow}>
      <div
        className="wb-popout"
        data-wb-group={area.id}
        onPointerDownCapture={() => focusGroup(area.id)}
        style={{
          position: 'fixed',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--win-bg, var(--bg-raised))',
          color: 'var(--text)',
        }}
      >
        <GroupHeader
          area={area}
          host={{ kind: 'popout', popoutId: id }}
          focused={focused}
          maximized={false}
          presentedInstanceId={presentedInstanceId}
          onTabPointerDown={NO_DRAG}
          suppressClick={NO_SUPPRESS}
        />
        {active ? (
          <PaneSlot
            key={active.instanceId}
            instanceId={active.instanceId}
            className="wb-pane"
            data-wb-pane={active.instanceId}
            style={{ position: 'relative', flex: 1 }}
          />
        ) : null}
      </div>
      <ContextMenuLayer view={handle.win} />
    </PaneWindowContext.Provider>,
    handle.root,
  );
});
