/**
 * The aim drill's two overlays: the clock while it runs, the score when it ends.
 *
 * Both read a `DrillStatus` the panel mirrors out of the training range
 * (`training.ts`), and neither decides anything: the numbers are
 * `summarise`'s, the same function the native client ports.
 *
 * Absolute inside the pane, like `PostMatchDebrief`, and for its reason: a card
 * that belongs to one pane must not cover the app.
 */
import { useEffect, useState } from 'react';

import { RollingCounter } from '../../../viz/RollingCounter';
import type { DrillStatus } from '../training';
import type { DrillStats } from '../training-drill';

const MONO = 'var(--font-mono, ui-monospace, monospace)';
const ACCENT = 'var(--accent)';
const DIM = 'var(--text-dim)';

/** DESIGN.md's Display step: the drill's name, and the figures it exists to show. */
const DISPLAY = '19px';
/** DESIGN.md's Label and Telemetry steps share a size. */
const SMALL = '10.5px';

const heading: React.CSSProperties = {
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.14em',
};

const label: React.CSSProperties = {
  fontWeight: 700,
  fontSize: SMALL,
  textTransform: 'uppercase',
  letterSpacing: '0.1em',
  color: DIM,
};

/** The countdown, then the seconds left, top centre. */
export function DrillClock({ status }: { status: DrillStatus }) {
  const counting = status.phase === 'countdown';
  return (
    <div
      style={{
        position: 'absolute',
        top: '0.75rem',
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 40,
        pointerEvents: 'none',
        padding: '0.35rem 0.9rem',
        background: 'rgba(13, 17, 23, 0.72)',
        borderTop: `2px solid ${ACCENT}`,
        clipPath: 'polygon(8px 0, calc(100% - 8px) 0, 100% 100%, 0 100%)',
        textAlign: 'center',
        color: 'var(--text)',
      }}
    >
      <div style={label}>{status.name}</div>
      <div style={{ fontFamily: MONO, fontSize: DISPLAY, color: 'var(--text-strong)' }}>
        {counting ? Math.ceil(status.remaining) : `${Math.ceil(status.remaining)}s`}
      </div>
    </div>
  );
}

interface Tile {
  label: string;
  value: number | null;
  format: (n: number) => string;
}

function tiles(stats: DrillStats): Tile[] {
  const pct = (n: number) => `${Math.round(n * 100)}%`;
  const ms = (n: number) => `${Math.round(n * 1000)} ms`;
  return [
    { label: 'Kills / min', value: stats.killsPerMinute, format: (n) => n.toFixed(1) },
    { label: 'Accuracy', value: stats.accuracy, format: pct },
    { label: 'Headshots', value: stats.headshotShare, format: pct },
    { label: 'Reaction', value: stats.medianReaction, format: ms },
    { label: 'Time to kill', value: stats.medianTtk, format: ms },
    { label: 'Kills', value: stats.kills, format: (n) => String(Math.round(n)) },
  ];
}

/** Stagger per tile, capped so six tiles never take more than ~a third of a second. */
const STAGGER_MS = 55;

export interface DrillCardProps {
  status: DrillStatus;
  /** Whether this run beat the stored best for the drill. */
  best: boolean;
  onAgain: () => void;
  onFree: () => void;
  onMenu: () => void;
}

export function DrillCard({ status, best, onAgain, onFree, onMenu }: DrillCardProps) {
  const stats = status.stats;
  // Rolled up from zero on mount; `RollingCounter` snaps to the value on a
  // timeout, so a backgrounded tab still shows the truth.
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setShown(true), 0);
    return () => clearTimeout(id);
  }, []);
  if (!stats) return null;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 200,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1rem',
        background:
          'linear-gradient(rgba(13,17,23,0.86), rgba(13,17,23,0.86)),' +
          ' repeating-linear-gradient(0deg, rgba(255,255,255,0.025) 0 1px, transparent 1px 24px),' +
          ' repeating-linear-gradient(90deg, rgba(255,255,255,0.025) 0 1px, transparent 1px 24px)',
      }}
    >
      <style>{`@keyframes hassaultDrillTileIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}`}</style>
      <div
        style={{
          width: '100%',
          maxWidth: 520,
          background: 'var(--bg-raised)',
          borderTop: `2px solid ${ACCENT}`,
          clipPath: 'polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 0 100%)',
          padding: '1.1rem 1.25rem 1rem',
          color: 'var(--text)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <div style={{ ...heading, fontSize: DISPLAY, color: 'var(--text-strong)' }}>
            {status.name}
          </div>
          <div style={{ fontFamily: MONO, fontSize: SMALL, color: DIM, display: 'flex' }}>
            <span style={{ paddingRight: '0.5rem' }}>
              {stats.hitShots}/{stats.shots} shots
            </span>
            <span
              style={{
                paddingLeft: '0.5rem',
                borderLeft: '1px solid var(--border-dim)',
              }}
            >
              {stats.hits} hits
            </span>
          </div>
        </div>
        {best && <div style={{ ...label, color: ACCENT, marginTop: '0.25rem' }}>Personal best</div>}

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 1,
            marginTop: '0.9rem',
            background: 'var(--border-dim)',
            border: '1px solid var(--border-dim)',
          }}
        >
          {tiles(stats).map((tile, i) => (
            <div
              key={tile.label}
              style={{
                background: 'var(--bg-raised)',
                padding: '0.6rem 0.7rem',
                animation: `hassaultDrillTileIn 260ms ease-out ${Math.min(i, 5) * STAGGER_MS}ms both`,
              }}
            >
              <div style={label}>{tile.label}</div>
              <div
                style={{
                  fontFamily: MONO,
                  fontSize: DISPLAY,
                  color: 'var(--text-strong)',
                  marginTop: '0.15rem',
                }}
              >
                {tile.value === null ? (
                  <span style={{ color: DIM }}>n/a</span>
                ) : (
                  <RollingCounter
                    value={shown ? tile.value : 0}
                    format={tile.format}
                    className=""
                  />
                )}
              </div>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
          <button onClick={onAgain}>Run again</button>
          <button onClick={onFree}>Free practice</button>
          <span style={{ flex: 1 }} />
          <button onClick={onMenu}>Menu</button>
        </div>
      </div>
    </div>
  );
}
