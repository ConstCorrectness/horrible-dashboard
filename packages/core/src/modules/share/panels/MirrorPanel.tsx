import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type React from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import type { MirrorArea, MirrorFrame, MirrorPane, MirrorWindow } from '../mirror';
import { mirrorPanes } from '../mirror';
import { IconPlay, IconVolume } from '../../../glyphs';
import { GRANT_LADDER, type GrantLevel } from '../api';
import { startPlayback, watchFirstFrame, type PlaybackOutcome } from '../playback';
import { getWatchState, subscribeWatch } from '../watch';
import { getShareSnapshot, sendShareAction, subscribeShare } from '../ws';
import './mirror.css';

/**
 * The guest's window onto the host's desktop.
 *
 * It is a **map**, not a copy, and that distinction is the whole design. A pane
 * component renders local data, so seeding the host's layout into this dashboard
 * would open *this* machine's editor and *this* machine's terminal, arranged like
 * the host's — a layout copy wearing a mirror's clothes, and a genuinely
 * dangerous one to put in front of someone who has been told they are looking at
 * somebody else's screen.
 *
 * So each tile says exactly what is known: which pane is there, whether it is
 * shared, and whether the host is looking at it.
 *
 * When the host is also sending pixels, the video takes the stage and the map
 * moves behind a toggle. The two are complementary rather than redundant: the
 * video shows content the map cannot, and the map shows what the video is
 * *hiding* — a redacted pane is a tile here and simply absent from the picture.
 */

function tone(pane: MirrorPane): 'shared' | 'redacted' {
  return pane.mode === 'redacted' ? 'redacted' : 'shared';
}

function Tile({
  pane,
  focused,
  selected,
  minimized,
  onSelect,
}: {
  pane: MirrorPane;
  focused: boolean;
  selected: boolean;
  minimized: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="share-tile"
      data-tone={tone(pane)}
      data-focused={focused ? 'true' : undefined}
      data-selected={selected ? 'true' : undefined}
      data-minimized={minimized ? 'true' : undefined}
      onClick={onSelect}
      title={
        pane.mode === 'redacted'
          ? `${pane.title} — not shared. Nothing about this pane left the host.`
          : `${pane.title} — shared as ${pane.mode}`
      }
    >
      <span className="share-tile-title">{pane.title}</span>
      <span className="share-tile-mode">{pane.mode === 'redacted' ? 'not shared' : pane.mode}</span>
    </button>
  );
}

/** One window's tab strip and its showing pane. */
function Area({
  area,
  minimized,
  focusedId,
  selectedId,
  onSelect,
}: {
  area: MirrorArea;
  minimized: boolean;
  focusedId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const active = area.tabs[area.activeTab];
  return (
    <div className="share-area">
      {area.tabs.length > 1 && (
        <div className="share-tabs">
          {area.tabs.map((t) => (
            <span
              key={t.instanceId}
              className="share-tab"
              data-active={t.instanceId === active?.instanceId ? 'true' : undefined}
            >
              {t.title}
            </span>
          ))}
        </div>
      )}
      {active ? (
        <Tile
          pane={active}
          focused={active.instanceId === focusedId}
          selected={active.instanceId === selectedId}
          minimized={minimized}
          onSelect={() => onSelect(active.instanceId)}
        />
      ) : (
        <div className="share-tile" data-tone="empty">
          <span className="share-tile-mode">empty</span>
        </div>
      )}
    </div>
  );
}

/** A host's window as a fraction of their desktop, so the map keeps its proportions. */
function windowStyle(win: MirrorWindow, viewport: { w: number; h: number }): React.CSSProperties {
  const pct = (v: number, of: number) => `${Math.min(Math.max((v / of) * 100, 0), 100)}%`;
  return {
    left: pct(win.rect.x, viewport.w),
    top: pct(win.rect.y, viewport.h),
    width: pct(win.rect.w, viewport.w),
    height: pct(win.rect.h, viewport.h),
    zIndex: win.z,
  };
}

function Desktop({
  frame,
  following,
  selectedId,
  onSelect,
}: {
  frame: MirrorFrame;
  following: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  // Following means the map tracks what the host is looking at; unfollowing lets
  // the guest hold their own attention on a tile while the host moves around.
  const highlighted = following ? frame.focusedInstanceId : selectedId;
  // A host on a build from before the tiling frame was removed may send no
  // `windows`; its centre grid has no equivalent here, so the map is just empty.
  const windows = frame.windows ?? [];
  const open = windows.filter((w) => !w.minimized);
  const minimized = windows.filter((w) => w.minimized);
  // Rects are pixels against the host's surface; before the host has measured
  // one, any box keeps the proportions between windows right.
  const viewport = frame.windowViewport ?? { w: 1280, h: 800 };

  return (
    <div className="share-workspace">
      <div className="share-desktop" style={{ aspectRatio: `${viewport.w} / ${viewport.h}` }}>
        {open.map((win) => (
          <div key={win.id} className="share-window" style={windowStyle(win, viewport)}>
            <Area
              area={win.area}
              minimized={false}
              focusedId={highlighted}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          </div>
        ))}
      </div>
      {minimized.length > 0 && (
        <div className="share-minimized" aria-label="Minimized windows">
          {minimized.map((win) => (
            <Area
              key={win.id}
              area={win.area}
              minimized
              focusedId={highlighted}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The host's screen, when they are sending one.
 *
 * Tries for sound first: this is another person talking, and muting it by
 * default would be the wrong end of the autoplay trade-off. When the engine
 * refuses sound without a gesture it plays muted and says so with a labelled
 * Unmute — the pattern the public viewer page uses — instead of the old silent
 * refusal, which left a paused element that read as a black, broken stream.
 *
 * "Showing" waits for the first decoded frame, not the track: a track arrives
 * with the answer, and a connection that negotiates and never carries a picture
 * is exactly the case that has to keep saying it is waiting. `playsInline` keeps
 * iOS from taking the video fullscreen on play.
 */
function VideoStage({ stream }: { stream: MediaStream }) {
  const ref = useRef<HTMLVideoElement | null>(null);
  const [showing, setShowing] = useState(false);
  const [outcome, setOutcome] = useState<PlaybackOutcome | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let stale = false;
    setShowing(false);
    setOutcome(null);
    el.srcObject = stream;
    const stopWatching = watchFirstFrame(el, () => {
      if (!stale) setShowing(true);
    });
    void startPlayback(el, () => !stale).then((how) => {
      if (how) setOutcome(how);
    });
    // The native controls can unmute too; the prompt must not outlive that.
    const onVolume = () => {
      if (!el.muted) setOutcome((o) => (o === 'muted' ? 'sound' : o));
    };
    el.addEventListener('volumechange', onVolume);
    return () => {
      stale = true;
      stopWatching();
      el.removeEventListener('volumechange', onVolume);
      el.srcObject = null;
    };
  }, [stream]);

  const unmute = () => {
    const el = ref.current;
    if (!el) return;
    el.muted = false;
    // The click is the gesture the engine wanted; play again under it in case
    // the element paused along the way.
    void el.play().catch(() => {});
    setOutcome('sound');
  };

  const play = () => {
    const el = ref.current;
    if (!el) return;
    void el.play().then(
      () => setOutcome(el.muted ? 'muted' : 'sound'),
      () => {},
    );
  };

  return (
    <div className="share-video-stage" data-showing={showing ? 'true' : undefined}>
      <video ref={ref} className="share-video" autoPlay playsInline controls={showing} />
      {!showing && outcome !== 'blocked' && (
        <div className="share-video-wait" role="status">
          <span className="share-video-wait__title">Connecting to their screen</span>
          <span className="share-video-wait__meta">waiting for the first frame</span>
        </div>
      )}
      {outcome === 'blocked' && (
        <div className="share-video-wait">
          <span className="share-video-wait__title">Ready to play</span>
          <span className="share-video-wait__meta">your browser blocked autoplay</span>
          <button type="button" className="share-video-action" onClick={play}>
            <IconPlay aria-hidden="true" />
            <span>Play</span>
          </button>
        </div>
      )}
      {showing && outcome === 'muted' && (
        <button
          type="button"
          className="share-video-action share-video-unmute"
          title="Your browser started this muted. Click to hear them."
          onClick={unmute}
        >
          <IconVolume muted aria-hidden="true" />
          <span>Unmute</span>
        </button>
      )}
    </div>
  );
}

/** Position on the grant ladder. Unknown rungs sit at the bottom, the same
 *  reading `gate.rung` applies on the backend. */
function rungOf(grant: GrantLevel): number {
  const at = GRANT_LADDER.indexOf(grant);
  return at < 0 ? 0 : at;
}

export function MirrorPanel() {
  const state = useSyncExternalStore(subscribeShare, getShareSnapshot, getShareSnapshot);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showMap, setShowMap] = useState(false);

  const sessions = state.joined;
  const current = sessions.find((s) => s.id === activeId) ?? sessions[0] ?? null;
  const frame = current ? (state.mirrors[current.id] ?? null) : null;

  const sessionId = current?.id ?? '';
  // The connection lives in `watch.ts`, not here — a pane that owned it lost
  // every offer sent while its tab was inactive or not yet open. This only reads
  // which stream belongs to the session on screen.
  const watch = useSyncExternalStore(subscribeWatch, getWatchState, getWatchState);
  const stream = sessionId ? (watch.streams[sessionId] ?? null) : null;

  const grant = current?.grant ?? 'view';

  /**
   * Report this guest's pointer to the host, but only on the `cursor` rung.
   *
   * Throttled to ~20/s and sent as **fractions of the box**, never pixels: the
   * two ends are different windows at different sizes, and a pixel offset would
   * land somewhere else on the host's screen. Gated client-side purely to avoid
   * shouting at a host who would refuse every frame — the host's registry is the
   * real gate and refuses these regardless of what this check does.
   */
  const stageRef = useRef<HTMLDivElement | null>(null);
  const lastSent = useRef(0);
  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!sessionId || rungOf(grant) < rungOf('cursor')) return;
      const now = performance.now();
      if (now - lastSent.current < 50) return;
      lastSent.current = now;
      const box = stageRef.current?.getBoundingClientRect();
      if (!box || box.width === 0 || box.height === 0) return;
      sendShareAction(sessionId, 'cursor.move', {
        x: (e.clientX - box.left) / box.width,
        y: (e.clientY - box.top) / box.height,
        instanceId: selectedId ?? '',
      });
    },
    [sessionId, grant, selectedId],
  );

  const counts = useMemo(() => {
    if (!frame) return null;
    const panes = mirrorPanes(frame);
    return { total: panes.length, hidden: frame.redactedCount };
  }, [frame]);

  if (sessions.length === 0) {
    return (
      <div className="share-mirror">
        <EmptyState title="Not in a session">
          When you accept an invitation to somebody&rsquo;s workspace, their layout appears here.
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="share-mirror">
      <PaneHeader
        title={current?.title ?? 'Session mirror'}
        meta={
          counts
            ? [current?.host_name ?? '', `${counts.total} panes`, `${counts.hidden} hidden`]
            : [current?.host_name ?? '']
        }
        actions={
          <>
            {sessions.length > 1 && (
              <select
                value={current?.id ?? ''}
                aria-label="Session"
                onChange={(e) => setActiveId(e.target.value)}
              >
                {sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            )}
            {stream && (
              <Button
                size="sm"
                aria-pressed={showMap}
                title="Switch between their screen and the structural map."
                onClick={() => setShowMap((v) => !v)}
              >
                {showMap ? 'Screen' : 'Map'}
              </Button>
            )}
            <Button
              intent={following ? 'primary' : 'default'}
              size="sm"
              aria-pressed={following}
              title="Track whichever pane the host is looking at."
              onClick={() => setFollowing((v) => !v)}
            >
              {following ? 'Following' : 'Follow'}
            </Button>
          </>
        }
      />

      {/* The stage is what the pointer is reported against, so the ref and the
          handler sit on the element the guest is actually looking at. */}
      <div ref={stageRef} onPointerMove={onPointerMove} className="share-mirror__stage">
        {stream && !showMap && <VideoStage stream={stream} />}

        {!frame && !stream && (
          <EmptyState title="Waiting for the host">
            You are in the session, but they have not published their layout yet.
          </EmptyState>
        )}

        {frame && (!stream || showMap) && (
          <>
            <Desktop
              frame={frame}
              following={following}
              selectedId={selectedId}
              onSelect={(id) => {
                setSelectedId(id);
                // Picking a tile is the gesture that means "stop tracking them" —
                // making the guest also find a toggle would be one step too many.
                setFollowing(false);
              }}
            />
            <p className="share-mirror-note">
              A map of their desktop, not a copy of it. Panes they have not shared show as{' '}
              <strong>not shared</strong> &mdash; nothing about those left their machine, not even a
              title.
              {counts && counts.hidden > 0 && (
                <>
                  {' '}
                  <Chip kind="warn">{counts.hidden} hidden</Chip>
                </>
              )}
            </p>
          </>
        )}
      </div>
    </div>
  );
}
