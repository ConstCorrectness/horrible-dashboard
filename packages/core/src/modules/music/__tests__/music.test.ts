import { beforeEach, describe, expect, it } from 'vitest';

import { musicModule } from '../index';
import { mediaUrl, type PlayerState, type QueueEntry } from '../api';
import { planReconcile } from '../player';
import {
  applyPlayerState,
  getPlayerState,
  getProgress,
  resetMusicForTests,
  EMPTY_STATE,
} from '../store';

const ME = 'window-a';

function entry(overrides: Partial<QueueEntry> = {}): QueueEntry {
  return {
    entry_id: 'e1',
    track_id: 't1',
    title: 'Africa',
    artist: 'Toto',
    album: 'Toto IV',
    duration: 295,
    art_hash: '',
    ready: true,
    reason: '',
    played_at: null,
    ...overrides,
  };
}

function state(overrides: Partial<PlayerState> = {}): PlayerState {
  return { ...EMPTY_STATE, revision: 1, ...overrides };
}

const idle = { loadedEntryId: null, currentTime: 0, paused: true };

describe('music module manifest', () => {
  it('hosts the library and queue as strips of the player', () => {
    const player = musicModule.panels?.find((p) => p.id === 'music.player');
    expect(player?.role).toBe('document');
    const at = (id: string) => player?.regions?.find((r) => r.id === id)?.position;
    expect(at('music.library')).toBe('left');
    expect(at('music.queue')).toBe('right');
    for (const id of ['music.library', 'music.queue']) {
      expect(musicModule.panels?.find((p) => p.id === id)?.embedded).toBe(true);
    }
  });

  it('shows now-playing in the shell, because the audio outlives every pane', () => {
    expect(musicModule.shellIndicators?.map((s) => s.id)).toContain('music.nowPlaying');
  });

  it('scopes single-key bindings to the player pane', () => {
    for (const binding of musicModule.keybindings ?? []) {
      expect(binding.when).toBe("paneFocus == 'music.player'");
      expect(musicModule.commands?.some((c) => c.id === binding.command)).toBe(true);
    }
  });

  it('namespaces every setting under music.', () => {
    for (const setting of musicModule.settings ?? [])
      expect(setting.key.startsWith('music.')).toBe(true);
  });
});

describe('music store', () => {
  beforeEach(() => resetMusicForTests());

  it('drops stale broadcasts', () => {
    applyPlayerState(state({ revision: 5, volume: 0.5 }));
    applyPlayerState(state({ revision: 3, volume: 0.1 }));
    expect(getPlayerState().volume).toBe(0.5);
  });

  it('carries the state position into the progress slot', () => {
    applyPlayerState(state({ revision: 2, position: 42, duration: 200 }));
    expect(getProgress()).toEqual({ position: 42, duration: 200 });
  });
});

describe('planReconcile', () => {
  it('asks for the output when something plays and no window owns it', () => {
    const plan = planReconcile(
      state({ now_playing: entry(), playing: true, output_client: null }),
      idle,
      ME,
    );
    expect(plan).toMatchObject({ claim: true, play: false, owner: false });
  });

  it('stays silent when another window owns the output', () => {
    const plan = planReconcile(
      state({ now_playing: entry(), playing: true, output_client: 'window-b' }),
      idle,
      ME,
    );
    expect(plan).toMatchObject({ claim: false, play: false, owner: false });
    expect(plan.load).toBeUndefined();
  });

  it('loads and plays the current entry when this window owns it', () => {
    const plan = planReconcile(
      state({ now_playing: entry(), playing: true, output_client: ME }),
      idle,
      ME,
    );
    expect(plan.load).toEqual({ entryId: 'e1', url: mediaUrl('t1') });
    expect(plan.play).toBe(true);
    expect(plan.seekTo).toBeUndefined();
  });

  it('resumes mid-song when taking over', () => {
    const plan = planReconcile(
      state({ now_playing: entry(), playing: true, output_client: ME, position: 90 }),
      idle,
      ME,
    );
    expect(plan.seekTo).toBe(90);
  });

  it('waits for a file that is still downloading', () => {
    const plan = planReconcile(
      state({ now_playing: entry({ ready: false }), playing: true, output_client: ME }),
      idle,
      ME,
    );
    expect(plan.load).toBeUndefined();
    expect(plan.play).toBe(false);
  });

  it('seeks only when the element drifts past the tolerance', () => {
    const base = state({ now_playing: entry(), playing: true, output_client: ME, position: 60 });
    const loaded = { loadedEntryId: 'e1', paused: false };
    expect(planReconcile(base, { ...loaded, currentTime: 59.2 }, ME).seekTo).toBeUndefined();
    expect(planReconcile(base, { ...loaded, currentTime: 10 }, ME).seekTo).toBe(60);
  });

  it('unloads when nothing is playing', () => {
    const plan = planReconcile(state(), { loadedEntryId: 'e1', currentTime: 3, paused: false }, ME);
    expect(plan.load).toBeNull();
  });
});
