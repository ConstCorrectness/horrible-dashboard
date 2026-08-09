/**
 * The running order: who is up, who is after that, and what has already been sung.
 *
 * Reorders are ↑/↓ buttons rather than drag-and-drop on purpose. This pane is the
 * one most likely to be driven from a phone in a dark room, where a precise drag
 * against a scrolling list is genuinely hard; a tap that moves an entry one place
 * is unambiguous on any input. The backing API (`moveInQueue`) takes an index, so
 * a drag implementation can be layered on later without a server change.
 */
import { useEffect, useSyncExternalStore } from 'react';

import { useAgentContext } from '../../../agent-context';
import { ApiError } from '../../../api';
import { toastsStore } from '../../../toasts';
import { clearQueue, moveInQueue, removeFromQueue, setAutoplay } from '../api';
import {
  applyPlayerState,
  connectKaraoke,
  ensureLoaded,
  getPlayerState,
  karaokeVersion,
  subscribeKaraoke,
} from '../store';

export function KaraokeQueuePanel() {
  useSyncExternalStore(subscribeKaraoke, karaokeVersion);
  const state = getPlayerState();

  useEffect(() => {
    connectKaraoke();
    void ensureLoaded();
  }, []);

  useAgentContext(() => ({
    nowPlaying: state.now_playing
      ? { title: state.now_playing.title, singer: state.now_playing.singer }
      : null,
    // entry_id included so the agent can act on what it reads here — it's what
    // karaoke.unqueue takes, and a title wouldn't identify a song queued twice.
    queue: state.queue.map((e) => ({
      entry_id: e.entry_id,
      title: e.title,
      singer: e.singer,
    })),
    autoplay: state.autoplay,
  }));

  async function act<T>(promise: Promise<T>, label: string): Promise<void> {
    try {
      applyPlayerState((await promise) as never);
    } catch (e) {
      toastsStore.add('error', label, e instanceof ApiError ? e.message : String(e));
    }
  }

  const { now_playing: nowPlaying, queue, history } = state;

  return (
    <div className="kk-pane">
      <div className="kk-pane__bar">
        <label className="kk-transport__group">
          <input
            type="checkbox"
            checked={state.autoplay}
            onChange={(e) => void act(setAutoplay(e.target.checked), 'Autoplay')}
          />
          Autoplay
        </label>
        <span className="kk-transport__spacer" />
        <button
          type="button"
          onClick={() => void act(clearQueue(), 'Clear')}
          disabled={queue.length === 0}
        >
          Clear
        </button>
      </div>

      <div className="kk-pane__list">
        {nowPlaying ? (
          <div className="kk-row kk-row--playing">
            <span className="kk-row__index">▶</span>
            <div className="kk-row__main">
              <div className="kk-row__title">{nowPlaying.title}</div>
              <div className="kk-row__sub">
                {nowPlaying.artist}
                {nowPlaying.singer ? (
                  <span className="kk-singer"> · 🎤 {nowPlaying.singer}</span>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}

        {queue.length === 0 && !nowPlaying ? (
          <div className="kk-pane__empty">
            The queue is empty. Add songs from the Search pane, or ask the agent to queue one.
          </div>
        ) : null}

        {queue.map((entry, index) => (
          <div className="kk-row" key={entry.entry_id}>
            <span className="kk-row__index">{index + 1}</span>
            <div className="kk-row__main">
              <div className="kk-row__title" title={entry.title}>
                {entry.title}
              </div>
              <div className="kk-row__sub">
                {entry.artist}
                {entry.singer ? <span className="kk-singer"> · 🎤 {entry.singer}</span> : null}
              </div>
            </div>
            <div className="kk-row__actions">
              <button
                type="button"
                onClick={() => void act(moveInQueue(entry.entry_id, index - 1), 'Move')}
                disabled={index === 0}
                title="Move up"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={() => void act(moveInQueue(entry.entry_id, index + 1), 'Move')}
                disabled={index === queue.length - 1}
                title="Move down"
              >
                ↓
              </button>
              <button
                type="button"
                onClick={() => void act(removeFromQueue(entry.entry_id), 'Remove')}
                title="Remove"
              >
                ✕
              </button>
            </div>
          </div>
        ))}

        {history.length > 0 ? (
          <>
            <div className="kk-pane__bar" style={{ borderTop: '1px solid var(--border)' }}>
              <span className="kk-row__sub">Already sung</span>
            </div>
            {history.map((entry) => (
              <div className="kk-row" key={`${entry.entry_id}-done`}>
                <span className="kk-row__index">·</span>
                <div className="kk-row__main">
                  <div className="kk-row__title kk-row__sub" title={entry.title}>
                    {entry.title}
                    {entry.singer ? ` — ${entry.singer}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </>
        ) : null}
      </div>
    </div>
  );
}
