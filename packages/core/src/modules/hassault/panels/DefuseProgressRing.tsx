/**
 * Radial SVG Progress Ring for competitive bomb defusal and planting.
 *
 * Positioned under the crosshair with radial progress, wire-snipping milestones
 * (25%, 50%, 75%), remaining time readout, and kit/no-kit badging.
 */

import { useEffect, useRef } from 'react';
import type { ModeSelf } from '../net';

export interface DefuseProgressRingProps {
  mine: ModeSelf | null | undefined;
  onWireCut?: () => void;
}

export function DefuseProgressRing({ mine, onWireCut }: DefuseProgressRingProps) {
  const lastProgressRef = useRef<number>(0);
  const cutFiredRef = useRef<{ 25: boolean; 50: boolean; 75: boolean }>({
    25: false,
    50: false,
    75: false,
  });

  const progress = Math.max(0, Math.min(1, mine?.progress ?? 0));
  const kind = mine?.progressKind;
  const isDefusing = kind === 'defuse';
  const isPlanting = kind === 'plant';

  // Wire snip sound triggers at 25%, 50%, 75% milestones during defuse
  useEffect(() => {
    if (!isDefusing || progress <= 0) {
      cutFiredRef.current = { 25: false, 50: false, 75: false };
      lastProgressRef.current = 0;
      return;
    }

    if (progress < lastProgressRef.current) {
      // Progress reset
      cutFiredRef.current = { 25: false, 50: false, 75: false };
    }
    lastProgressRef.current = progress;

    if (progress >= 0.25 && !cutFiredRef.current[25]) {
      cutFiredRef.current[25] = true;
      onWireCut?.();
    }
    if (progress >= 0.5 && !cutFiredRef.current[50]) {
      cutFiredRef.current[50] = true;
      onWireCut?.();
    }
    if (progress >= 0.75 && !cutFiredRef.current[75]) {
      cutFiredRef.current[75] = true;
      onWireCut?.();
    }
  }, [isDefusing, progress, onWireCut]);

  if (!kind || progress <= 0) return null;

  const hasKit = Boolean(mine?.hasKit);
  const totalTime = isDefusing ? (hasKit ? 5.0 : 10.0) : 3.0;
  const timeLeft = Math.max(0, totalTime * (1 - progress));

  const strokeColor = isDefusing
    ? hasKit
      ? '#38bdf8' // Cyan for Defuse Kit
      : '#f59e0b' // Amber for No Kit
    : '#ef4444'; // Red/Crimson for C4 Plant

  const glowColor = isDefusing
    ? hasKit
      ? 'rgba(56, 189, 248, 0.45)'
      : 'rgba(245, 158, 11, 0.45)'
    : 'rgba(239, 68, 68, 0.45)';

  const radius = 38;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - progress);

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '60%',
        transform: 'translate(-50%, -50%)',
        pointerEvents: 'none',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'monospace',
        userSelect: 'none',
      }}
    >
      <div style={{ position: 'relative', width: 96, height: 96 }}>
        <svg
          width="96"
          height="96"
          viewBox="0 0 96 96"
          style={{ transform: 'rotate(-90deg)', filter: `drop-shadow(0 0 6px ${glowColor})` }}
        >
          {/* Background Track */}
          <circle
            cx="48"
            cy="48"
            r={radius}
            fill="none"
            stroke="rgba(15, 23, 42, 0.85)"
            strokeWidth="6"
          />

          {/* Quarter milestone notch lines */}
          {isDefusing && (
            <>
              {/* 25% tick (top-right, 0 deg after rotate) */}
              <circle
                cx="86"
                cy="48"
                r="2"
                fill={progress >= 0.25 ? strokeColor : 'rgba(255,255,255,0.3)'}
              />
              {/* 50% tick (bottom, 90 deg) */}
              <circle
                cx="48"
                cy="86"
                r="2"
                fill={progress >= 0.5 ? strokeColor : 'rgba(255,255,255,0.3)'}
              />
              {/* 75% tick (left, 180 deg) */}
              <circle
                cx="10"
                cy="48"
                r="2"
                fill={progress >= 0.75 ? strokeColor : 'rgba(255,255,255,0.3)'}
              />
            </>
          )}

          {/* Progress Arc */}
          <circle
            cx="48"
            cy="48"
            r={radius}
            fill="none"
            stroke={strokeColor}
            strokeWidth="6"
            strokeDasharray={circumference}
            strokeDashoffset={dashOffset}
            strokeLinecap="round"
            style={{ transition: 'stroke-dashoffset 0.05s linear' }}
          />
        </svg>

        {/* Center Readout */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            textAlign: 'center',
          }}
        >
          <div
            style={{
              fontSize: '1.05rem',
              fontWeight: 'bold',
              color: '#ffffff',
              textShadow: '0 1px 4px rgba(0,0,0,0.9)',
              letterSpacing: '0.04em',
            }}
          >
            {timeLeft.toFixed(1)}s
          </div>
          <div
            style={{
              fontSize: '0.62rem',
              letterSpacing: '0.12em',
              color: strokeColor,
              fontWeight: 600,
              textTransform: 'uppercase',
            }}
          >
            {Math.round(progress * 100)}%
          </div>
        </div>
      </div>

      {/* Bottom Status Badge */}
      <div
        style={{
          marginTop: '0.5rem',
          padding: '3px 10px',
          background: 'rgba(15, 23, 42, 0.9)',
          border: `1px solid ${strokeColor}`,
          borderRadius: 4,
          fontSize: '0.72rem',
          fontWeight: 'bold',
          letterSpacing: '0.14em',
          color: strokeColor,
          boxShadow: `0 0 8px ${glowColor}`,
          display: 'flex',
          alignItems: 'center',
          gap: '0.4rem',
        }}
      >
        {isDefusing && (
          <>
            <span>{hasKit ? '✂️' : '⏱️'}</span>
            <span>{hasKit ? 'DEFUSING (KIT: 5.0s)' : 'DEFUSING (NO KIT: 10.0s)'}</span>
          </>
        )}
        {isPlanting && (
          <>
            <span>💣</span>
            <span>PLANTING C4 (3.0s)</span>
          </>
        )}
      </div>
    </div>
  );
}
