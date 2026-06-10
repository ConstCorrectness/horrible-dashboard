import { useEffect, useState } from 'react';
import { registry } from '@horrible/core';

import { CommandPalette } from './CommandPalette';
import './styles.css';

function matchesBinding(e: KeyboardEvent, key: string): boolean {
  const wantsMod = key.startsWith('mod+');
  const plain = wantsMod ? key.slice(4) : key;
  const hasMod = e.ctrlKey || e.metaKey;
  return wantsMod === hasMod && e.key.toLowerCase() === plain;
}

export function AppShell({ appTitle }: { appTitle: string }) {
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
      ],
      keybindings: [{ key: 'mod+k', command: 'shell.commandPalette' }],
    });
    registry.setPanelOpener(setActivePanelId);
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
      <header className="shell-header">
        <div className="shell-brand">
          <img src="/logo.svg" alt="" className="shell-logo" />
          <h1>{appTitle}</h1>
        </div>
        <button onClick={() => setPaletteOpen(true)}>Commands (Ctrl+K)</button>
      </header>
      <div className="shell-body">
        <nav className="shell-sidebar">
          {panels.map((p) => (
            <button
              key={p.id}
              className={p.id === active?.id ? 'active' : ''}
              onClick={() => setActivePanelId(p.id)}
            >
              {p.title}
            </button>
          ))}
        </nav>
        <main className="shell-main">
          {active ? <active.component /> : <p className="shell-empty">No panels registered.</p>}
        </main>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
