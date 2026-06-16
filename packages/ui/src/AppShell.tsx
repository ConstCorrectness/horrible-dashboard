import { useEffect, useRef, useState } from 'react';
import { registry, type OpenPaneOptions, type ShellView } from '@horrible/core';

import { ApprovalPrompts } from './ApprovalPrompts';
import { CommandPalette } from './CommandPalette';
import { HomeView } from './HomeView';
import { Workspace } from './Workspace';
import './styles.css';

function matchesBinding(e: KeyboardEvent, key: string): boolean {
  const wantsMod = key.startsWith('mod+');
  const plain = wantsMod ? key.slice(4) : key;
  const hasMod = e.ctrlKey || e.metaKey;
  return wantsMod === hasMod && e.key.toLowerCase() === plain;
}

export function AppShell({ appTitle }: { appTitle: string }) {
  const [view, setView] = useState<ShellView>('home');
  const [paletteOpen, setPaletteOpen] = useState(false);
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
      const binding = registry.keybindings.find((b) => matchesBinding(e, b.key));
      if (binding) {
        e.preventDefault();
        void registry.runCommand(binding.command);
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
        {registry.panels.map((p) => (
          <button key={p.id} title={`Open ${p.title}`} onClick={() => registry.openPanel(p.id)}>
            {p.title[0]}
          </button>
        ))}
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
    </div>
  );
}
