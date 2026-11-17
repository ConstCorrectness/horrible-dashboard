/**
 * The agent chat's sessions rail: the conversations of the selected agent, newest
 * first and bucketed by recency, with search, rename and delete — the left column
 * of the chat pane (ChatWidget). It replaced a native `<select>` that hid every
 * conversation but the current one behind a click.
 *
 * Presentational only: every action is ChatWidget's (it owns the session store
 * calls and the in-flight-turn rules), so this file cannot fork a conversation by
 * accident. See docs/modules/agent-chat.mdx.
 */
import { useMemo, useState, type KeyboardEvent } from 'react';

import { IconClose, IconPencil, IconPlus, IconSearch, IconTrash } from '../../glyphs';
import type { RosterAgent } from './api';
import { filterSessions, groupSessions, shortAge } from './session-groups';
import type { ChatSessionMeta } from './sessions';

export interface SessionsRailProps {
  sessions: ChatSessionMeta[];
  activeId: string | null;
  roster: RosterAgent[];
  agentId: string;
  /** A chat pinned to one agent shows no agent switcher. */
  pinned: boolean;
  /** A turn is streaming: switching agents (and the active row) is held. */
  busy: boolean;
  /** Overlay mode (narrow pane): show a close button. */
  overlay: boolean;
  onPick: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPickAgent: (id: string) => void;
  onClose: () => void;
}

/** Cap on the entrance stagger, so a long list does not take seconds to arrive. */
const STAGGER_CAP = 8;

export function SessionsRail({
  sessions,
  activeId,
  roster,
  agentId,
  pinned,
  busy,
  overlay,
  onPick,
  onNew,
  onDelete,
  onRename,
  onPickAgent,
  onClose,
}: SessionsRailProps) {
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);
  const groups = useMemo(() => groupSessions(filterSessions(sessions, query)), [sessions, query]);

  const commitRename = () => {
    if (!editing) return;
    const title = editing.title.trim();
    const was = sessions.find((s) => s.id === editing.id)?.title;
    setEditing(null);
    if (title && title !== was) onRename(editing.id, title);
  };
  // The input's own handler, not a global listener: Escape here means "cancel the
  // rename", and it must not reach the shell's Escape ladder as well.
  const onRenameKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      setEditing(null);
    }
  };

  let index = 0;
  return (
    <aside className="agent-rail" aria-label="Conversations" data-overlay={overlay || undefined}>
      <div className="agent-rail-head">
        {roster.length > 1 && !pinned ? (
          <select
            className="agent-rail-agent"
            value={agentId}
            onChange={(e) => onPickAgent(e.target.value)}
            aria-label="Agent"
            title={roster.find((a) => a.id === agentId)?.description ?? ''}
            disabled={busy}
          >
            {roster.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="agent-rail-agent-name">
            {roster.find((a) => a.id === agentId)?.name ?? agentId}
          </span>
        )}
        {overlay ? (
          <button
            type="button"
            className="agent-icon-btn"
            onClick={onClose}
            title="Hide conversations"
            aria-label="Hide conversations"
          >
            <IconClose />
          </button>
        ) : null}
      </div>
      <button type="button" className="agent-rail-new" onClick={onNew}>
        <IconPlus />
        <span>New chat</span>
      </button>
      <label className="agent-rail-search">
        <IconSearch />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search chats"
          aria-label="Search chats"
        />
      </label>
      <nav className="agent-rail-list">
        {groups.length === 0 ? (
          <p className="agent-rail-empty">{query ? 'No chats match.' : 'No chats yet.'}</p>
        ) : (
          groups.map((g) => (
            <section key={g.label} className="agent-rail-group">
              <h4 className="agent-rail-group-label">{g.label}</h4>
              <ul>
                {g.sessions.map((s) => {
                  const active = s.id === activeId;
                  const i = Math.min(index++, STAGGER_CAP);
                  if (editing?.id === s.id) {
                    return (
                      <li key={s.id} className="agent-rail-item" data-active={active || undefined}>
                        <input
                          type="text"
                          className="agent-rail-rename"
                          value={editing.title}
                          // The rename was asked for by this click: focus belongs here.
                          autoFocus
                          onChange={(e) => setEditing({ id: s.id, title: e.target.value })}
                          onKeyDown={onRenameKey}
                          onBlur={commitRename}
                          aria-label="Chat title"
                        />
                      </li>
                    );
                  }
                  return (
                    <li
                      key={s.id}
                      className="agent-rail-item"
                      data-active={active || undefined}
                      style={{ ['--i' as string]: i }}
                    >
                      <button
                        type="button"
                        className="agent-rail-row"
                        aria-current={active || undefined}
                        onClick={() => onPick(s.id)}
                        title={s.title}
                      >
                        {active && busy ? (
                          <span className="agent-rail-pulse" aria-label="running" />
                        ) : null}
                        <span className="agent-rail-title">{s.title}</span>
                        <span className="agent-rail-age">{shortAge(s.updated)}</span>
                      </button>
                      <span className="agent-rail-actions">
                        <button
                          type="button"
                          className="agent-icon-btn"
                          title="Rename"
                          aria-label={`Rename “${s.title}”`}
                          onClick={() => setEditing({ id: s.id, title: s.title })}
                        >
                          <IconPencil />
                        </button>
                        <button
                          type="button"
                          className="agent-icon-btn agent-icon-btn-danger"
                          title="Delete"
                          aria-label={`Delete “${s.title}”`}
                          disabled={active && busy}
                          onClick={() => onDelete(s.id)}
                        >
                          <IconTrash />
                        </button>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))
        )}
      </nav>
    </aside>
  );
}
