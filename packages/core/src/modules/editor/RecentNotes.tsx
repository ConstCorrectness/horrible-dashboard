import { useCallback, useEffect, useState } from 'react';

import { openBuffer } from './index';
import { createNote, listNotes, type NoteMeta } from './sources';

/**
 * The `editor.recentNotes` dashboard widget (C5): the most-recently-updated notes,
 * click to open as a `note:` buffer, plus a "New note" affordance. Backend data
 * (`GET /notes`) so it is identical in both layouts; refreshes on focus so opening
 * or saving a note elsewhere reflects here. See docs/modules/editor.md.
 */
function relativeTime(epochSeconds: number): string {
  const seconds = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function RecentNotesWidget() {
  const [notes, setNotes] = useState<NoteMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listNotes()
      .then((list) => {
        setNotes(list);
        setError(null);
      })
      .catch((e: unknown) => {
        setNotes(null);
        setError(String(e));
      });
  }, []);

  useEffect(() => {
    refresh();
    // Reflect notes created/saved elsewhere when the user returns to this tab.
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, [refresh]);

  const onNewNote = useCallback(async () => {
    const source = await createNote();
    openBuffer(source);
    refresh();
  }, [refresh]);

  return (
    <div className="recent-notes">
      <div className="recent-notes-toolbar">
        <button onClick={() => void onNewNote()}>New note</button>
        <button onClick={refresh} title="Refresh">
          ↻
        </button>
      </div>
      {error && <p className="recent-notes-empty">Notes unavailable — is the backend running?</p>}
      {!error && notes?.length === 0 && (
        <p className="recent-notes-empty">No notes yet — create one to get started.</p>
      )}
      {notes && notes.length > 0 && (
        <ul className="recent-notes-list">
          {notes.map((note) => (
            <li
              key={note.id}
              className="recent-note-row"
              onClick={() => openBuffer(`note:${note.id}`)}
            >
              <span className="recent-note-title">{note.title || 'Untitled'}</span>
              <span className="recent-note-time">{relativeTime(note.updated_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
