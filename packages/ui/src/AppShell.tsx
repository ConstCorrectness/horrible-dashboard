import { useEffect, useState } from 'react';
import { registry, type ShellView } from '@horrible/core';

import { CommandPalette } from './CommandPalette';
import { HomeView } from './HomeView';
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
  const [activePanelId, setActivePanelId] = useState<string | null>(null);

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
      ],
      keybindings: [{ key: 'mod+k', command: 'shell.commandPalette' }],
    });
    registry.setViewOpener(setView);
    registry.setPanelOpener((id) => {
      setActivePanelId(id);
      setView('workspace');
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

  const panels = registry.panels;
  const active = panels.find((p) => p.id === activePanelId) ?? panels[0];

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
        {panels.map((p) => (
          <button
            key={p.id}
            title={p.title}
            className={view === 'workspace' && p.id === active?.id ? 'active' : ''}
            onClick={() => registry.openPanel(p.id)}
          >
            {p.title[0]}
          </button>
        ))}
        <div className="rail-spacer" />
        <button title="Commands (Ctrl+K)" onClick={() => setPaletteOpen(true)}>
          ⌘
        </button>
      </nav>
      <div className="shell-content">
        {view === 'home' ? (
          <HomeView />
        ) : (
          <>
            <header className="shell-header">
              <h1>{appTitle}</h1>
              <button onClick={() => setPaletteOpen(true)}>Commands (Ctrl+K)</button>
            </header>
            <main className="shell-main">
              {active ? <active.component /> : <p className="shell-empty">No panels registered.</p>}
            </main>
          </>
        )}
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
