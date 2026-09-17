/**
 * The wire traffic a run produced, under its timeline.
 *
 * The live I/O ring holds 500 events and forgets everything else, so before this
 * existed the only honest answer to "what did this turn actually send" was "it is
 * gone". These rows come from `telemetry_events`, joined on `traj_runs.turn_id`.
 *
 * Fetched on demand rather than with the run: most postmortems never open it, and a
 * long turn's HTTP traffic is a good deal larger than its step list.
 */
import { useEffect, useState } from 'react';

import type { RowKind } from '../../../DataList';
import { Button, Chip } from '../../../Primitives';
import { getRunIo, type RunIoEvent } from '../api';
import { heading, mono, ms } from './common';

function statusKind(event: RunIoEvent): RowKind {
  if (event.error) return 'fail';
  if (event.verdict === 'blocked') return 'fail';
  if (event.status == null) return 'info';
  if (event.status >= 500) return 'fail';
  if (event.status >= 400) return 'warn';
  return 'ok';
}

/** `http://host:port/a/b?c` → `host/a/b`, which is what a reader scans for. */
function shortTarget(target: string): string {
  try {
    const url = new URL(target);
    return `${url.host}${url.pathname}`;
  } catch {
    return target;
  }
}

function bytes(value: number | null): string {
  if (value == null) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function EventRow({ event }: { event: RunIoEvent }) {
  const [open, setOpen] = useState(false);
  const detail = event.detail;
  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: 'var(--space-2) 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <Chip kind={statusKind(event)}>{event.status ?? event.verdict ?? '—'}</Chip>
        <span style={{ ...mono, minWidth: 44 }}>{event.method}</span>
        <span style={{ ...mono, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {shortTarget(event.target)}
        </span>
        {event.round != null ? <span style={mono}>r{event.round}</span> : null}
        <span style={mono}>{ms(event.duration_ms)}</span>
        <span style={mono}>{bytes(event.response_bytes)}</span>
        {detail ? (
          <Button intent="ghost" size="sm" onClick={() => setOpen(!open)}>
            {open ? 'hide' : 'headers'}
          </Button>
        ) : null}
      </div>
      {event.error ? (
        <div style={{ ...mono, color: 'var(--danger)', marginTop: 'var(--space-1)' }}>
          {event.error}
        </div>
      ) : null}
      {open && detail ? (
        <pre
          style={{
            ...mono,
            margin: 'var(--space-2) 0 0',
            padding: 'var(--space-3)',
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            overflow: 'auto',
            maxHeight: 240,
          }}
        >
          {JSON.stringify(detail, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}

export function IoLane({ runId, hasTurn }: { runId: string; hasTurn: boolean }) {
  const [events, setEvents] = useState<RunIoEvent[] | null>(null);
  const [joinable, setJoinable] = useState(true);
  const [error, setError] = useState('');
  const [shown, setShown] = useState(false);

  useEffect(() => {
    setEvents(null);
    setShown(false);
    setError('');
  }, [runId]);

  useEffect(() => {
    if (!shown) return;
    let cancelled = false;
    getRunIo(runId)
      .then((res) => {
        if (cancelled) return;
        setEvents(res.events);
        setJoinable(res.joinable);
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [runId, shown]);

  // A run with no turn has nothing to join against and never will. Saying so beats
  // an expander that always opens on an empty list.
  if (!hasTurn) return null;

  if (!shown) {
    return (
      <div style={{ marginBottom: 'var(--space-5)' }}>
        <Button intent="ghost" size="sm" onClick={() => setShown(true)}>
          Show network
        </Button>
      </div>
    );
  }

  return (
    <div style={{ marginBottom: 'var(--space-5)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-2)',
        }}
      >
        <span style={heading}>Network</span>
        {events ? <span style={mono}>{events.length} requests</span> : null}
      </div>
      {error ? (
        <div role="alert" style={{ ...mono, color: 'var(--danger)' }}>
          {error}
        </div>
      ) : events === null ? (
        <div style={mono}>Loading…</div>
      ) : !joinable ? (
        <div style={mono}>
          This run carries no turn id, so there is no wire to join to — imported and SDK runs record
          their own steps but not this node&apos;s I/O.
        </div>
      ) : events.length === 0 ? (
        <div style={mono}>
          Nothing stored for this turn. Either it made no requests, or they aged out of the
          retention window.
        </div>
      ) : (
        events.map((event) => (
          <EventRow key={`${event.boot_id}:${event.ev_id}`} event={event} />
        ))
      )}
    </div>
  );
}
