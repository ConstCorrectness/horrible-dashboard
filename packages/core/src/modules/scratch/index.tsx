import { useEffect, useState } from 'react';

import { registry, type ModuleManifest } from '../../registry';

/**
 * A minimal non-singleton panel: open as many as you like, drag them into
 * splits/tabs/floating windows. Serves as the reference panel for the windowing
 * system (docs/architecture/windowing.md) until richer modules (editor,
 * terminal) arrive. Content is shared across instances for now (one localStorage
 * key) — a per-instance store lands with real buffer identity.
 */
const STORAGE_KEY = 'horrible.scratch';

function ScratchPanel() {
  const [text, setText] = useState(() => localStorage.getItem(STORAGE_KEY) ?? '');

  useEffect(() => {
    const id = setTimeout(() => localStorage.setItem(STORAGE_KEY, text), 300);
    return () => clearTimeout(id);
  }, [text]);

  return (
    <textarea
      className="scratch"
      value={text}
      spellCheck={false}
      placeholder="Scratch notes — open more, split and float them…"
      onChange={(e) => setText(e.target.value)}
    />
  );
}

export const scratchModule: ModuleManifest = {
  id: 'scratch',
  title: 'Scratch',
  panels: [
    {
      id: 'scratch.note',
      title: 'Scratch',
      component: ScratchPanel,
      defaultPlacement: 'center',
      // Not a singleton: every open creates a new window.
    },
  ],
  commands: [
    {
      id: 'scratch.open',
      title: 'Scratch: New note',
      run: () => registry.openPanel('scratch.note'),
    },
  ],
};
