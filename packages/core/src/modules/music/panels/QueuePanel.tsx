/**
 * Up next, and what already played.
 *
 * Entries are addressed by `entry_id` (the same song queued twice is two seats),
 * which is also what the agent context exposes, so "drop the third one" works.
 * Reordering is one-place moves rather than drag-and-drop — exact on any input,
 * and the API takes an index, so a drag can be layered on without a server change.
 */
import { useEffect, useState } from 'react';

import { useAgentContext } from '../../../agent-context';
import { DataList, DataRow } from '../../../DataList';
import { IconArrowUp, IconClose, IconRadio } from '../../../glyphs';
import { Button, EmptyState, PaneHeader } from '../../../Primitives';
import { clearQueue, createPlaylist, djMore, moveInQueue, removeFromQueue } from '../api';
import { connectMusic, ensureLoaded, refreshPlaylists } from '../store';
import { act, formatTime, reasonLabel, useMusicState } from '../ui';

export function MusicQueuePanel() {
  const state = useMusicState();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    connectMusic();
    void ensureLoaded();
  }, []);

  useAgentContext(() => ({
    nowPlaying: state.now_playing
      ? {
          entry_id: state.now_playing.entry_id,
          title: state.now_playing.title,
          artist: state.now_playing.artist,
        }
      : null,
    queue: state.queue.map((e) => ({
      entry_id: e.entry_id,
      track_id: e.track_id,
      title: e.title,
      artist: e.artist,
    })),
  }));

  const { queue, history } = state;
  const total = queue.reduce((sum, e) => sum + (e.duration ?? 0), 0);

  const saveAsPlaylist = async () => {
    const name = window.prompt('Name the playlist', 'Queue');
    if (!name) return;
    await act(createPlaylist(name, [], true), 'Save queue');
    void refreshPlaylists().catch(() => {});
  };

  const more = async () => {
    setBusy(true);
    await act(djMore(10), 'More like this');
    setBusy(false);
  };

  return (
    <div className="mu-pane">
      <PaneHeader
        title="Up next"
        meta={[`${queue.length}`, formatTime(total)]}
        actions={
          <>
            <Button
              size="sm"
              intent="ghost"
              icon={<IconRadio />}
              title="More like this: the DJ queues songs similar to what's been playing"
              disabled={busy}
              onClick={() => void more()}
            >
              More
            </Button>
            <Button
              size="sm"
              intent="ghost"
              disabled={queue.length === 0}
              onClick={() => void saveAsPlaylist()}
            >
              Save
            </Button>
            <Button
              size="sm"
              intent="ghost"
              disabled={queue.length === 0}
              onClick={() => void act(clearQueue(), 'Clear queue')}
            >
              Clear
            </Button>
          </>
        }
      />
      <div className="mu-pane__scroll">
        {queue.length === 0 ? (
          <EmptyState title="Queue is empty">
            Add songs from the library, or press “More like this” to have the DJ fill it from what
            you've been playing.
          </EmptyState>
        ) : (
          <DataList label="Up next">
            {queue.map((entry, index) => (
              <DataRow
                key={entry.entry_id}
                index={index}
                kind={entry.ready ? 'idle' : 'info'}
                hideMark
                title={entry.title}
                meta={[
                  entry.artist || '—',
                  formatTime(entry.duration),
                  ...(entry.ready ? [] : ['downloading']),
                ]}
                badge={reasonLabel(entry.reason) || undefined}
                actions={
                  <>
                    <Button
                      size="sm"
                      intent="ghost"
                      aria-label="Move up"
                      title="Move up"
                      disabled={index === 0}
                      icon={<IconArrowUp />}
                      onClick={() => void act(moveInQueue(entry.entry_id, index - 1), 'Move')}
                    />
                    <Button
                      size="sm"
                      intent="ghost"
                      aria-label="Remove"
                      title="Remove from the queue"
                      icon={<IconClose />}
                      onClick={() => void act(removeFromQueue(entry.entry_id), 'Remove')}
                    />
                  </>
                }
              />
            ))}
          </DataList>
        )}
        {history.length > 0 && (
          <>
            <div className="mu-section-head">
              <span className="mu-section-title">Played</span>
              <span className="mu-section-meta">{history.length}</span>
            </div>
            <DataList label="Played">
              {history.slice(0, 30).map((entry, index) => (
                <DataRow
                  key={entry.entry_id}
                  index={index}
                  hideMark
                  title={entry.title}
                  meta={[entry.artist || '—', formatTime(entry.duration)]}
                />
              ))}
            </DataList>
          </>
        )}
      </div>
    </div>
  );
}
