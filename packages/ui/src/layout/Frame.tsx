/**
 * The tiling frame: an activity rail down each side, tool docks, and a
 * Blender-style centre area grid, plus the fullscreen-area mode. Renders
 * straight off the layout store.
 *
 * It is mounted only while the active desktop's `mode` is `tiling` — that is why
 * there is no rail/dock branch inside it. A floating desktop shows its backdrop
 * and its windows instead, and both live above this component's head.
 *
 * Installing the engine — controller, autosave, commands, hydration — is NOT
 * done here: it belongs to the shell and lives in `layout/install.ts`, because
 * the desktop is the landing surface and this component may never mount at all.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { findArea, layoutStore, setCenterMeasurer } from '@horrible/core';

import { ActivityRail } from './ActivityRail';
import { Area } from './Area';
import { Minibuffer } from './Minibuffer';
import { CenterGrid } from './CenterGrid';
import { Dock } from './Dock';
import './frame.css';

export function Frame() {
  const state = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const centerRef = useRef<HTMLDivElement>(null);

  // Only the DOM-bound part stays here: the center grid's measurer, which is the
  // one thing that genuinely needs this component's ref. Everything else the
  // frame installs (controller, autosave, commands, hydration) is shell-level
  // and now runs from `installFrameShell()` — see layout/install.ts.
  useEffect(() => {
    setCenterMeasurer(() => {
      const el = centerRef.current;
      return el ? { width: el.clientWidth, height: el.clientHeight } : null;
    });
    return () => {
      setCenterMeasurer(null);
    };
  }, []);

  // Escape exits fullscreen via the shell's Escape ladder (rung 5), so that a
  // dialog or a capturing pane gets the key first. The command and menu paths
  // are unchanged.
  const { frame } = state;
  const fullscreen = frame.fullscreenAreaId ? findArea(frame.center, frame.fullscreenAreaId) : null;

  return (
    <div className="frame-root">
      <div className="frame-main">
        <ActivityRail side="left" frame={frame} />
        {fullscreen ? (
          <div className="frame-center" ref={centerRef}>
            <Area area={fullscreen} focused fullscreen />
          </div>
        ) : (
          <>
            <Dock side="left" dock={frame.docks.left} />
            <div className="frame-center-stack">
              <div className="frame-center" ref={centerRef}>
                <CenterGrid node={frame.center} focusedAreaId={frame.focusedAreaId} />
              </div>
              <Dock side="bottom" dock={frame.docks.bottom} />
            </div>
            <Dock side="right" dock={frame.docks.right} />
          </>
        )}
        <ActivityRail side="right" frame={frame} />
      </div>
      <Minibuffer />
    </div>
  );
}
