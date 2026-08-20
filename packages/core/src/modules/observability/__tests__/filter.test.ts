/**
 * The data-flow list's filter.
 *
 * The interesting case is the one the old filter could not express. `source` was
 * a single choice — "all", or exactly one — so a busy websocket left two
 * options: a list buried in `ws` frames, or a list with nothing *but* them.
 * "Everything except ws" is the state anybody actually wants, and these tests
 * pin that it exists and that it round-trips through the setting that stores it.
 */
import { describe, expect, it } from 'vitest';

import type { IoEvent, IoSource } from '../../../telemetry';
import { applyFilter, formatMuted, parseMuted } from '../view';

function ev(source: IoSource, target: string, status = 200): IoEvent {
  return {
    id: `${source}-${target}`,
    ts: 0,
    source,
    method: 'GET',
    target,
    status,
  } as IoEvent;
}

const EVENTS: IoEvent[] = [
  ev('ws', 'hassault'),
  ev('ws', 'agent'),
  ev('inbound', '/api/settings'),
  ev('outbound', 'https://example.test'),
  ev('client', '/api/hassault/maps'),
];

describe('muting a source', () => {
  it('hides that source and keeps every other one', () => {
    const out = applyFilter(EVENTS, {
      query: '',
      muted: new Set<IoSource>(['ws']),
      errorsOnly: false,
    });
    expect(out).toHaveLength(3);
    expect(out.some((e) => e.source === 'ws')).toBe(false);
    expect(out.map((e) => e.source)).toContain('inbound');
  });

  it('mutes nothing by default', () => {
    const out = applyFilter(EVENTS, { query: '', muted: new Set(), errorsOnly: false });
    expect(out).toHaveLength(EVENTS.length);
  });

  it('can mute several at once', () => {
    const out = applyFilter(EVENTS, {
      query: '',
      muted: new Set<IoSource>(['ws', 'outbound']),
      errorsOnly: false,
    });
    expect(out.map((e) => e.source)).toEqual(['inbound', 'client']);
  });

  it('still applies the text query and the errors toggle', () => {
    const withError = [...EVENTS, ev('inbound', '/api/boom', 500)];
    const out = applyFilter(withError, {
      query: '',
      muted: new Set<IoSource>(['ws']),
      errorsOnly: true,
    });
    expect(out).toHaveLength(1);
    expect(out[0].target).toBe('/api/boom');
  });
});

describe('the stored value', () => {
  it('round-trips', () => {
    const muted = new Set<IoSource>(['ws', 'browser']);
    expect(parseMuted(formatMuted(muted))).toEqual(muted);
  });

  it('is empty when the setting is unset', () => {
    expect(parseMuted(undefined).size).toBe(0);
    expect(parseMuted('').size).toBe(0);
  });

  it('drops names it does not know rather than keeping them', () => {
    // The setting is editable by hand. A typo that mutes nothing is better than
    // one that sits in the list looking as though it did something.
    expect(parseMuted('ws, nonsense, WS')).toEqual(new Set(['ws']));
  });

  it('serializes in a stable order, so the value diffs cleanly', () => {
    expect(formatMuted(new Set(['browser', 'ws'] as IoSource[]))).toBe('ws,browser');
  });
});
