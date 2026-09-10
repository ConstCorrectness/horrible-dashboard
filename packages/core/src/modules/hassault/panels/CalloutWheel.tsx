/**
 * Tactical Ping & Callout Radial Wheel.
 *
 * Appears when holding Z (>200ms). Mouse displacement selects one of 4 tactical callouts:
 * - Top (Red): Enemy Spotted
 * - Right (Cyan): Watching Angle
 * - Bottom (Amber): Danger / Fall Back
 * - Left (Blue): Need Utility
 *
 * Styled with esports HUD aesthetics, SVG icons, glowing borders, and pure CSS variables.
 */

import React from 'react';
import type { PingKind } from '../net';

export interface CalloutWheelProps {
  active: boolean;
  selected: PingKind;
}

export interface CalloutOption {
  kind: PingKind;
  label: string;
  sub: string;
  color: string;
  bgRgba: string;
  borderRgba: string;
  glowRgba: string;
  icon: React.ReactNode;
}

export const CALLOUT_OPTIONS: Record<PingKind, CalloutOption> = {
  spotted: {
    kind: 'spotted',
    label: 'ENEMY SPOTTED',
    sub: 'Hostile contact in sight',
    color: 'rgb(239, 68, 68)',
    bgRgba: 'rgba(239, 68, 68, 0.22)',
    borderRgba: 'rgba(239, 68, 68, 0.85)',
    glowRgba: 'rgba(239, 68, 68, 0.45)',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="2" x2="12" y2="6" />
        <line x1="12" y1="18" x2="12" y2="22" />
        <line x1="2" y1="12" x2="6" y2="12" />
        <line x1="18" y1="12" x2="22" y2="12" />
        <circle cx="12" cy="12" r="3" fill="currentColor" />
      </svg>
    ),
  },
  watch: {
    kind: 'watch',
    label: 'WATCHING HERE',
    sub: 'Holding angle / sightline',
    color: 'rgb(6, 182, 212)',
    bgRgba: 'rgba(6, 182, 212, 0.22)',
    borderRgba: 'rgba(6, 182, 212, 0.85)',
    glowRgba: 'rgba(6, 182, 212, 0.45)',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    ),
  },
  danger: {
    kind: 'danger',
    label: 'DANGER / FALL BACK',
    sub: 'Heavy fire / retreat',
    color: 'rgb(245, 158, 11)',
    bgRgba: 'rgba(245, 158, 11, 0.22)',
    borderRgba: 'rgba(245, 158, 11, 0.85)',
    glowRgba: 'rgba(245, 158, 11, 0.45)',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
        <line x1="12" y1="9" x2="12" y2="13" />
        <line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
    ),
  },
  utility: {
    kind: 'utility',
    label: 'NEED UTILITY',
    sub: 'Requesting smoke or flash',
    color: 'rgb(59, 130, 246)',
    bgRgba: 'rgba(59, 130, 246, 0.22)',
    borderRgba: 'rgba(59, 130, 246, 0.85)',
    glowRgba: 'rgba(59, 130, 246, 0.45)',
    icon: (
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2v4" />
        <path d="M12 6a5 5 0 0 0-5 5v5a5 5 0 0 0 10 0v-5a5 5 0 0 0-5-5Z" />
        <line x1="7" y1="11" x2="17" y2="11" />
        <line x1="7" y1="15" x2="17" y2="15" />
      </svg>
    ),
  },
};

/** Determine PingKind from mouse displacement relative to center. */
export function getCalloutFromDelta(dx: number, dy: number, deadzone = 15): PingKind {
  const dist = Math.hypot(dx, dy);
  if (dist < deadzone) return 'spotted'; // Default center

  const angle = Math.atan2(dy, dx); // [-PI, PI], 0 is Right, PI/2 is Down, -PI/2 is Up, PI/-PI is Left
  if (angle >= -0.75 * Math.PI && angle < -0.25 * Math.PI) {
    return 'spotted'; // Top
  }
  if (angle >= -0.25 * Math.PI && angle < 0.25 * Math.PI) {
    return 'watch'; // Right
  }
  if (angle >= 0.25 * Math.PI && angle < 0.75 * Math.PI) {
    return 'danger'; // Down
  }
  return 'utility'; // Left
}

export function CalloutWheel({ active, selected }: CalloutWheelProps) {
  if (!active) return null;

  const current = CALLOUT_OPTIONS[selected];

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
        zIndex: 999,
      }}
    >
      {/* Background backdrop blur */}
      <div
        style={{
          position: 'absolute',
          width: 320,
          height: 320,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(15, 23, 42, 0.85) 0%, rgba(15, 23, 42, 0.4) 70%, transparent 100%)',
          backdropFilter: 'blur(4px)',
          border: '1px solid rgba(255, 255, 255, 0.12)',
          boxShadow: `0 0 40px ${current.glowRgba}`,
          transition: 'box-shadow 0.15s ease',
        }}
      />

      {/* Central callout display card */}
      <div
        style={{
          position: 'relative',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
          color: current.color,
          transform: 'scale(1.05)',
          transition: 'all 0.15s ease',
          zIndex: 2,
        }}
      >
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: '50%',
            background: current.bgRgba,
            border: `2px solid ${current.borderRgba}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: `0 0 20px ${current.glowRgba}`,
            marginBottom: 8,
          }}
        >
          {current.icon}
        </div>
        <div
          style={{
            fontSize: 14,
            fontWeight: 800,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            textShadow: `0 0 10px ${current.color}`,
          }}
        >
          {current.label}
        </div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 500,
            color: 'rgba(255, 255, 255, 0.75)',
            marginTop: 2,
            maxWidth: 160,
          }}
        >
          {current.sub}
        </div>
      </div>

      {/* Quadrant buttons: Top, Right, Bottom, Left */}
      {(['spotted', 'watch', 'danger', 'utility'] as PingKind[]).map((kind) => {
        const opt = CALLOUT_OPTIONS[kind];
        const isSel = selected === kind;

        // Position offsets
        let top: number | string = 'auto';
        let left: number | string = 'auto';
        let right: number | string = 'auto';
        let bottom: number | string = 'auto';
        let transform = '';

        if (kind === 'spotted') {
          top = 'calc(50% - 132px)';
          left = '50%';
          transform = 'translateX(-50%)';
        } else if (kind === 'watch') {
          right = 'calc(50% - 132px)';
          top = '50%';
          transform = 'translateY(-50%)';
        } else if (kind === 'danger') {
          bottom = 'calc(50% - 132px)';
          left = '50%';
          transform = 'translateX(-50%)';
        } else {
          left = 'calc(50% - 132px)';
          top = '50%';
          transform = 'translateY(-50%)';
        }

        return (
          <div
            key={kind}
            style={{
              position: 'absolute',
              top,
              left,
              right,
              bottom,
              transform,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '6px 10px',
              borderRadius: 8,
              background: isSel ? opt.bgRgba : 'rgba(15, 23, 42, 0.7)',
              border: isSel ? `2px solid ${opt.borderRgba}` : '1px solid rgba(255, 255, 255, 0.15)',
              boxShadow: isSel ? `0 0 16px ${opt.glowRgba}` : 'none',
              transition: 'all 0.12s ease',
              color: isSel ? opt.color : 'rgba(255, 255, 255, 0.65)',
              opacity: isSel ? 1 : 0.6,
              scale: isSel ? '1.1' : '0.95',
              zIndex: 3,
            }}
          >
            <div style={{ transform: 'scale(0.85)' }}>{opt.icon}</div>
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: '0.08em',
                marginTop: 2,
                whiteSpace: 'nowrap',
              }}
            >
              {opt.label.split(' ')[0]}
            </span>
          </div>
        );
      })}

      {/* Guide prompt */}
      <div
        style={{
          position: 'absolute',
          bottom: 'calc(50% - 180px)',
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.06em',
          color: 'rgba(255, 255, 255, 0.5)',
          background: 'rgba(0, 0, 0, 0.4)',
          padding: '2px 8px',
          borderRadius: 4,
        }}
      >
        RELEASE <kbd style={{ color: '#fff', fontWeight: 800 }}>Z</kbd> TO CALLOUT
      </div>
    </div>
  );
}
