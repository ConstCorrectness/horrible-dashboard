import { useEffect, useRef, useState } from 'react';
import {
  getActiveScope,
  hasCapability,
  isEditableTarget,
  registry,
  resolveKeybinding,
  SymbolSearchModal,
  toastsStore,
  windowControl,
  type OpenPaneOptions,
  type ShellView,
} from '@horrible/core';

import { ApprovalPrompts } from './ApprovalPrompts';
import { CommandPalette } from './CommandPalette';
import { Dialogs } from './Dialogs';
import { Toasts } from './Toasts';
import { HomeView } from './HomeView';
import { Frame } from './layout/Frame';
import { WindowResizeHandles } from './layout/WindowChrome';
import { WorkspaceTabs } from './layout/WorkspaceTabs';
import './styles.css';

/**
 * The shell: the workspace tab strip on top (always visible, Blender-style),
 * then either the `home` view or the frame (the workspace engine — activity
 * rail, docks, center area grid). Global chrome (palette, toasts, dialogs,
 * keybinding dispatch) lives here. See docs/architecture/layout-shell.mdx.
 */
export function AppShell({
  appTitle,
  initialWorkspaceId,
}: {
  appTitle: string;
  /** When set (a per-workspace OS window), boot into this workspace, not home. */
  initialWorkspaceId?: string;
}) {
  const [view, setView] = useState<ShellView>(initialWorkspaceId ? 'workspace' : 'home');
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Bumped each time a panel should open, so the Frame reacts even to repeats.
  const [pendingOpen, setPendingOpen] = useState<{
    panelId: string;
    opts?: OpenPaneOptions;
    nonce: number;
  }>();
  // Same pattern for switching to a named workspace tab.
  const [pendingWorkspace, setPendingWorkspace] = useState<{
    workspaceId: string;
    nonce: number;
  }>();
  const nonce = useRef(0);

  useEffect(() => {
    document.title = appTitle;
  }, [appTitle]);

  useEffect(() => {
    // OS-window fullscreen is a phase-2 native-shell capability (F11); distinct
    // from the frame's in-window `area.fullscreen` (ctrl+space). Only register
    // it where a native shell grants the capability, so the browser keeps its
    // own native F11 and the palette stays clean.
    const nativeFullscreen = hasCapability('window.fullscreen');
    registry.register({
      id: 'shell',
      title: 'Shell',
      commands: [
        {
          id: 'shell.commandPalette',
          title: 'Open command palette',
          run: () => setPaletteOpen(true),
        },
        { id: 'shell.home', title: 'Go home', run: () => setView('home') },
        { id: 'shell.workspace', title: 'Go to workspace', run: () => setView('workspace') },
        ...(nativeFullscreen
          ? [
              {
                id: 'shell.toggleFullscreen',
                title: 'Window: Toggle fullscreen',
                run: async () => {
                  const on = await windowControl()?.toggleFullscreen();
                  if (on !== undefined) {
                    toastsStore.add(
                      'info',
                      on ? 'Fullscreen' : 'Windowed',
                      on ? 'Press F11 to exit.' : 'Press F11 for fullscreen.',
                      1500,
                    );
                  }
                },
              },
            ]
          : []),
      ],
      keybindings: [
        { key: 'mod+k', command: 'shell.commandPalette' },
        ...(nativeFullscreen ? [{ key: 'f11', command: 'shell.toggleFullscreen' }] : []),
      ],
    });
    // Opening any panel switches to the workspace and tells it which to open.
    registry.setPanelOpener((panelId, opts) => {
      setView('workspace');
      setPendingOpen({ panelId, opts, nonce: nonce.current++ });
    });
    // Switching workspaces also enters the workspace view, then signals the frame.
    registry.setWorkspaceSwitcher((workspaceId) => {
      setView('workspace');
      setPendingWorkspace({ workspaceId, nonce: nonce.current++ });
    });
    // Per-workspace OS window: open straight into the requested workspace once
    // the frame is mounted (it replays this pending switch after hydration).
    if (initialWorkspaceId) {
      setPendingWorkspace({ workspaceId: initialWorkspaceId, nonce: nonce.current++ });
    }
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Plain-letter bindings (Blender-style region toggles) must not hijack
      // typing. Ignore no-modifier keydowns originating in a text field; `mod+`
      // shortcuts still work (they carry ctrl/meta).
      if (!e.ctrlKey && !e.metaKey && isEditableTarget(e.target)) return;
      const command = resolveKeybinding(e, getActiveScope(), registry.keybindings);
      if (command) {
        e.preventDefault();
        void registry.runCommand(command);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Mount the Frame the first time it's shown, then keep it alive across views
  // so pane state (terminals, editors) survives visiting home.
  const [workspaceMounted, setWorkspaceMounted] = useState(false);
  useEffect(() => {
    if (view === 'workspace') setWorkspaceMounted(true);
  }, [view]);

  return (
    <div className="shell shell--frame">
      {hasCapability('chrome.workspaceTabs') && <WindowResizeHandles />}
      <WorkspaceTabs />
      <div className="shell-content">
        <div hidden={view !== 'home'} className="shell-view">
          <HomeView />
        </div>
        {workspaceMounted && (
          <div hidden={view !== 'workspace'} className="shell-view">
            <Frame pendingOpen={pendingOpen} pendingWorkspace={pendingWorkspace} />
          </div>
        )}
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <SymbolSearchModal />
      <ApprovalPrompts />
      <Toasts />
      <Dialogs />
    </div>
  );
}
