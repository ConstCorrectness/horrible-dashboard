/**
 * The frame: workspace tab strip on top, an activity rail down each side + tool
 * docks around a
 * Blender-style center area grid, with the floating layer and fullscreen-area
 * mode. Renders straight off the layout store; on mount it installs the
 * LayoutController (the seam agent tools drive), binds the debounced autosave,
 * and hydrates the active workspace from the server.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  executeTool,
  findArea,
  focusAreaDirection,
  framePersistence,
  fullscreenArea,
  fullscreenFocusedArea,
  installFrameController,
  joinAreaDirection,
  layoutStore,
  movePaneDirection,
  openFramePane,
  registry,
  setCenterMeasurer,
  splitAreaBy,
  toggleDock,
  minibuffer,
  toggleRegion,
  workspaceStore,
  type OpenPaneOptions,
} from '@horrible/core';

import { ActivityRail } from './ActivityRail';
import { Area } from './Area';
import { Minibuffer } from './Minibuffer';
import { CenterGrid } from './CenterGrid';
import { Dock } from './Dock';
import { FloatingLayer } from './FloatingLayer';
import './frame.css';

export function Frame({
  pendingOpen,
  pendingWorkspace,
}: {
  pendingOpen?: { panelId: string; opts?: OpenPaneOptions; nonce: number };
  pendingWorkspace?: { workspaceId: string; nonce: number };
}) {
  const state = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const centerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    installFrameController();
    framePersistence.bindAutosave();
    if (import.meta.env.DEV) {
      // Dev/E2E handle (the frame-engine analogue of __horribleWorkspace).
      // `exec` runs a relayed tool by name — the same entry point agent/REPL
      // calls hit — so layout verbs can be exercised from the console.
      (
        window as Window & {
          __horribleFrame?: {
            store: typeof layoutStore;
            registry: typeof registry;
            exec: typeof executeTool;
          };
        }
      ).__horribleFrame = { store: layoutStore, registry, exec: executeTool };
    }
    setCenterMeasurer(() => {
      const el = centerRef.current;
      return el ? { width: el.clientWidth, height: el.clientHeight } : null;
    });
    // Frame-owned commands (the palette/keybinding surface for shell chrome).
    // Directional commands act on the FOCUSED area, vim/i3 style.
    const focusedAreaId = () => layoutStore.getSnapshot().frame.focusedAreaId;
    const NAVS = ['left', 'right', 'up', 'down'] as const;
    const SPLIT_FOR: Record<(typeof NAVS)[number], 'left' | 'right' | 'above' | 'below'> = {
      left: 'left',
      right: 'right',
      up: 'above',
      down: 'below',
    };
    const nthWorkspace = (n: number): string | undefined => {
      const presets = registry.framePresets.map((p) => p.id);
      const customs = workspaceStore
        .getSnapshot()
        .workspaces.map((w) => w.id)
        .filter((id) => !presets.includes(id));
      return [...presets, ...customs][n - 1];
    };
    registry.register({
      id: 'frame',
      title: 'Frame',
      commands: [
        {
          id: 'area.fullscreen',
          title: 'Area: Toggle fullscreen',
          run: () => fullscreenFocusedArea(),
        },
        ...NAVS.map((dir) => ({
          id: `area.focus:${dir}`,
          title: `Area: Focus ${dir}`,
          run: () => void focusAreaDirection(dir),
        })),
        ...NAVS.map((dir) => ({
          id: `area.split:${dir}`,
          title: `Area: Split ${dir}`,
          run: () => {
            const areaId = focusedAreaId();
            if (areaId) splitAreaBy(areaId, SPLIT_FOR[dir]);
          },
        })),
        ...NAVS.map((dir) => ({
          id: `pane.move:${dir}`,
          title: `Pane: Move ${dir}`,
          run: () => void movePaneDirection(dir),
        })),
        {
          id: 'area.join',
          title: 'Area: Join neighbor',
          run: () => {
            const areaId = focusedAreaId();
            if (!areaId) return;
            for (const dir of NAVS) if (joinAreaDirection(areaId, dir)) break;
          },
        },
        {
          id: 'minibuffer.open',
          title: 'Minibuffer: Run a command (M-x)',
          run: () => minibuffer.open('/'),
        },
        { id: 'dock.toggle:left', title: 'Dock: Toggle left', run: () => void toggleDock('left') },
        {
          id: 'dock.toggle:right',
          title: 'Dock: Toggle right',
          run: () => void toggleDock('right'),
        },
        {
          id: 'dock.toggle:bottom',
          title: 'Dock: Toggle bottom',
          run: () => void toggleDock('bottom'),
        },
        {
          id: 'region.toggle:left',
          title: 'Region: Toggle left',
          run: () => {
            const areaId = focusedAreaId();
            if (!areaId) return;
            const area = findArea(layoutStore.getSnapshot().frame.center, areaId);
            const active = area?.tabs[area.activeTab];
            if (active) void toggleRegion(active.instanceId, 'left');
          },
        },
        {
          id: 'region.toggle:right',
          title: 'Region: Toggle right',
          run: () => {
            const areaId = focusedAreaId();
            if (!areaId) return;
            const area = findArea(layoutStore.getSnapshot().frame.center, areaId);
            const active = area?.tabs[area.activeTab];
            if (active) void toggleRegion(active.instanceId, 'right');
          },
        },
        {
          id: 'region.toggle:bottom',
          title: 'Region: Toggle bottom',
          run: () => {
            const areaId = focusedAreaId();
            if (!areaId) return;
            const area = findArea(layoutStore.getSnapshot().frame.center, areaId);
            const active = area?.tabs[area.activeTab];
            if (active) void toggleRegion(active.instanceId, 'bottom');
          },
        },
        ...Array.from({ length: 9 }, (_, i) => ({
          id: `workspace.switch:${i + 1}`,
          title: `Workspace: Switch to #${i + 1}`,
          run: () => {
            const id = nthWorkspace(i + 1);
            if (id) registry.switchWorkspace(id);
          },
        })),
      ],
      keybindings: [
        { key: 'ctrl+space', command: 'area.fullscreen' },
        // `override` so a focused editor pane can't shadow it — the minibuffer
        // is the escape hatch and has to be reachable from anywhere.
        { key: 'alt+x', command: 'minibuffer.open', override: true },
        ...NAVS.map((dir) => ({ key: `alt+${dir}`, command: `area.focus:${dir}` })),
        ...NAVS.map((dir) => ({ key: `alt+shift+${dir}`, command: `pane.move:${dir}` })),
        ...NAVS.map((dir) => ({ key: `mod+alt+${dir}`, command: `area.split:${dir}` })),
        { key: 'mod+alt+j', command: 'area.join' },
        { key: 'mod+b', command: 'dock.toggle:left' },
        { key: 'mod+alt+b', command: 'dock.toggle:right' },
        { key: 'mod+j', command: 'dock.toggle:bottom' },
        { key: 't', command: 'region.toggle:left' },
        { key: 'n', command: 'region.toggle:right' },
        { key: 'b', command: 'region.toggle:bottom' },
        ...Array.from({ length: 9 }, (_, i) => ({
          key: `mod+${i + 1}`,
          command: `workspace.switch:${i + 1}`,
        })),
      ],
    });
    void framePersistence.hydrate().finally(() => setReady(true));
    return () => {
      setCenterMeasurer(null);
    };
  }, []);

  // Escape exits fullscreen (in addition to the command/menu paths).
  useEffect(() => {
    if (!state.frame.fullscreenAreaId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') fullscreenArea(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [state.frame.fullscreenAreaId]);

  // Pending opens/switches from the shell (registry.openPanel / switchWorkspace),
  // replayed once hydration finishes so boot-time requests aren't lost.
  const lastOpenNonce = useRef(-1);
  const lastSwitchNonce = useRef(-1);
  useEffect(() => {
    if (!ready) return;
    if (pendingOpen && pendingOpen.nonce !== lastOpenNonce.current) {
      lastOpenNonce.current = pendingOpen.nonce;
      openFramePane(pendingOpen.panelId, pendingOpen.opts);
    }
    if (pendingWorkspace && pendingWorkspace.nonce !== lastSwitchNonce.current) {
      lastSwitchNonce.current = pendingWorkspace.nonce;
      void framePersistence.switchWorkspace(pendingWorkspace.workspaceId);
    }
  }, [ready, pendingOpen, pendingWorkspace]);

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
                <FloatingLayer floating={frame.floating} containerRef={centerRef} />
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
