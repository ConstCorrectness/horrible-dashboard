/**
 * The radar, the utility tray, and the flashbang.
 *
 * Three small HUD pieces that share one idea: **they draw what the server said,
 * and nothing else.** The radar in particular is not a client-side decision —
 * which enemies appear on it is `MatchRoom.spotted_by`, resolved on the server
 * because only the server holds the two things the answer depends on, the
 * level's geometry and the smoke standing in it. The same goes for gunfire
 * (`you.heard`, `MatchRoom.heard_by_side`) and for the sound blobs, which are
 * drawn from the noise envelope's bearing and its quantised `est`.
 *
 * The map is drawn from the cube grid the client already downloaded, so a radar
 * costs no extra request and no extra byte on the wire. The floor is rasterised
 * **once per map** into an offscreen canvas and blitted every frame; the wall
 * outline is one `Path2D`, also built once, stroked under the frame's transform.
 * Walking a 256×256 grid sixty times a second to draw the same walls is the
 * obvious way to make a minimap cost more than the game.
 *
 * Every number that decides where something lands, how big it is or what colour
 * it is lives in `radar-view.ts`, pinned against the native radar by
 * `radar-vectors.json`.
 */
import { useEffect, useMemo, useRef } from "react";

import type { TacticalSpec } from "../api";
import type { FlashLayer } from "../flash-layer";
import type {
  HeardRow,
  ModeBomb,
  ModeSite,
  NoiseEvent,
  PingRow,
  PlayerRow,
} from "../net";
import {
  blipRadius,
  fadeOut,
  FLOOR_BANDS,
  floorBand,
  floorColor,
  floorRange,
  GRID_CUBES,
  HEARD_SECONDS,
  LAST_KNOWN_SECONDS,
  OWN_NOISE_SECONDS,
  project,
  RADAR_COLORS,
  radarFrame,
  type RadarSettings,
  type Rgba,
  SOUND_PING_CUBES,
  SOUND_PING_SECONDS,
  wallEdges,
} from "../radar-view";
import type { World } from "../world";

/** Size of the radar in CSS pixels at `hudScale` 1. */
const SIZE = 168;
/** How much bigger the instrument grows while it shows the whole map. */
const FULL_MAP_GROWTH = 1.5;
/** Canvas pixels per cube in the offscreen floor plan. */
const PLAN_SCALE = 2;

const PING_RADAR_COLORS: Record<string, string> = {
  spotted: "rgb(239, 68, 68)",
  watch: "rgb(6, 182, 212)",
  danger: "rgb(245, 158, 11)",
  utility: "rgb(59, 130, 246)",
};

/** A palette entry as a canvas colour, optionally faded. */
function css(c: Rgba, alpha = 1): string {
  const [r, g, b, a] = c;
  return `rgba(${Math.round(r * 255)}, ${Math.round(g * 255)}, ${Math.round(b * 255)}, ${(a * alpha).toFixed(3)})`;
}

/** The floor plan: open cells tinted by height band, rasterised once. */
function renderPlan(world: World): HTMLCanvasElement | null {
  if (typeof document === "undefined") return null;
  const size = Math.ceil(world.ssize * PLAN_SCALE);
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const solid = (x: number, y: number) => world.isSolid(x, y);
  const [lo, hi] = floorRange(world.ssize, solid, (x, y) =>
    world.floorAt(x, y),
  );
  // One fillStyle per band rather than per cell: setting a style is the
  // expensive half of a fillRect.
  const bands: [number, number][][] = Array.from({ length: FLOOR_BANDS }, () => []);
  for (let y = 0; y < world.ssize; y += 1) {
    for (let x = 0; x < world.ssize; x += 1) {
      // Open cells are the *floor plan*, so they are what gets painted — solid
      // cube is the negative space, and painting that instead gives a map that
      // reads inside-out.
      if (solid(x, y)) continue;
      bands[floorBand(world.floorAt(x, y), lo, hi)].push([x, y]);
    }
  }
  bands.forEach((cells, band) => {
    ctx.fillStyle = css(floorColor(band));
    for (const [x, y] of cells)
      ctx.fillRect(x * PLAN_SCALE, y * PLAN_SCALE, PLAN_SCALE, PLAN_SCALE);
  });
  return canvas;
}

/** The wall outline as one path in cube coordinates. */
function renderWalls(world: World): Path2D | null {
  if (typeof Path2D === "undefined") return null;
  const path = new Path2D();
  for (const e of wallEdges(world.ssize, (x, y) => world.isSolid(x, y))) {
    path.moveTo(e.x0, e.y0);
    path.lineTo(e.x1, e.y1);
  }
  return path;
}

/** One enemy footstep, landing or reload, as heard. */
export interface RadarSound {
  at: number;
  event: NoiseEvent;
  /** Where *we* stood when we heard it: the blob is anchored to the ear. */
  from: { x: number; y: number };
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
  /** Tactical team pings. */
  pings?: readonly PingRow[];
  /** Gunfire our side heard, straight from `you.heard`. */
  heard?: readonly HeardRow[];
  /** Recent noise envelope entries, for the footstep blobs. */
  sounds?: readonly RadarSound[];
  sites?: readonly ModeSite[];
  bomb?: ModeBomb;
  settings: RadarSettings;
  scoresHeld: boolean;
  zoomHeld: boolean;
  /** Our own last noise: its audible radius in cubes and `Date.now()` it was made. */
  ownNoise?: { radius: number; at: number } | null;
}

interface LastKnown {
  x: number;
  y: number;
  /** `Date.now()` the contact was lost; 0 while it is still spotted. */
  lost: number;
}

export function Radar({
  world,
  me,
  myId,
  myTeam,
  rows,
  spotted,
  pings,
  heard,
  sounds,
  sites,
  bomb,
  settings,
  scoresHeld,
  zoomHeld,
  ownNoise,
}: RadarProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Rebuilt only when the map changes. The dependency is the world object
  // itself, which is replaced wholesale on a map load.
  const plan = useMemo(() => (world ? renderPlan(world) : null), [world]);
  const walls = useMemo(() => (world ? renderWalls(world) : null), [world]);
  // Where each enemy was last seen. Only positions this client was already sent
  // while they were spotted — the "?" adds memory, not information.
  const lastKnown = useRef(new Map<string, LastKnown>());

  const full = scoresHeld && settings.fullOnScores;
  const size = Math.round(
    SIZE * settings.hudScale * (full ? FULL_MAP_GROWTH : 1),
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !me) return;
    const now = Date.now();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== size * dpr) {
      canvas.width = size * dpr;
      canvas.height = size * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);

    const radius = size / 2;
    const ssize = world?.ssize ?? 256;
    const frame = radarFrame(settings, me, ssize, scoresHeld, zoomHeld);
    const perCube = (radius * 2) / frame.span;
    const at = (x: number, y: number): [number, number] => {
      const [px, py] = project(frame, radius, x, y);
      return [radius + px, radius + py];
    };
    const inside = (x: number, y: number, pad = 0) =>
      Math.hypot(x - radius, y - radius) <= radius - pad;
    const icon = (kind: Parameters<typeof blipRadius>[0]) =>
      blipRadius(kind, settings.iconScale) * radius;

    // ---- the instrument and the map, under the world transform ----
    ctx.save();
    ctx.beginPath();
    ctx.arc(radius, radius, radius - 1, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = css(RADAR_COLORS.ground);
    ctx.fillRect(0, 0, size, size);

    ctx.save();
    ctx.translate(radius, radius);
    ctx.rotate(frame.angle);
    ctx.scale(perCube, perCube);
    ctx.translate(-frame.cx, -frame.cy);
    if (plan) ctx.drawImage(plan, 0, 0, ssize, ssize);

    // A faint world-space grid: scale you can read distance off, and the
    // subtle structure the HUD uses instead of gradients.
    ctx.strokeStyle = css(RADAR_COLORS.grid);
    ctx.lineWidth = 1 / perCube;
    ctx.beginPath();
    for (let g = 0; g <= ssize; g += GRID_CUBES) {
      ctx.moveTo(g, 0);
      ctx.lineTo(g, ssize);
      ctx.moveTo(0, g);
      ctx.lineTo(ssize, g);
    }
    ctx.stroke();

    // Bomb sites under the walls, so a site's edge never hides a corridor.
    for (const site of sites ?? []) {
      ctx.beginPath();
      ctx.arc(site.x, site.y, site.radius, 0, Math.PI * 2);
      ctx.fillStyle = css(RADAR_COLORS.site);
      ctx.fill();
      ctx.setLineDash([3 / perCube, 2 / perCube]);
      ctx.strokeStyle = css(RADAR_COLORS.siteEdge, 0.7);
      ctx.lineWidth = 1 / perCube;
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (walls) {
      ctx.strokeStyle = css(RADAR_COLORS.wall);
      ctx.lineWidth = 1.15 / perCube;
      ctx.lineCap = "square";
      ctx.stroke(walls);
    }

    // CS2's "how far can I be heard" ring, round wherever we are.
    if (ownNoise) {
      const life = fadeOut((now - ownNoise.at) / 1000, OWN_NOISE_SECONDS);
      if (life > 0) {
        ctx.beginPath();
        // Grows into its radius as it fades, so it reads as a sound going out
        // rather than a circle being drawn.
        ctx.arc(
          me.x,
          me.y,
          ownNoise.radius * (1 - life * 0.25),
          0,
          Math.PI * 2,
        );
        ctx.strokeStyle = css(RADAR_COLORS.ownNoise, life);
        ctx.lineWidth = 1.2 / perCube;
        ctx.stroke();
      }
    }

    // Enemy footsteps: soft, wide, "about there". Anchored to where we stood
    // when we heard them, along the bearing, at the distance they *sounded*.
    if (settings.soundPings) {
      for (const s of sounds ?? []) {
        if (!s.event.foe || s.event.est === undefined) continue;
        const life = fadeOut((now - s.at) / 1000, SOUND_PING_SECONDS);
        if (life <= 0) continue;
        const wx = s.from.x + Math.cos(s.event.bearing) * s.event.est;
        const wy = s.from.y + Math.sin(s.event.bearing) * s.event.est;
        const blob = ctx.createRadialGradient(
          wx,
          wy,
          0,
          wx,
          wy,
          SOUND_PING_CUBES,
        );
        blob.addColorStop(0, css(RADAR_COLORS.soundPing, life));
        blob.addColorStop(1, css(RADAR_COLORS.soundPing, 0));
        ctx.fillStyle = blob;
        ctx.beginPath();
        ctx.arc(wx, wy, SOUND_PING_CUBES, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();

    // ---- labels and blips, in screen space so they stay upright ----
    for (const site of sites ?? []) {
      // Up and to the left of the centre, inside the disc: the middle is where
      // the bomb gets planted, and a letter under the bomb is one you cannot read.
      const offset = site.radius * perCube * 0.55;
      const [sx0, sy0] = at(site.x, site.y);
      const sx = sx0 - offset;
      const sy = sy0 - offset;
      if (!inside(sx, sy, 6)) continue;
      ctx.font = `800 ${Math.max(9, radius * 0.13).toFixed(1)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = css(RADAR_COLORS.siteEdge);
      ctx.fillText(site.id.toUpperCase(), sx, sy);
    }

    // Last-known: update the memory from this frame's contacts first.
    const known = lastKnown.current;
    const alive = new Map(rows.filter((r) => r.alive).map((r) => [r.id, r]));
    for (const row of rows) {
      if (row.team === myTeam || row.id === myId) continue;
      if (row.alive && spotted.includes(row.id))
        known.set(row.id, { x: row.x, y: row.y, lost: 0 });
    }
    for (const [id, k] of known) {
      if (
        !alive.has(id) ||
        (k.lost > 0 && now - k.lost > LAST_KNOWN_SECONDS * 1000)
      ) {
        known.delete(id);
      } else if (k.lost === 0 && !spotted.includes(id)) {
        k.lost = now;
      }
    }
    for (const k of known.values()) {
      if (k.lost === 0) continue;
      const life = fadeOut((now - k.lost) / 1000, LAST_KNOWN_SECONDS);
      const [kx, ky] = at(k.x, k.y);
      if (!inside(kx, ky)) continue;
      const r = icon("lastKnown");
      ctx.beginPath();
      ctx.arc(kx, ky, r, 0, Math.PI * 2);
      ctx.strokeStyle = css(RADAR_COLORS.enemy, 0.85 * life);
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.font = `700 ${(r * 1.7).toFixed(1)}px ui-monospace, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = css(RADAR_COLORS.enemy, life);
      ctx.fillText("?", kx, ky + 0.5);
    }

    // Gunfire: an exact dot at the shot's origin, with a ring going out from
    // it on arrival so a new one is noticed rather than merely present.
    for (const h of heard ?? []) {
      const life = fadeOut(h.age, HEARD_SECONDS);
      if (life <= 0) continue;
      const [hx, hy] = at(h.x, h.y);
      if (!inside(hx, hy)) continue;
      const r = icon("heard");
      const arrive = Math.min(1, h.age / 0.5);
      if (arrive < 1) {
        ctx.beginPath();
        ctx.arc(hx, hy, r * (1 + arrive * 2.5), 0, Math.PI * 2);
        ctx.strokeStyle = css(RADAR_COLORS.enemy, 1 - arrive);
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(hx, hy, r * Math.min(1, 0.55 + h.age * 4), 0, Math.PI * 2);
      ctx.fillStyle = css(RADAR_COLORS.enemy, life);
      ctx.fill();
    }

    for (const row of rows) {
      if (row.id === myId || !row.alive) continue;
      const friendly = row.team === myTeam;
      // Teammates are unconditional — that is a radio, and every team shooter
      // works that way. An enemy has to have been seen by somebody on our side.
      if (!friendly && !spotted.includes(row.id)) continue;
      const [px, py] = at(row.x, row.y);
      if (!inside(px, py)) continue;
      const r = icon(friendly ? "mate" : "enemy");
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = css(friendly ? RADAR_COLORS.mate : RADAR_COLORS.enemy);
      ctx.fill();
      if (bomb?.state === "carried" && bomb.carrier === row.id) {
        ctx.beginPath();
        ctx.arc(px, py, r + 2, 0, Math.PI * 2);
        ctx.strokeStyle = css(RADAR_COLORS.bomb);
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }
    }

    // The bomb on the ground or in a site. Planted, it pulses faster as the
    // fuse runs down — the one thing on the instrument that is a clock.
    if (
      bomb &&
      (bomb.state === "dropped" || bomb.state === "planted") &&
      bomb.x !== undefined &&
      bomb.y !== undefined
    ) {
      const [bx, by] = at(bomb.x, bomb.y);
      const pad = icon("bomb");
      const cx = Math.max(pad, Math.min(size - pad, bx));
      const cy = Math.max(pad, Math.min(size - pad, by));
      const rate =
        bomb.state === "planted" ? 2 + 6 / Math.max(1, bomb.fuseIn ?? 40) : 0;
      const pulse =
        rate > 0
          ? 0.6 + 0.4 * Math.abs(Math.sin((now / 1000) * Math.PI * rate))
          : 1;
      drawBomb(ctx, cx, cy, pad, css(RADAR_COLORS.bomb, pulse));
    }

    // Tactical pings: diamond blips in callout colour, pinned to the rim when
    // they are out of range.
    for (const p of pings ?? []) {
      const [px0, py0] = project(frame, radius, p.x, p.y);
      const dist = Math.hypot(px0, py0);
      const factor = dist > 0 ? Math.min(dist, radius - 5) / dist : 0;
      const r = icon("ping");
      ctx.save();
      ctx.translate(radius + px0 * factor, radius + py0 * factor);
      ctx.beginPath();
      ctx.moveTo(0, -r);
      ctx.lineTo(r, 0);
      ctx.lineTo(0, r);
      ctx.lineTo(-r, 0);
      ctx.closePath();
      ctx.fillStyle = PING_RADAR_COLORS[p.kind] || "rgb(224, 185, 106)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.restore();
    }
    ctx.restore();

    // Us, drawn last: an arrow pointing up on a rotating radar, turning with us
    // on a north-up one, and off-centre when the radar is not always centred.
    const [ax, ay] = at(me.x, me.y);
    const s = icon("you") / 6;
    ctx.save();
    ctx.translate(ax, ay);
    ctx.rotate(frame.arrowAngle);
    ctx.beginPath();
    ctx.moveTo(0, -6 * s);
    ctx.lineTo(4.5 * s, 5 * s);
    ctx.lineTo(0, 2.5 * s);
    ctx.lineTo(-4.5 * s, 5 * s);
    ctx.closePath();
    ctx.fillStyle = css(RADAR_COLORS.you);
    ctx.fill();
    ctx.restore();

    ctx.beginPath();
    ctx.arc(radius, radius, radius - 1, 0, Math.PI * 2);
    ctx.strokeStyle = css(RADAR_COLORS.rim);
    ctx.lineWidth = 1;
    ctx.stroke();
    // A 2px accent arc across the top of the rim — the HUD's top-border
    // language, bent round the instrument.
    ctx.beginPath();
    ctx.arc(radius, radius, radius - 1.5, -Math.PI * 0.68, -Math.PI * 0.32);
    ctx.strokeStyle = css(RADAR_COLORS.you, 0.7);
    ctx.lineWidth = 2;
    ctx.stroke();
  }, [
    plan,
    walls,
    world,
    me,
    myId,
    myTeam,
    rows,
    spotted,
    pings,
    heard,
    sounds,
    sites,
    bomb,
    settings,
    scoresHeld,
    zoomHeld,
    ownNoise,
    size,
  ]);

  if (!me) return null;
  return (
    <canvas
      ref={canvasRef}
      style={{
        position: "absolute",
        top: 12,
        left: 12,
        width: size,
        height: size,
        pointerEvents: "none",
        borderRadius: "50%",
        transition: "width 120ms ease-out, height 120ms ease-out",
      }}
    />
  );
}

/** The bomb: a squat case with a fuse, drawn rather than an emoji. */
function drawBomb(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  color: string,
): void {
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = color;
  ctx.strokeStyle = "rgba(8, 10, 14, 0.9)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.rect(-r, -r * 0.6, r * 2, r * 1.3);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(r * 0.4, -r * 0.6);
  ctx.lineTo(r * 0.9, -r * 1.2);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;
  ctx.stroke();
  ctx.restore();
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
 * The flashbang: a white-out, with the last frame you saw burned in under it.
 *
 * Renders two empty layers and hands them to a `FlashLayer` (`flash-layer.ts`),
 * which the render loop drives directly. The white moves every frame for seconds
 * at a time, and routing that through React state would re-render the whole pane
 * at 60 Hz to change one opacity.
 *
 * How white, and for how long, is the server's answer (`grenades.flash_effect`),
 * run through the shared curve in `flashbang.ts` — so this is a renderer for a
 * number, not a client-side effect.
 */
export function FlashOverlay({ layer }: { layer: FlashLayer }) {
  const white = useRef<HTMLDivElement>(null);
  const afterimage = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (white.current && afterimage.current) {
      layer.attach({ white: white.current, afterimage: afterimage.current });
    }
    return () => layer.attach(null);
  }, [layer]);
  const fill = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    pointerEvents: 'none',
    opacity: 0,
  } as const;
  return (
    <div aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      {/* Slightly blurred and brightened: an afterimage is a ghost, not a
          screenshot, and a sharp copy reads as the game having frozen. */}
      <canvas ref={afterimage} style={{ ...fill, filter: 'blur(1.5px) brightness(1.35)' }} />
      <div ref={white} style={{ ...fill, background: '#ffffff' }} />
    </div>
  );
}

/** What the thrower is told: counts and the longest blind, never who or where. */
export interface FlashAssist {
  enemies: number;
  allies: number;
  longest: number;
  /** `Date.now()` when it arrived, so a second assist restarts the entrance. */
  at: number;
}

/** Seconds the assist readout stays up. */
const ASSIST_MS = 2600;

/**
 * "FLASHED 2 · 1.9s" for the thrower, under the crosshair.
 *
 * The seconds roll up to their value rather than appearing, per the HUD's rule
 * for numbers that changed — seeded at the final value and snapped to it by a
 * timeout, because `requestAnimationFrame` does not fire in a backgrounded tab
 * and a readout stuck at 0.0 would be worse than no animation at all.
 */
export function FlashAssistToast({ assist }: { assist: FlashAssist | null }) {
  const seconds = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = seconds.current;
    if (!assist || !el) return;
    const final = `${assist.longest.toFixed(1)}s`;
    el.textContent = final;
    const started = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const k = Math.min(1, (t - started) / 450);
      el.textContent = `${(assist.longest * k).toFixed(1)}s`;
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const snap = setTimeout(() => {
      cancelAnimationFrame(raf);
      el.textContent = final;
    }, 500);
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(snap);
    };
  }, [assist]);
  if (!assist || Date.now() - assist.at > ASSIST_MS) return null;
  const teamkill = assist.enemies === 0;
  const accent = teamkill ? '#f87171' : '#fbbf24';
  return (
    <div
      key={assist.at}
      aria-live="polite"
      style={{
        position: 'absolute',
        left: '50%',
        top: '62%',
        transform: 'translateX(-50%)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.5rem',
        padding: '0.3rem 0.7rem',
        background: 'rgba(8, 10, 14, 0.72)',
        borderTop: `2px solid ${accent}`,
        clipPath: 'polygon(6px 0, 100% 0, calc(100% - 6px) 100%, 0 100%)',
        pointerEvents: 'none',
        color: accent,
        animation: `hassault-assist-in 180ms ease-out, hassault-assist-out 400ms ease-in ${ASSIST_MS - 400}ms forwards`,
      }}
    >
      <style>{`@keyframes hassault-assist-in { from { opacity: 0; transform: translate(-50%, 6px); } }
@keyframes hassault-assist-out { to { opacity: 0; } }`}</style>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
        <circle cx="8" cy="8" r="2.4" />
        <path d="M8 1.5v2.2M8 12.3v2.2M1.5 8h2.2M12.3 8h2.2M3.4 3.4l1.5 1.5M11.1 11.1l1.5 1.5M3.4 12.6l1.5-1.5M11.1 4.9l1.5-1.5" />
      </svg>
      <span style={{ fontWeight: 700, letterSpacing: '0.14em', fontSize: '0.7rem' }}>
        {teamkill ? 'TEAM FLASHED' : 'FLASHED'}
      </span>
      <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: 'rgba(255,255,255,0.7)' }}>
        {teamkill ? assist.allies : assist.enemies}
        {!teamkill && assist.allies > 0 ? ` +${assist.allies} TEAM` : ''} ·{' '}
        <span ref={seconds}>{assist.longest.toFixed(1)}s</span>
      </span>
    </div>
  );
}
