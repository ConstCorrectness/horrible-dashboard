/**
 * Tactical Pings 3D World & Screen-Space Edge-Clamped Overlay.
 *
 * Renders tactical callouts placed by teammates in 3D space:
 * - On-screen: In-world beacon pole with glowing ground ring, tactical icon, owner name, and distance (e.g. "18m").
 * - Off-screen / Behind: Edge-clamped directional pointer with chevron indicator and distance tag.
 * - Fades smoothly as ping TTL expires.
 */

import React, { useMemo } from 'react';
import * as THREE from 'three';
import type { PingRow } from '../net';
import { CALLOUT_OPTIONS } from './CalloutWheel';

export interface TacticalPingsOverlayProps {
  pings: readonly PingRow[] | undefined;
  camera: THREE.Camera | null;
  playerPos: { x: number; y: number; z: number } | null;
  containerRef?: React.RefObject<HTMLDivElement | null>;
  viewportWidth?: number;
  viewportHeight?: number;
}

interface ProjectedPing {
  row: PingRow;
  screenX: number;
  screenY: number;
  distance: number;
  inView: boolean;
  angle: number; // Angle in radians pointing from viewport center or edge
  opacity: number;
}

export function TacticalPingsOverlay({
  pings,
  camera,
  playerPos,
  containerRef,
  viewportWidth,
  viewportHeight,
}: TacticalPingsOverlayProps) {
  const w = viewportWidth ?? containerRef?.current?.clientWidth ?? (typeof window !== 'undefined' ? window.innerWidth : 800);
  const h = viewportHeight ?? containerRef?.current?.clientHeight ?? (typeof window !== 'undefined' ? window.innerHeight : 600);

  const projected = useMemo(() => {
    if (!pings || pings.length === 0 || !camera || !playerPos) return [];

    const forward = new THREE.Vector3();
    camera.getWorldDirection(forward);

    const margin = 38;
    const minX = margin;
    const maxX = Math.max(margin + 1, w - margin);
    const minY = margin;
    const maxY = Math.max(margin + 1, h - margin);
    const centerX = w / 2;
    const centerY = h / 2;

    const list: ProjectedPing[] = [];

    for (const p of pings) {
      // Distance calculation in world cubes (meters)
      const dx = p.x - playerPos.x;
      const dy = p.y - playerPos.y;
      const dz = p.z - playerPos.z;
      const distance = Math.hypot(dx, dy, dz);

      // Three.js world position: (worldX, worldHeight, worldY)
      const worldPos = new THREE.Vector3(p.x, p.z, p.y);
      const toPing = worldPos.clone().sub(camera.position);
      const inFront = toPing.dot(forward) > 0;

      // Project into NDC
      const projectedVec = worldPos.clone().project(camera);

      // Screen coordinates
      let sx = (projectedVec.x * 0.5 + 0.5) * w;
      let sy = (-projectedVec.y * 0.5 + 0.5) * h;

      if (!inFront) {
        // If behind camera, invert direction vector from center
        sx = centerX - (sx - centerX);
        sy = centerY - (sy - centerY);
      }

      const rawDx = sx - centerX;
      const rawDy = sy - centerY;
      const angle = Math.atan2(rawDy, rawDx);

      const inView = inFront && sx >= minX && sx <= maxX && sy >= minY && sy <= maxY;

      let screenX = sx;
      let screenY = sy;

      if (!inView) {
        // Clamp to screen perimeter along direction ray
        const absCos = Math.abs(Math.cos(angle));
        const absSin = Math.abs(Math.sin(angle));
        const halfW = (w - margin * 2) / 2;
        const halfH = (h - margin * 2) / 2;

        let scale = 1;
        if (halfW * absSin <= halfH * absCos) {
          scale = halfW / (absCos || 1e-5);
        } else {
          scale = halfH / (absSin || 1e-5);
        }

        screenX = centerX + Math.cos(angle) * scale;
        screenY = centerY + Math.sin(angle) * scale;
      }

      list.push({
        row: p,
        screenX,
        screenY,
        distance,
        inView,
        angle,
        opacity: 1.0,
      });
    }

    return list;
  }, [pings, camera, playerPos, w, h]);

  if (projected.length === 0) return null;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        overflow: 'hidden',
        zIndex: 40,
      }}
    >
      {projected.map(({ row, screenX, screenY, distance, inView, angle, opacity }) => {
        const opt = CALLOUT_OPTIONS[row.kind] || CALLOUT_OPTIONS.spotted;
        const distMeters = Math.round(distance);

        if (inView) {
          return (
            <div
              key={row.id}
              style={{
                position: 'absolute',
                left: screenX,
                top: screenY,
                transform: 'translate(-50%, -100%)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                opacity,
                transition: 'opacity 0.2s ease',
              }}
            >
              {/* Floating callout badge */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '4px 8px',
                  borderRadius: 6,
                  background: 'rgba(15, 23, 42, 0.88)',
                  backdropFilter: 'blur(6px)',
                  border: `1.5px solid ${opt.borderRgba}`,
                  boxShadow: `0 0 16px ${opt.glowRgba}`,
                  color: '#ffffff',
                  marginBottom: 6,
                  animation: 'pulse 1.8s infinite',
                }}
              >
                <div style={{ color: opt.color, display: 'flex', alignItems: 'center' }}>
                  {opt.icon}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 800,
                        letterSpacing: '0.06em',
                        color: opt.color,
                        textShadow: `0 0 8px ${opt.color}`,
                      }}
                    >
                      {opt.label.split(' ')[0]}
                    </span>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        color: 'rgba(255, 255, 255, 0.9)',
                        background: 'rgba(255, 255, 255, 0.12)',
                        padding: '1px 4px',
                        borderRadius: 3,
                      }}
                    >
                      {distMeters}m
                    </span>
                  </div>
                  <span
                    style={{
                      fontSize: 9,
                      fontWeight: 500,
                      color: 'rgba(255, 255, 255, 0.65)',
                      marginTop: 1,
                    }}
                  >
                    {row.ownerName}
                  </span>
                </div>
              </div>

              {/* In-world vertical beacon line */}
              <div
                style={{
                  width: 2,
                  height: 32,
                  background: `linear-gradient(to bottom, ${opt.color}, transparent)`,
                  boxShadow: `0 0 8px ${opt.color}`,
                }}
              />

              {/* Ground pulse beacon ring */}
              <div
                style={{
                  width: 12,
                  height: 6,
                  borderRadius: '50%',
                  border: `1.5px solid ${opt.color}`,
                  background: opt.bgRgba,
                  boxShadow: `0 0 10px ${opt.color}`,
                }}
              />
            </div>
          );
        }

        // Off-screen clamped indicator
        const deg = (angle * 180) / Math.PI;
        return (
          <div
            key={row.id}
            style={{
              position: 'absolute',
              left: screenX,
              top: screenY,
              transform: 'translate(-50%, -50%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              opacity,
              zIndex: 45,
            }}
          >
            {/* Edge badge */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                padding: '3px 7px',
                borderRadius: 20,
                background: 'rgba(15, 23, 42, 0.92)',
                border: `1.5px solid ${opt.borderRgba}`,
                boxShadow: `0 0 14px ${opt.glowRgba}`,
                color: opt.color,
              }}
            >
              <div style={{ transform: 'scale(0.75)', display: 'flex' }}>
                {opt.icon}
              </div>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 800,
                  color: '#ffffff',
                }}
              >
                {distMeters}m
              </span>
              {/* Direction chevron */}
              <div
                style={{
                  transform: `rotate(${deg}deg)`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: opt.color,
                }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M5 3l14 9-14 9V3z" />
                </svg>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
