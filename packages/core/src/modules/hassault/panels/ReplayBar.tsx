/**
 * The demo player's controls: play, the scrubber, frag bookmarks, speed, the
 * drone camera and the way out.
 *
 * Every control works with the mouse free. Loading a demo no longer captures
 * the pointer, and a click on the world only does while the drone camera is on
 * (it steers by the mouse); Escape gives it back as it does in a match.
 *
 * Absolute inside the pane, like the drill card: one pane's bar must not cover
 * the app.
 */
import type { DemoBookmark } from '../demo';

const MONO = 'var(--font-mono, ui-monospace, monospace)';
const ACCENT = 'var(--accent)';
const DIM = 'var(--text-dim)';

const label: React.CSSProperties = {
  fontWeight: 700,
  fontSize: '10.5px',
  textTransform: 'uppercase',
  letterSpacing: '0.14em',
  color: DIM,
};

/** Speeds the bar offers; `DemoPlayer.setSpeed` clamps to 0.1-4 either way. */
const SPEEDS = [0.25, 0.5, 1, 2, 4];

function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

export interface ReplayBarProps {
  map: string;
  playing: boolean;
  timeMs: number;
  durationMs: number;
  speed: number;
  bookmarks: DemoBookmark[];
  freecam: boolean;
  onToggle: () => void;
  onSeek: (ms: number) => void;
  onSpeed: (speed: number) => void;
  onBookmark: (index: number) => void;
  onFreecam: () => void;
  onClose: () => void;
  /** How far an .mp4 export is, 0 to 1; `null` when none is running. */
  exporting: number | null;
  onExport: () => void;
  onCancelExport: () => void;
}

export function ReplayBar(props: ReplayBarProps) {
  const ended = !props.playing && props.timeMs >= props.durationMs - 1;
  // An export records the demo as it plays, so nothing may move it meanwhile.
  const busy = props.exporting !== null;
  return (
    <div
      style={{
        position: 'absolute',
        bottom: '1rem',
        left: '50%',
        transform: 'translateX(-50%)',
        width: 'min(720px, calc(100% - 32px))',
        zIndex: 100,
        background: 'rgba(13, 17, 23, 0.9)',
        borderTop: `2px solid ${ACCENT}`,
        clipPath: 'polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 0 100%)',
        padding: '0.55rem 0.8rem 0.6rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.45rem',
        color: 'var(--text)',
      }}
      // The world under the bar takes clicks to capture the pointer; the bar's
      // own clicks are the bar's.
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <span style={label}>Demo · {props.map}</span>
        <span
          style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: '0.75rem', color: DIM }}
          aria-live="off"
        >
          {clock(props.timeMs)} / {clock(props.durationMs)}
        </span>
      </div>

      {busy && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ ...label, color: ACCENT }}>Exporting MP4</span>
          <div
            role="progressbar"
            aria-label="Export progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round((props.exporting ?? 0) * 100)}
            style={{ flex: 1, height: 2, background: 'var(--border, rgba(255,255,255,0.12))' }}
          >
            <div
              style={{
                width: '100%',
                height: '100%',
                background: ACCENT,
                // Scaled rather than resized, so the fill never re-lays out the bar.
                transform: `scaleX(${Math.min(1, props.exporting ?? 0)})`,
                transformOrigin: 'left center',
                transition: 'transform 0.1s linear',
              }}
            />
          </div>
          <span style={{ fontFamily: MONO, fontSize: '0.75rem', color: DIM, minWidth: '3.5em' }}>
            {Math.floor((props.exporting ?? 0) * 100)}%
          </span>
          <button onClick={props.onCancelExport}>Cancel</button>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <button onClick={props.onToggle} disabled={busy} style={{ minWidth: 76 }}>
          {props.playing ? 'Pause' : ended ? 'Replay' : 'Play'}
        </button>
        <input
          type="range"
          aria-label="Demo position"
          min={0}
          max={Math.max(1, Math.round(props.durationMs))}
          step={10}
          value={Math.round(props.timeMs)}
          onChange={(e) => props.onSeek(Number(e.target.value))}
          disabled={busy}
          style={{ flex: 1, accentColor: ACCENT }}
        />
        <select
          value={String(props.speed)}
          onChange={(e) => props.onSpeed(Number(e.target.value))}
          aria-label="Playback speed"
          disabled={busy}
          style={{ width: 76, padding: '0 0.4rem', fontFamily: MONO }}
        >
          {SPEEDS.map((sp) => (
            <option key={sp} value={String(sp)}>
              {sp}x
            </option>
          ))}
        </select>
        <button
          onClick={props.onFreecam}
          aria-pressed={props.freecam}
          title="Drone camera (F3 or C). Click the world to steer it with the mouse; WASD, Space and crouch to fly; Escape frees the mouse."
          style={props.freecam ? { color: ACCENT, borderColor: ACCENT } : undefined}
        >
          {props.freecam ? 'Drone on' : 'Drone'}
        </button>
        <button
          onClick={props.onExport}
          disabled={busy}
          title="Record the demo to an .mp4 from the start, at this speed, as it plays. Keep the pane visible until it is done."
        >
          Export MP4
        </button>
        <button onClick={props.onClose}>Close</button>
      </div>

      {props.bookmarks.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={label}>Frags</span>
          <select
            value=""
            onChange={(e) => {
              if (e.target.value !== '') props.onBookmark(Number(e.target.value));
            }}
            aria-label="Jump to a frag"
            disabled={busy}
            style={{ flex: 1, padding: '0 0.4rem', fontFamily: MONO }}
          >
            <option value="">
              {props.bookmarks.length} frag{props.bookmarks.length === 1 ? '' : 's'}: jump to one
            </option>
            {props.bookmarks.map((bm, i) => (
              <option key={i} value={String(i)}>
                {clock(bm.timestamp)} {bm.label}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}

/** Why a demo would not load. Over the pause menu, which is where it was opened. */
export function ReplayNotice({
  title = 'Demo not loaded',
  message,
  onDismiss,
}: {
  title?: string;
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div
      role="alert"
      style={{
        position: 'absolute',
        top: '1rem',
        left: '50%',
        transform: 'translateX(-50%)',
        width: 'min(520px, calc(100% - 32px))',
        zIndex: 70,
        background: 'var(--bg-raised)',
        borderTop: '2px solid var(--danger, #f85149)',
        padding: '0.55rem 0.8rem',
        display: 'flex',
        alignItems: 'center',
        gap: '0.6rem',
        color: 'var(--text)',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={label}>{title}</div>
        <div style={{ fontSize: '0.8rem', marginTop: 2 }}>{message}</div>
      </div>
      <button onClick={onDismiss}>Dismiss</button>
    </div>
  );
}
