import { useEffect } from 'react';

import { useCollab } from '../network';
import { registry, type ModuleManifest } from '../../registry';

/**
 * A minimal non-singleton panel: open as many as you like, drag them into
 * splits/tabs/floating windows. Serves as the reference panel for the windowing
 * system (docs/architecture/windowing.md) until richer modules (editor,
 * terminal) arrive. Content is shared across instances for now (one localStorage
 * key) — a per-instance store lands with real buffer identity.
 *
 * It's also the reference consumer for **network-aware panes**: it declares
 * `collab` in its manifest and drives the `useCollab` host hook, so toggling Share
 * syncs the note live with other users on connected nodes (rev-checked
 * last-writer-wins) and shows a live presence count — with no channel plumbing of
 * its own. See docs/modules/network.mdx (collab).
 */
const STORAGE_KEY = 'horrible.scratch';
// All shared scratch notes (across users/nodes) sync through one well-known room.
const SHARE_KEY = 'scratch:shared';

function ScratchPanel() {
  const { text, setText, shared, setShared, members } = useCollab(SHARE_KEY, {
    initialText: localStorage.getItem(STORAGE_KEY) ?? '',
  });

  useEffect(() => {
    if (shared) return; // while shared, the room is the source of truth
    const id = setTimeout(() => localStorage.setItem(STORAGE_KEY, text), 300);
    return () => clearTimeout(id);
  }, [text, shared]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.25rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.75rem',
        }}
      >
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          Share
        </label>
        {shared && (
          <span style={{ color: 'var(--text-dim)' }}>
            live with peers · {members} {members === 1 ? 'editor' : 'editors'}
          </span>
        )}
      </div>
      <textarea
        className="scratch"
        value={text}
        spellCheck={false}
        placeholder="Scratch notes — open more, split and float them…"
        onChange={(e) => setText(e.target.value)}
        style={{ flex: 1 }}
      />
    </div>
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
      role: 'document',
      icon: '✏',
      // Not a singleton: every open creates a new window.
      // Network-aware: declares the shared collab room it syncs through, so the
      // shell knows this pane participates in the peer fabric.
      collab: { room: 'shared', key: 'scratch:shared' },
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
