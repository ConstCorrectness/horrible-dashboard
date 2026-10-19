/**
 * Drawing a crosshair spec: the in-game reticle and the generator's preview are
 * this one component, so the preview is the reticle rather than a picture of it.
 *
 * The geometry is `crosshairRects` (`crosshair.ts`), pinned to the native HUD by
 * `crosshair-vectors.json`; all this file decides is colour by layer. The outline
 * is the native HUD's `OUTLINE` near-black.
 *
 * The hitmarker is a **separate** layer drawn outside the arms. It never
 * recolours or reshapes the reticle: while spraying into somebody — the moment
 * aim matters most — the thing you are aiming with must not change under you.
 */
import { useMemo, useSyncExternalStore } from 'react';

import { getSetting, settingsStore } from '../../settings';
import {
  CROSSHAIR_KEYS,
  crosshairRects,
  specFromSettings,
  type CrosshairShape,
  type CrosshairSpec,
} from './crosshair';

/** The native HUD's `OUTLINE`, `[0.02, 0.02, 0.03]`. */
const OUTLINE_RGB = '5,5,8';
/** The pane HUD's hit and kill colours, as before; drawn over the game, not the theme. */
const HIT_COLOR = 'rgb(255,209,102)';
const KILL_COLOR = 'rgb(255,107,107)';

/** The player's crosshair, read live from the settings bag and normalized. */
export function useCrosshairSpec(): CrosshairSpec {
  const snapshot = useSyncExternalStore(settingsStore.subscribe, settingsStore.getSnapshot);
  return useMemo(() => {
    const bag: Record<string, unknown> = {};
    for (const key of Object.values(CROSSHAIR_KEYS)) bag[key] = getSetting(key);
    return specFromSettings(bag);
    // The snapshot is the dependency: a new reference on every settings change.
  }, [snapshot]);
}

function extent(shapes: CrosshairShape[]): number {
  let r = 1;
  for (const s of shapes) {
    if (s.kind === 'ring') r = Math.max(r, s.r1);
    else r = Math.max(r, Math.abs(s.x), Math.abs(s.y), Math.abs(s.x + s.w), Math.abs(s.y + s.h));
  }
  return Math.ceil(r) + 2;
}

export function CrosshairShapes({
  spec,
  coneGap,
  unit,
  hit = false,
  killed = false,
}: {
  spec: CrosshairSpec;
  coneGap: number;
  unit: number;
  hit?: boolean;
  killed?: boolean;
}) {
  const shapes = crosshairRects(spec, coneGap, unit);
  const fill = spec.rgb.join(',');
  // The marker's reach, outside the arms whatever the style.
  const gap = spec.gap * unit * 0.5 + (spec.dynamic ? Math.max(0, coneGap) : 0);
  const arm = spec.size * unit;
  const near = Math.max(gap, 0) + arm * 0.7;
  const far = Math.max(gap, 0) + arm * 1.9;
  const marker = hit || killed;
  const r = Math.max(extent(shapes), marker ? Math.ceil(far) + 4 : 0);
  const d = Math.SQRT1_2;
  const markerWidth = Math.max(spec.thickness * unit * 2, 1.5);
  return (
    <svg
      width={r * 2}
      height={r * 2}
      viewBox={`${-r} ${-r} ${r * 2} ${r * 2}`}
      style={{
        position: 'absolute',
        left: -r,
        top: -r,
        overflow: 'visible',
        pointerEvents: 'none',
      }}
      aria-hidden
    >
      {shapes.map((s, i) => {
        const color = `rgba(${s.layer === 'fill' ? fill : OUTLINE_RGB},${s.alpha})`;
        if (s.kind === 'rect') {
          return <rect key={i} x={s.x} y={s.y} width={s.w} height={s.h} fill={color} />;
        }
        return (
          <circle
            key={i}
            r={(s.r0 + s.r1) / 2}
            fill="none"
            stroke={color}
            strokeWidth={s.r1 - s.r0}
          />
        );
      })}
      {marker &&
        [
          [-1, -1],
          [1, -1],
          [-1, 1],
          [1, 1],
        ].map(([sx, sy], i) => (
          <line
            key={`m${i}`}
            x1={sx * near * d}
            y1={sy * near * d}
            x2={sx * far * d}
            y2={sy * far * d}
            stroke={killed ? KILL_COLOR : HIT_COLOR}
            strokeWidth={markerWidth}
            strokeLinecap="square"
          />
        ))}
    </svg>
  );
}
