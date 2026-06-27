import { useEffect, useRef, useState } from 'react';

import { collabJoin, collabLeave, collabOp, subscribeCollab } from '../network';
import { registry, type ModuleManifest } from '../../registry';

/**
 * A minimal non-singleton panel: open as many as you like, drag them into
 * splits/tabs/floating windows. Serves as the reference panel for the windowing
 * system (docs/architecture/windowing.md) until richer modules (editor,
 * terminal) arrive. Content is shared across instances for now (one localStorage
 * key) — a per-instance store lands with real buffer identity.
 *
 * It's also the reference consumer for **collaborative shared panes**: toggling
 * Share joins a `collab` room (a fixed pane key) so the note syncs live with other
 * users on connected nodes — rev-checked last-writer-wins. See docs/modules/network.mdx.
 */
const STORAGE_KEY = 'horrible.scratch';
// All shared scratch notes (across users/nodes) sync through one well-known room.
const SHARE_KEY = 'scratch:shared';

function ScratchPanel() {
  const [text, setText] = useState(() => localStorage.getItem(STORAGE_KEY) ?? '');
  const [shared, setShared] = useState(false);
  // Last revision the backend acked, sent as baseRev with the next edit.
  const revRef = useRef(0);

  useEffect(() => {
    if (shared) return; // while shared, the room is the source of truth
    const id = setTimeout(() => localStorage.setItem(STORAGE_KEY, text), 300);
    return () => clearTimeout(id);
  }, [text, shared]);

  useEffect(() => {
    if (!shared) return;
    const unsub = subscribeCollab(SHARE_KEY, (update) => {
      revRef.current = update.rev;
      setText(update.text);
    });
    collabJoin(SHARE_KEY);
    return () => {
      collabLeave(SHARE_KEY);
      unsub();
    };
  }, [shared]);

  const onChange = (next: string) => {
    setText(next);
    if (shared) collabOp(SHARE_KEY, revRef.current, next);
  };

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
        {shared && <span style={{ color: 'var(--text-dim)' }}>live with peers</span>}
      </div>
      <textarea
        className="scratch"
        value={text}
        spellCheck={false}
        placeholder="Scratch notes — open more, split and float them…"
        onChange={(e) => onChange(e.target.value)}
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
