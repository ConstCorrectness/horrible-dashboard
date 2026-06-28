import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  dialogs,
  getActiveScope,
  registry,
  resolveKeybinding,
  toastsStore,
  useWorkspaces,
  type OpenPaneOptions,
  type ShellView,
} from '@horrible/core';

import { ApprovalPrompts } from './ApprovalPrompts';
import { CommandPalette } from './CommandPalette';
import { Dialogs } from './Dialogs';
import { Toasts } from './Toasts';
import { HomeView } from './HomeView';
import { Workspace } from './Workspace';
import './styles.css';

export function AppShell({ appTitle }: { appTitle: string }) {
  const [view, setView] = useState<ShellView>('home');
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The rail is the workspace switcher: predefined workflow layouts plus any
  // custom workspaces, with the active one highlighted (state owned by Workspace,
  // read here via the shared store).
  const { workspaces, activeId } = useWorkspaces();

  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    workspaceId: string;
    workspaceName: string;
    isPreset: boolean;
  } | null>(null);

  useEffect(() => {
    if (!contextMenu) return;
    const clickOutside = () => setContextMenu(null);
    window.addEventListener('mousedown', clickOutside);
    window.addEventListener('keydown', clickOutside);
    return () => {
      window.removeEventListener('mousedown', clickOutside);
      window.removeEventListener('keydown', clickOutside);
    };
  }, [contextMenu]);

  const handleRename = async (id: string, currentName: string) => {
    setContextMenu(null);
    const newName = await dialogs.prompt({
      title: 'Rename workspace',
      defaultValue: currentName,
      confirmLabel: 'Rename',
    });
    if (newName && newName.trim()) {
      await registry.layoutController?.renameWorkspace(id, newName.trim());
    }
  };

  const handleDelete = async (id: string) => {
    const name = contextMenu?.workspaceName;
    setContextMenu(null);
    const ok = await dialogs.confirm({
      title: 'Delete workspace',
      message: `“${name}” and its layout will be removed. This can't be undone.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (ok) {
      await registry.layoutController?.deleteWorkspace(id);
      toastsStore.add('info', 'Workspace deleted', `“${name}” was removed.`);
    }
  };
  // Bumped each time a panel should open, so the Workspace reacts even to repeats.
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
      ],
      keybindings: [{ key: 'mod+k', command: 'shell.commandPalette' }],
    });
    // Opening any panel switches to the workspace and tells it which to open.
    registry.setPanelOpener((panelId, opts) => {
      setView('workspace');
      setPendingOpen({ panelId, opts, nonce: nonce.current++ });
    });
    // Switching workspaces also enters the workspace view, then signals the tab.
    registry.setWorkspaceSwitcher((workspaceId) => {
      setView('workspace');
      setPendingWorkspace({ workspaceId, nonce: nonce.current++ });
    });
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const command = resolveKeybinding(e, getActiveScope(), registry.keybindings);
      if (command) {
        e.preventDefault();
        void registry.runCommand(command);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Mount the Workspace the first time it's shown (mounting dockview inside a
  // hidden, zero-size container mis-measures), then keep it alive across views.
  const [workspaceMounted, setWorkspaceMounted] = useState(false);
  useEffect(() => {
    if (view === 'workspace') setWorkspaceMounted(true);
  }, [view]);

  // Rail = every predefined workflow layout (always shown, even before first
  // open), then any custom workspace that isn't one of them.
  const presetIds = new Set(registry.layouts.map((p) => p.id));
  const railEntries = [
    ...registry.layouts.map((p) => ({ id: p.id, glyph: p.icon ?? p.name[0], title: p.name })),
    ...workspaces
      .filter((w) => !presetIds.has(w.id))
      .map((w) => ({ id: w.id, glyph: w.name[0], title: w.name })),
  ];

  return (
    <div className="shell">
      <nav className="shell-rail">
        <button
          className={`rail-logo ${view === 'home' ? 'active' : ''}`}
          title="Home"
          onClick={() => setView('home')}
        >
          <img src="/logo.svg" alt="Home" />
        </button>
        {railEntries.map((entry) => (
          <button
            key={entry.id}
            className={entry.id === activeId ? 'active' : ''}
            title={entry.title}
            onClick={() => registry.switchWorkspace(entry.id)}
            onContextMenu={(e) => {
              e.preventDefault();
              setContextMenu({
                x: e.clientX,
                y: e.clientY,
                workspaceId: entry.id,
                workspaceName: entry.title,
                isPreset: presetIds.has(entry.id),
              });
            }}
          >
            {entry.glyph}
          </button>
        ))}
        <button title="New workspace" onClick={() => void registry.runCommand('workspace.new')}>
          ＋
        </button>
        <div className="rail-spacer" />
        <button title="Commands (Ctrl+K)" onClick={() => setPaletteOpen(true)}>
          ⌘
        </button>
      </nav>
      <div className="shell-content">
        <div hidden={view !== 'home'} className="shell-view">
          <HomeView />
        </div>
        {workspaceMounted && (
          <div hidden={view !== 'workspace'} className="shell-view">
            <Workspace pendingOpen={pendingOpen} pendingWorkspace={pendingWorkspace} />
          </div>
        )}
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ApprovalPrompts />
      <Toasts />
      <Dialogs />
      {contextMenu &&
        createPortal(
          <div
            className="pane-tab-menu"
            style={{
              left: contextMenu.x,
              top: contextMenu.y,
            }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <button
              className="pane-tab-menu-item"
              onClick={() => handleRename(contextMenu.workspaceId, contextMenu.workspaceName)}
            >
              Rename
            </button>
            {!contextMenu.isPreset && (
              <button
                className="pane-tab-menu-item"
                style={{ color: 'var(--danger, #f7768e)' }}
                onClick={() => handleDelete(contextMenu.workspaceId)}
              >
                Delete
              </button>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
