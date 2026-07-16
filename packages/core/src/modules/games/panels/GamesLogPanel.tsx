import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';

import { useGames } from '../game-ws';
import {
  clearGamesLog,
  STREAM_ICON,
  STREAM_LABEL,
  useGamesLog,
  type LogEntry,
  type LogStream,
} from '../games-log';
import { TraceRow } from './TraceRow';

/**
 * **Games Log** (`games.log`) — everything that happened this match, in one place:
 * your agent's reasoning, the referee's events, connection/auth, and errors. This
 * replaced the Agent Thoughts pane; thoughts are now the `agent` stream here, because
 * debugging a harness means reading reasoning *against* what the server actually did.
 *
 * Filter chips scope the view to one or more streams; rows expand to the raw payload.
 * Autoscroll follows the tail, and turns itself off the moment you scroll up to read
 * something (a log that yanks you away from the line you're reading is useless).
 *
 * See docs/modules/games.mdx.
 */

const STREAMS: LogStream[] = ['agent', 'match', 'server', 'error'];

const chip = (on: boolean): CSSProperties => ({
  display: 'flex',
  alignItems: 'center',
  gap: 4,
  padding: '0.15rem 0.45rem',
  borderRadius: 999,
  fontSize: '0.68rem',
  cursor: 'pointer',
  border: `1px solid ${on ? 'var(--accent, #6ea8fe)' : 'var(--border, #33343a)'}`,
  background: on ? 'color-mix(in srgb, var(--accent, #6ea8fe) 14%, transparent)' : 'transparent',
  color: on ? 'var(--text)' : 'var(--text-dim)',
});

function stamp(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(
    2,
    '0',
  )}:${String(d.getSeconds()).padStart(2, '0')}`;
}

export function GamesLogPanel() {
  const all = useGamesLog();
  const { matchSeats } = useGames();
  const [on, setOn] = useState<Set<LogStream>>(() => new Set(STREAMS));
  const [expanded, setExpanded] = useState<number | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  // Follow the tail until the reader scrolls away from it.
  const [follow, setFollow] = useState(true);

  const rows = useMemo(() => all.filter((e) => on.has(e.stream)), [all, on]);

  useEffect(() => {
    if (!follow) return;
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows.length, follow]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setFollow(atBottom);
  };

  const toggle = (s: LogStream) =>
    setOn((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', fontSize: '0.78rem' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          flexWrap: 'wrap',
          padding: '0.35rem 0.6rem',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <span style={{ color: 'var(--text-dim)' }}>📜 Log {matchSeats ? '· live' : ''}</span>
        <span style={{ flex: 1 }} />
        {STREAMS.map((s) => {
          const n = all.filter((e) => e.stream === s).length;
          return (
            <button key={s} type="button" onClick={() => toggle(s)} style={chip(on.has(s))}>
              <span aria-hidden>{STREAM_ICON[s]}</span>
              {STREAM_LABEL[s]}
              <span style={{ color: 'var(--text-faint, #666)' }}>{n}</span>
            </button>
          );
        })}
        <button
          type="button"
          onClick={clearGamesLog}
          title="Clear the log"
          style={{ ...chip(false), border: 'none' }}
        >
          clear
        </button>
      </div>

      <div
        ref={scroller}
        onScroll={onScroll}
        style={{ flex: 1, overflow: 'auto', padding: '0.3rem 0.5rem' }}
      >
        {rows.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', padding: '0.3rem' }}>
            {all.length === 0
              ? 'Nothing yet. Everything your agent and the game server do lands here once a match starts — set the games.policy setting to agent so a model, not the random policy, picks moves.'
              : 'No entries in the selected streams.'}
          </div>
        ) : (
          rows.map((e) => (
            <Row
              key={e.id}
              entry={e}
              expanded={expanded === e.id}
              onToggle={() => setExpanded(expanded === e.id ? null : e.id)}
            />
          ))
        )}
      </div>

      {!follow && (
        <button
          type="button"
          onClick={() => setFollow(true)}
          style={{
            ...chip(true),
            justifyContent: 'center',
            margin: '0 0.5rem 0.4rem',
            padding: '0.25rem',
          }}
        >
          ↓ follow live
        </button>
      )}
    </div>
  );
}

function Row({
  entry,
  expanded,
  onToggle,
}: {
  entry: LogEntry;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      style={{
        borderBottom: '1px solid color-mix(in srgb, var(--border) 45%, transparent)',
        padding: '0.2rem 0',
      }}
    >
      <div
        onClick={onToggle}
        style={{ display: 'flex', gap: 6, alignItems: 'baseline', cursor: 'pointer' }}
      >
        <span
          style={{
            fontFamily: 'var(--font-mono, monospace)',
            fontSize: '0.66rem',
            color: 'var(--text-faint, #666)',
          }}
        >
          {stamp(entry.ts)}
        </span>
        <span aria-hidden>{STREAM_ICON[entry.stream]}</span>
        <span
          style={{
            flex: 1,
            color: entry.stream === 'error' ? 'var(--danger, #f87171)' : 'var(--text)',
          }}
        >
          {entry.text}
        </span>
        {entry.event && (
          <span
            style={{
              fontFamily: 'var(--font-mono, monospace)',
              fontSize: '0.62rem',
              color: 'var(--text-faint, #666)',
            }}
          >
            {entry.event}
          </span>
        )}
      </div>
      {/* An agent row expands to the full reasoning step; anything else to its payload. */}
      {expanded && (
        <div style={{ padding: '0.25rem 0 0.35rem 1.4rem' }}>
          {entry.step ? (
            <TraceRow step={entry.step} />
          ) : (
            <pre
              style={{
                margin: 0,
                fontSize: '0.68rem',
                color: 'var(--text-dim)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {JSON.stringify(entry.detail, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
