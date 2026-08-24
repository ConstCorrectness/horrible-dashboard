import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type React from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import type { MirrorFrame, MirrorNode, MirrorPane } from '../mirror';
import { mirrorPanes } from '../mirror';
import { ShareSubscriber } from '../rtc';
import { parseSignal } from '../signal';
import { GRANT_LADDER, type GrantLevel } from '../api';
import { getShareSnapshot, onShareSignal, sendShareAction, subscribeShare } from '../ws';
import './mirror.css';

/**
 * The guest's window onto the host's workspace.
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
  onSelect,
}: {
  pane: MirrorPane;
  focused: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="share-tile"
      data-tone={tone(pane)}
      data-focused={focused ? 'true' : undefined}
      data-selected={selected ? 'true' : undefined}
      data-minimized={pane.minimized ? 'true' : undefined}
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

function Node({
  node,
  focusedId,
  selectedId,
  onSelect,
}: {
  node: MirrorNode;
  focusedId: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (node.kind === 'area') {
    const active = node.tabs[node.activeTab];
    return (
      <div className="share-area">
        {node.tabs.length > 1 && (
          <div className="share-tabs">
            {node.tabs.map((t) => (
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
  return (
    <div className="share-split" data-orientation={node.orientation}>
      {node.children.map((child, i) => (
        <div
          key={child.id}
          className="share-split-child"
          style={{ flexGrow: node.sizes[i] ?? 1, flexBasis: 0 }}
        >
          <Node node={child} focusedId={focusedId} selectedId={selectedId} onSelect={onSelect} />
        </div>
      ))}
    </div>
  );
}

function Workspace({
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
  const docks = (['left', 'right'] as const).filter((s) => frame.docks[s].visible);

  return (
    <div className="share-workspace">
      {docks.includes('left') && (
        <Dock tools={frame.docks.left.tools} highlighted={highlighted} onSelect={onSelect} />
      )}
      <div className="share-center">
        <Node
          node={frame.center}
          focusedId={highlighted}
          selectedId={selectedId}
          onSelect={onSelect}
        />
        {frame.docks.bottom.visible && frame.docks.bottom.tools.length > 0 && (
          <Dock
            tools={frame.docks.bottom.tools}
            highlighted={highlighted}
            onSelect={onSelect}
            horizontal
          />
        )}
      </div>
      {docks.includes('right') && (
        <Dock tools={frame.docks.right.tools} highlighted={highlighted} onSelect={onSelect} />
      )}
    </div>
  );
}

function Dock({
  tools,
  highlighted,
  onSelect,
  horizontal,
}: {
  tools: MirrorPane[];
  highlighted: string | null;
  onSelect: (id: string) => void;
  horizontal?: boolean;
}) {
  if (tools.length === 0) return null;
  return (
    <div className="share-dock" data-horizontal={horizontal ? 'true' : undefined}>
      {tools.map((t) => (
        <Tile
          key={t.instanceId}
          pane={t}
          focused={t.instanceId === highlighted}
          selected={false}
          onSelect={() => onSelect(t.instanceId)}
        />
      ))}
    </div>
  );
}

/**
 * The host's screen, when they are sending one.
 *
 * `muted` is absent on purpose: this is another person talking, and muting it by
 * default would be the wrong end of the autoplay trade-off. `playsInline` keeps
 * iOS from taking the video fullscreen on play.
 */
function VideoStage({ stream }: { stream: MediaStream }) {
  const ref = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.srcObject = stream;
    // Autoplay with sound needs a gesture in most engines. Failing here is
    // normal and recoverable — the element keeps its controls, so the viewer
    // presses play — and must not throw into React.
    void el.play().catch(() => {});
    return () => {
      el.srcObject = null;
    };
  }, [stream]);

  return <video ref={ref} className="share-video" autoPlay playsInline controls />;
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
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [showMap, setShowMap] = useState(false);

  const sessions = state.joined;
  const current = sessions.find((s) => s.id === activeId) ?? sessions[0] ?? null;
  const frame = current ? (state.mirrors[current.id] ?? null) : null;

  const sessionId = current?.id ?? '';
  const hostNode = current?.host_node ?? '';

  // One subscriber per session. Rebuilt when the session changes rather than
  // reconfigured, so a stale peer connection can never outlive the session that
  // negotiated it.
  useEffect(() => {
    if (!sessionId || !hostNode) return;
    const sub = new ShareSubscriber(setStream, () => setStream(null));
    sub.expect(sessionId, hostNode);
    const off = onShareSignal((from, payload) => {
      const parsed = parseSignal(payload);
      if (parsed) void sub.accept(from, parsed);
    });
    return () => {
      off();
      sub.close();
    };
  }, [sessionId, hostNode]);

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
          <Workspace
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
            A map of their workspace, not a copy of it. Panes they have not shared show as{' '}
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
