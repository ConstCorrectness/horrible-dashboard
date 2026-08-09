import { beforeEach, describe, expect, it } from 'vitest';

import { karaokeModule } from '../index';
import type { PlayerState } from '../api';
import { mediaUrl } from '../api';
import { applyPlayerState, getPlayerState, resetKaraokeForTests } from '../store';

function state(overrides: Partial<PlayerState> = {}): PlayerState {
  return {
    now_playing: null,
    playing: false,
    position: 0,
    duration: null,
    volume: 1,
    semitones: 0,
    queue: [],
    history: [],
    autoplay: true,
    revision: 1,
    ...overrides,
  };
}

describe('karaoke module manifest', () => {
  it('declares the stage as a capturing document pane', () => {
    const stage = karaokeModule.panels?.find((p) => p.id === 'karaoke.stage');
    expect(stage?.role).toBe('document');
    expect(stage?.singleton).toBe(true);
    // Without capture the single-letter transport bindings below would fight the
    // shell's own bindings whenever the stage has focus.
    expect(stage?.capture?.mode).toBe('keyboard');
  });

  it('docks the queue and the search panes on opposite sides', () => {
    const queue = karaokeModule.panels?.find((p) => p.id === 'karaoke.queue');
    const search = karaokeModule.panels?.find((p) => p.id === 'karaoke.search');
    expect(queue?.role).toBe('tool');
    expect(queue?.defaultDock).toBe('left');
    expect(search?.defaultDock).toBe('right');
  });

  it('scopes every single-key binding to the stage', () => {
    // Unscoped, `space` and `n` would be stolen from every text field in the app.
    for (const binding of karaokeModule.keybindings ?? []) {
      expect(binding.when).toBe("paneFocus == 'karaoke.stage'");
    }
    const commands = (karaokeModule.keybindings ?? []).map((k) => k.command);
    expect(commands).toContain('karaoke.playPause');
    expect(commands).toContain('karaoke.next');
  });

  it('binds every keybinding to a command it declares', () => {
    const declared = new Set((karaokeModule.commands ?? []).map((c) => c.id));
    for (const binding of karaokeModule.keybindings ?? []) {
      expect(declared).toContain(binding.command);
    }
  });

  it('ships a Karaoke frame with the stage centered', () => {
    const frame = karaokeModule.frames?.find((f) => f.id === 'karaoke');
    expect(frame).toBeDefined();
    expect(frame?.frame.center).toEqual({ pane: 'karaoke.stage' });
    expect(frame?.frame.docks?.left?.tools).toContain('karaoke.queue');
    expect(frame?.frame.docks?.right?.tools).toContain('karaoke.search');
  });
});

describe('pending entries', () => {
  // The stage derives `pending` from this exact expression; the rule it encodes
  // is that an entry queued mid-download must NOT get a <video>, because a media
  // element that 404s sets error.code 4 and never retries.
  const pendingOf = (entry: { ready: boolean } | null) => Boolean(entry) && entry?.ready === false;

  it('treats a not-yet-downloaded entry as pending', () => {
    expect(pendingOf({ ready: false })).toBe(true);
  });

  it('treats a downloaded entry as playable', () => {
    expect(pendingOf({ ready: true })).toBe(false);
  });

  it('is not pending when nothing is playing', () => {
    expect(pendingOf(null)).toBe(false);
  });
});

describe('media url', () => {
  it('omits the semitones parameter at the original key', () => {
    // The plain path is the seekable one — it must not be shadowed by a `?0`
    // that would route through the transcode branch.
    expect(mediaUrl('abc')).toBe('/api/karaoke/media/abc');
  });

  it('carries the key in the URL so a change is a source change', () => {
    expect(mediaUrl('abc', -2)).toBe('/api/karaoke/media/abc?semitones=-2');
  });
});

describe('player state store', () => {
  beforeEach(() => {
    resetKaraokeForTests();
  });

  it('adopts a newer revision', () => {
    applyPlayerState(state({ revision: 3, volume: 0.5 }));
    expect(getPlayerState().volume).toBe(0.5);
  });

  it('drops a stale broadcast', () => {
    // A reconnect replay or two racing mutations deliver an older snapshot;
    // rendering it makes the queue visibly jump backwards.
    applyPlayerState(state({ revision: 5, volume: 0.5 }));
    applyPlayerState(state({ revision: 2, volume: 1 }));
    expect(getPlayerState().volume).toBe(0.5);
    expect(getPlayerState().revision).toBe(5);
  });
});
