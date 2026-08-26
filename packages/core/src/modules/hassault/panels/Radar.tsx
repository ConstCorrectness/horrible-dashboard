/**
 * The radar, the utility tray, and the flashbang.
 *
 * Three small HUD pieces that share one idea: **they draw what the server said,
 * and nothing else.** The radar in particular is not a client-side decision —
 * which enemies appear on it is `MatchRoom.spotted_by`, resolved on the server
 * because only the server holds the two things the answer depends on, the
 * level's geometry and the smoke standing in it.
 *
 * The map outline is drawn from the cube grid the client already downloaded, so
 * a radar costs no extra request and no extra byte on the wire. It is rasterised
 * **once per map** into an offscreen canvas and blitted every frame; walking a
 * 256×256 grid sixty times a second to draw the same walls is the obvious way to
 * make a minimap cost more than the game.
 */
import { useEffect, useMemo, useRef } from 'react';

import type { TacticalSpec } from '../api';
import type { PlayerRow } from '../net';
import type { World } from '../world';

/** Size of the radar in CSS pixels. */
const SIZE = 168;

/** How many cubes fit across the radar. Everything outside is clipped. */
const SPAN = 110;

const TEAM_YOU = '#7ee787';
const TEAM_MATE = '#58a6ff';
const TEAM_ENEMY = '#f85149';

/**
 * Rasterise the map's walls once, into a canvas that is then just blitted.
 *
 * Drawn from `isSolid` rather than from the mesh: the radar wants the floor plan
 * — where you cannot walk — and the mesh is a set of surfaces, which is a
 * different question with a much more expensive answer.
 */
function renderMap(world: World, scale: number): HTMLCanvasElement | null {
  if (typeof document === 'undefined') return null;
  const size = Math.ceil(world.ssize * scale);
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.fillStyle = 'rgba(140, 170, 210, 0.16)';
  for (let y = 0; y < world.ssize; y += 1) {
    for (let x = 0; x < world.ssize; x += 1) {
      // Open cells are the *floor plan*, so they are what gets painted — solid
      // cube is the negative space, and painting that instead gives a map that
      // reads inside-out.
      if (world.isSolid(x, y)) continue;
      ctx.fillRect(x * scale, y * scale, scale, scale);
    }
  }
  return canvas;
}

export interface RadarProps {
  world: World | null;
  /** Us. Cube coordinates and yaw. */
  me: { x: number; y: number; yaw: number } | null;
  myId: string;
  myTeam: number;
  rows: PlayerRow[];
  /** Enemy ids our team can see, straight from `you.spotted`. */
  spotted: readonly string[];
}

export function Radar({ world, me, myId, myTeam, rows, spotted }: RadarProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const scale = 2;
  // Rebuilt only when the map changes. The dependency is the world object
  // itself, which is replaced wholesale on a map load.
  const plan = useMemo(() => (world ? renderMap(world, scale) : null), [world]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !me) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== SIZE * dpr) {
      canvas.width = SIZE * dpr;
      canvas.height = SIZE * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, SIZE, SIZE);

    const radius = SIZE / 2;
    const pxPerCube = SIZE / SPAN;

    ctx.save();
    ctx.beginPath();
    ctx.arc(radius, radius, radius - 1, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = 'rgba(8, 12, 18, 0.72)';
    ctx.fillRect(0, 0, SIZE, SIZE);

    // Rotated so *up is where you are looking*. The alternative — north-up — is
    // easier to draw and much harder to read under pressure: it makes every
    // glance a mental rotation before it is information.
    ctx.translate(radius, radius);
    ctx.rotate(-me.yaw - Math.PI / 2);
    if (plan) {
      ctx.drawImage(
        plan,
        -me.x * pxPerCube,
        -me.y * pxPerCube,
        (plan.width / scale) * pxPerCube,
        (plan.height / scale) * pxPerCube,
      );
    }

    for (const row of rows) {
      if (row.id === myId || !row.alive) continue;
      const friendly = row.team === myTeam;
      // Teammates are unconditional — that is a radio, and every team shooter
      // works that way. An enemy has to have been seen by somebody on our side.
      if (!friendly && !spotted.includes(row.id)) continue;
      const px = (row.x - me.x) * pxPerCube;
      const py = (row.y - me.y) * pxPerCube;
      if (Math.hypot(px, py) > radius) continue;
      ctx.beginPath();
      ctx.arc(px, py, friendly ? 3.2 : 3.8, 0, Math.PI * 2);
      ctx.fillStyle = friendly ? TEAM_MATE : TEAM_ENEMY;
      ctx.fill();
    }
    ctx.restore();

    // Us, drawn last and unrotated: a triangle at the centre pointing up, which
    // is the fixed reference everything else is read against.
    ctx.save();
    ctx.translate(radius, radius);
    ctx.beginPath();
    ctx.moveTo(0, -6);
    ctx.lineTo(4.5, 5);
    ctx.lineTo(0, 2.5);
    ctx.lineTo(-4.5, 5);
    ctx.closePath();
    ctx.fillStyle = TEAM_YOU;
    ctx.fill();
    ctx.restore();

    ctx.beginPath();
    ctx.arc(radius, radius, radius - 1, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(180, 200, 230, 0.28)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }, [plan, me, myId, myTeam, rows, spotted]);

  if (!me) return null;
  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        top: 12,
        left: 12,
        width: SIZE,
        height: SIZE,
        pointerEvents: 'none',
        borderRadius: '50%',
      }}
    />
  );
}

/**
 * A vector glyph per grenade kind.
 *
 * Stroke icons rather than emoji, per the project's icon rule: they take their
 * colour from the container, so "readied", "carried" and "spent" are one drawing
 * in three colours instead of three pictures.
 */
function NadeGlyph({ kind, color }: { kind: string; color: string }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: color,
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };
  if (kind === 'smoke') {
    return (
      <svg {...common} aria-hidden="true">
        <path d="M5 15a3.5 3.5 0 0 1 .8-6.9A4.6 4.6 0 0 1 15 7.4a3.4 3.4 0 0 1 4 3.3 3.4 3.4 0 0 1-3.4 3.4z" />
        <path d="M7 18h11M9 21h7" />
      </svg>
    );
  }
  if (kind === 'flash') {
    return (
      <svg {...common} aria-hidden="true">
        <path d="M13 2 5 13h6l-1 9 8-11h-6z" />
      </svg>
    );
  }
  if (kind === 'fire') {
    return (
      <svg {...common} aria-hidden="true">
        <path d="M12 22a6 6 0 0 0 6-6c0-4-3-5-3-9 0 0-3 1.5-3 5 0-2-1.5-3-1.5-3S6 11 6 16a6 6 0 0 0 6 6z" />
      </svg>
    );
  }
  // HE: a fragmentation body with a spoon.
  return (
    <svg {...common} aria-hidden="true">
      <path d="M9 8h6a5 5 0 0 1 5 5v1a6 6 0 0 1-6 6h-4a6 6 0 0 1-6-6v-1a5 5 0 0 1 5-5z" />
      <path d="M11 8V5h3v3M14 5l3-1" />
    </svg>
  );
}

export interface NadeTrayProps {
  specs: readonly TacticalSpec[];
  counts: Readonly<Record<string, number>>;
  selected: number;
}

/** What you are carrying, and which one is readied. */
export function NadeTray({ specs, counts, selected }: NadeTrayProps) {
  if (specs.length === 0) return null;
  return (
    <div
      style={{
        position: 'absolute',
        right: 12,
        bottom: 12,
        display: 'flex',
        gap: 6,
        pointerEvents: 'none',
        fontFamily: 'ui-monospace, monospace',
      }}
    >
      {specs.map((spec, i) => {
        const count = counts[spec.id] ?? 0;
        const empty = count <= 0;
        const active = i === selected;
        const color = empty
          ? 'rgba(255,255,255,0.25)'
          : active
            ? '#fbbf24'
            : 'rgba(255,255,255,0.75)';
        return (
          <div
            key={spec.id}
            title={`${spec.name} — ${i + 6}`}
            style={{
              width: 40,
              height: 44,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 2,
              background: active ? 'rgba(251, 191, 36, 0.12)' : 'rgba(8, 12, 18, 0.55)',
              // A 2px top accent on the readied one rather than a glow all
              // round: it should read as selected, not as neon.
              borderTop: `2px solid ${active ? '#fbbf24' : 'transparent'}`,
              border: '1px solid rgba(255,255,255,0.12)',
              borderTopWidth: 2,
              borderTopColor: active ? '#fbbf24' : 'rgba(255,255,255,0.12)',
              borderRadius: 4,
              opacity: empty ? 0.45 : 1,
            }}
          >
            <NadeGlyph kind={spec.type} color={color} />
            <span style={{ fontSize: '0.62rem', color, letterSpacing: '0.06em' }}>{count}</span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The flashbang, as a white-out that fades.
 *
 * `strength` is `you.flash` — resolved per player on the server from where they
 * were looking and whether a wall was in the way, so this is a renderer for a
 * number rather than a client-side effect. Squared on the way in, so the tail of
 * a flash clears quickly and the peak is what hurts: a linear fade spends most
 * of its duration at a brightness that is annoying rather than blinding.
 */
export function FlashOverlay({ strength }: { strength: number }) {
  if (strength <= 0.01) return null;
  const eased = strength * strength;
  return (
    <div
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        background: '#ffffff',
        opacity: Math.min(1, eased),
        // No transition: the value already arrives smoothed from the server, and
        // a CSS ease on top would lag the recovery behind the simulation.
        mixBlendMode: 'screen',
      }}
    />
  );
}
