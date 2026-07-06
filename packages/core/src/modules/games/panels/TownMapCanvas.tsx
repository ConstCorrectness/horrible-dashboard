import { useEffect, useRef, useState } from 'react';

import type { TownHouse, TownResident, TownState } from '../game-ws';

/**
 * The AgentTown tile map: a canvas-rendered, procedurally drawn pixel tileset
 * (grass / cobble paths / plaza / pond / pier) with buildings, the cottage lane
 * (server-owned house lots — owner name plates once bought), a day/night tint,
 * and the residents as smooth HTML overlays (speech bubbles + stat tooltips)
 * positioned straight from the server's world coordinates.
 */

const COLS = 25;
const ROWS = 19;
const TILE = 32;
const W = COLS * TILE;
const H = ROWS * TILE;

/** Anchor points in the server's 0-100 world space (mirrors PLACE_COORDS). */
const PLACE_PCT: Record<string, { x: number; z: number }> = {
  fountain: { x: 50, z: 50 },
  bakery: { x: 20, z: 22 },
  library: { x: 80, z: 22 },
  tavern: { x: 20, z: 78 },
  docks: { x: 80, z: 78 },
  residential_zone: { x: 18, z: 50 },
  gym: { x: 82, z: 50 },
  workplace: { x: 50, z: 18 },
};

const px = (x: number) => (x / 100) * W;
const py = (z: number) => (z / 100) * H;

// ---- ground layer -----------------------------------------------------------

type Ground = 'grass' | 'grass2' | 'grass3' | 'path' | 'plaza' | 'water' | 'sand' | 'wood';

const PATHS: [string, string][] = [
  ['bakery', 'fountain'],
  ['library', 'fountain'],
  ['tavern', 'fountain'],
  ['docks', 'fountain'],
  ['residential_zone', 'fountain'],
  ['gym', 'fountain'],
  ['workplace', 'fountain'],
  ['bakery', 'workplace'],
  ['workplace', 'library'],
  ['library', 'gym'],
  ['gym', 'docks'],
  ['docks', 'tavern'],
  ['tavern', 'residential_zone'],
  ['residential_zone', 'bakery'],
];

function buildGround(): Ground[][] {
  const g: Ground[][] = [];
  for (let r = 0; r < ROWS; r++) {
    const row: Ground[] = [];
    for (let c = 0; c < COLS; c++) {
      const n = (c * 7 + r * 13) % 11;
      row.push(n === 0 ? 'grass2' : n === 5 ? 'grass3' : 'grass');
    }
    g.push(row);
  }
  // Pond in the south-east corner (the docks), with a sandy shoreline.
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const d = COLS - 1 - c + (ROWS - 1 - r);
      if (d <= 7) g[r][c] = 'water';
      else if (d <= 9) g[r][c] = 'sand';
    }
  }
  const put = (c: number, r: number) => {
    if (r < 0 || r >= ROWS || c < 0 || c >= COLS) return;
    if (g[r][c] === 'water' || g[r][c] === 'sand') return;
    g[r][c] = 'path';
  };
  for (const [a, b] of PATHS) {
    const pa = PLACE_PCT[a];
    const pb = PLACE_PCT[b];
    const steps = 80;
    for (let i = 0; i <= steps; i++) {
      const x = pa.x + ((pb.x - pa.x) * i) / steps;
      const z = pa.z + ((pb.z - pa.z) * i) / steps;
      put(Math.floor((x / 100) * COLS), Math.floor((z / 100) * ROWS));
    }
  }
  // The homes lane: a footpath running north-south past the cottage lots.
  for (let r = 5; r <= 14; r++) put(3, r);
  // The plaza: a stone diamond around the fountain.
  const fc = Math.floor((50 / 100) * COLS);
  const fr = Math.floor((50 / 100) * ROWS);
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      if (Math.abs(c - fc) + Math.abs(r - fr) <= 2) g[r][c] = 'plaza';
    }
  }
  // The pier: wood decking over the shoreline where dock life happens.
  for (let r = 13; r <= 16; r++) {
    for (let c = 18; c <= 22; c++) g[r][c] = 'wood';
  }
  return g;
}

const GROUND = buildGround();

// Trees dotted on open grass (tile coords, clear of buildings and plates).
const TREES: [number, number][] = [
  [1, 1],
  [8, 1],
  [16, 1],
  [22, 1],
  [23, 6],
  [1, 16],
  [6, 17],
  [11, 17],
  [15, 6],
  [9, 6],
];

// ---- low-level pixel helpers -------------------------------------------------

function rect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  fill: string,
): void {
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, h);
}

function plate(ctx: CanvasRenderingContext2D, cx: number, y: number, text: string): void {
  ctx.font = '700 10px system-ui, sans-serif';
  const w = ctx.measureText(text).width + 10;
  rect(ctx, cx - w / 2, y, w, 14, '#4d3a29');
  rect(ctx, cx - w / 2 + 1, y + 1, w - 2, 12, '#fdf6e3');
  ctx.fillStyle = '#4d3a29';
  ctx.fillText(text, cx - w / 2 + 5, y + 10.5);
}

// ---- tiles -------------------------------------------------------------------

function drawTile(
  ctx: CanvasRenderingContext2D,
  kind: Ground,
  c: number,
  r: number,
  t: number,
): void {
  const x = c * TILE;
  const y = r * TILE;
  const alt = (c + r) % 2 === 0;
  switch (kind) {
    case 'grass':
    case 'grass2':
    case 'grass3': {
      rect(ctx, x, y, TILE, TILE, alt ? '#8ecb84' : '#95d18b');
      if (kind === 'grass2') {
        rect(ctx, x + 6, y + 8, 4, 4, '#7dbb74');
        rect(ctx, x + 20, y + 18, 4, 4, '#7dbb74');
        rect(ctx, x + 12, y + 24, 4, 4, '#7dbb74');
      } else if (kind === 'grass3') {
        // A flowery grass variant doubles as scattered decoration.
        rect(ctx, x + 7, y + 9, 4, 4, (c * 13 + r) % 3 === 0 ? '#f0a3c0' : '#f5e17a');
        rect(ctx, x + 8, y + 10, 2, 2, '#fdf6e3');
        rect(ctx, x + 21, y + 21, 3, 3, '#f0a3c0');
      }
      break;
    }
    case 'path': {
      rect(ctx, x, y, TILE, TILE, alt ? '#dcc9a3' : '#d5c19a');
      rect(ctx, x + 4, y + 6, 8, 6, '#c8b28a');
      rect(ctx, x + 18, y + 10, 9, 7, '#cbb891');
      rect(ctx, x + 8, y + 20, 9, 6, '#c8b28a');
      rect(ctx, x + 22, y + 24, 6, 5, '#c1ab84');
      break;
    }
    case 'plaza': {
      rect(ctx, x, y, TILE, TILE, alt ? '#c9ced3' : '#c2c8ce');
      rect(ctx, x, y + TILE - 2, TILE, 2, '#aeb5bc');
      rect(ctx, x + TILE - 2, y, 2, TILE, '#aeb5bc');
      break;
    }
    case 'sand': {
      rect(ctx, x, y, TILE, TILE, alt ? '#ecdca9' : '#e6d5a0');
      rect(ctx, x + 8, y + 10, 3, 3, '#d9c48b');
      rect(ctx, x + 22, y + 20, 3, 3, '#d9c48b');
      break;
    }
    case 'water': {
      rect(ctx, x, y, TILE, TILE, alt ? '#63aede' : '#5ba7d8');
      // Slow drifting wave glints.
      const phase = Math.floor(t / 700 + c * 0.7 + r * 1.3) % 3;
      if (phase === 0) rect(ctx, x + 6, y + 10, 12, 3, '#8cc6ea');
      if (phase === 1) rect(ctx, x + 14, y + 22, 12, 3, '#8cc6ea');
      break;
    }
    case 'wood': {
      rect(ctx, x, y, TILE, TILE, alt ? '#b78a5c' : '#b18455');
      rect(ctx, x, y + 9, TILE, 2, '#96693f');
      rect(ctx, x, y + 21, TILE, 2, '#96693f');
      break;
    }
  }
}

// ---- props: trees, fountain, boat ---------------------------------------------

function drawTree(ctx: CanvasRenderingContext2D, c: number, r: number): void {
  const x = c * TILE + TILE / 2;
  const y = r * TILE + TILE / 2;
  rect(ctx, x - 3, y + 2, 6, 10, '#8a5a33');
  ctx.fillStyle = '#4e9a4e';
  ctx.beginPath();
  ctx.arc(x, y - 4, 11, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#63b063';
  ctx.beginPath();
  ctx.arc(x - 4, y - 8, 7, 0, Math.PI * 2);
  ctx.fill();
}

function drawFountain(ctx: CanvasRenderingContext2D, t: number): void {
  const x = px(50);
  const y = py(50);
  ctx.fillStyle = '#aab3bc';
  ctx.beginPath();
  ctx.arc(x, y, 21, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#dfe5ea';
  ctx.beginPath();
  ctx.arc(x, y, 17, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#6fb7e0';
  ctx.beginPath();
  ctx.arc(x, y, 13, 0, Math.PI * 2);
  ctx.fill();
  // Ripple + spray, on the world clock.
  const rr = ((t / 900) % 1) * 10;
  ctx.strokeStyle = 'rgba(255,255,255,0.6)';
  ctx.beginPath();
  ctx.arc(x, y, 3 + rr, 0, Math.PI * 2);
  ctx.stroke();
  rect(ctx, x - 3, y - 8, 6, 8, '#aab3bc');
  for (let i = 0; i < 3; i++) {
    const a = t / 300 + (i * Math.PI * 2) / 3;
    rect(ctx, x + Math.cos(a) * 6 - 1, y - 10 + Math.sin(a) * 2, 2, 2, '#ffffff');
  }
}

function drawBoat(ctx: CanvasRenderingContext2D, t: number): void {
  const x = px(93);
  const y = py(88) + Math.sin(t / 800) * 2;
  rect(ctx, x - 14, y, 28, 8, '#8a5a33');
  rect(ctx, x - 11, y - 3, 22, 4, '#a5713f');
  rect(ctx, x - 1, y - 14, 2, 12, '#6f4526');
}

// ---- buildings ----------------------------------------------------------------

interface BuildingSpec {
  place: string;
  label: string;
  icon: string;
  wall: string;
  wallDark: string;
  roof: string;
  wide: number; // body width in px
}

const BUILDINGS: BuildingSpec[] = [
  {
    place: 'bakery',
    label: 'Bakery',
    icon: '🍞',
    wall: '#ecd0a8',
    wallDark: '#d9b988',
    roof: '#c95555',
    wide: 78,
  },
  {
    place: 'library',
    label: 'Library',
    icon: '📚',
    wall: '#d9b280',
    wallDark: '#c69d68',
    roof: '#7a5cc4',
    wide: 82,
  },
  {
    place: 'tavern',
    label: 'Tavern',
    icon: '🍺',
    wall: '#c09060',
    wallDark: '#a97b4c',
    roof: '#8a5a2b',
    wide: 78,
  },
  {
    place: 'gym',
    label: 'Gym',
    icon: '🏋️',
    wall: '#b3c4d4',
    wallDark: '#9cb0c3',
    roof: '#d9534f',
    wide: 74,
  },
  {
    place: 'workplace',
    label: 'Offices',
    icon: '🏢',
    wall: '#9fb0c1',
    wallDark: '#8899ab',
    roof: '#5b6b7d',
    wide: 96,
  },
];

/** Window spots that should glow after dark — collected while drawing. */
let nightWindows: { x: number; y: number }[] = [];

function windowPane(ctx: CanvasRenderingContext2D, x: number, y: number): void {
  rect(ctx, x, y, 10, 10, '#54687c');
  rect(ctx, x + 1, y + 1, 8, 8, '#7f97ad');
  nightWindows.push({ x: x + 5, y: y + 5 });
}

function drawBuilding(ctx: CanvasRenderingContext2D, spec: BuildingSpec): void {
  const p = PLACE_PCT[spec.place];
  const cx = px(p.x);
  const cy = py(p.z) - 10; // body sits above the label/walk area
  const w = spec.wide;
  const bodyH = 34;
  const x = cx - w / 2;
  const y = cy - bodyH / 2;
  // Roof: three stepped slabs for a chunky pixel look.
  rect(ctx, x - 6, y - 8, w + 12, 10, spec.roof);
  rect(ctx, x + 2, y - 15, w - 4, 8, spec.roof);
  rect(ctx, x + 10, y - 21, w - 20, 7, spec.roof);
  // Body, base shadow, door and windows.
  rect(ctx, x, y + 2, w, bodyH, spec.wall);
  rect(ctx, x, y + bodyH - 3, w, 5, spec.wallDark);
  rect(ctx, cx - 6, y + bodyH - 10, 12, 16, '#6f4526');
  rect(ctx, cx - 4, y + bodyH - 8, 8, 14, '#8a5a33');
  windowPane(ctx, x + 8, y + 8);
  windowPane(ctx, x + w - 18, y + 8);
  // Sign + name plate.
  ctx.font = '13px "Segoe UI Emoji", system-ui, sans-serif';
  ctx.fillText(spec.icon, cx - 7, y - 24);
  plate(ctx, cx, cy + bodyH / 2 + 6, spec.label);
}

// ---- cottages (the server's house lots) ----------------------------------------

const ROOF_PALETTE = ['#c95555', '#4f8fc9', '#4fa361', '#c9884f', '#9b5de5', '#d4699e'];

function drawCottage(ctx: CanvasRenderingContext2D, house: TownHouse, index: number): void {
  const cx = px(house.x);
  const cy = py(house.z) - 6;
  const owned = house.owner_id !== null;
  const roof = owned ? ROOF_PALETTE[index % ROOF_PALETTE.length] : '#9a8f80';
  const wall = owned ? '#f2e3c8' : '#d8d0c2';
  const x = cx - 22;
  const y = cy - 12;
  // Roof steps + chimney.
  rect(ctx, x - 4, y - 6, 52, 8, roof);
  rect(ctx, x + 2, y - 12, 40, 7, roof);
  rect(ctx, x + 8, y - 17, 28, 6, roof);
  rect(ctx, x + 32, y - 24, 7, 10, '#8a7a6a');
  // Body, door, window.
  rect(ctx, x, y + 2, 44, 24, wall);
  rect(ctx, x, y + 22, 44, 4, owned ? '#dcc9a3' : '#c4bcae');
  rect(ctx, x + 28, y + 8, 10, 18, '#6f4526');
  rect(ctx, x + 30, y + 10, 6, 16, owned ? '#8a5a33' : '#7d746a');
  if (owned) {
    windowPane(ctx, x + 7, y + 9);
  } else {
    rect(ctx, x + 7, y + 9, 10, 10, '#a8a094');
    rect(ctx, x + 8, y + 10, 8, 8, '#bfb7a9');
  }
}

// ---- the frame -----------------------------------------------------------------

const PHASE_TINT: Record<string, string | null> = {
  morning: 'rgba(255, 170, 70, 0.10)',
  afternoon: null,
  evening: 'rgba(230, 90, 40, 0.16)',
  night: 'rgba(16, 22, 54, 0.42)',
};

function render(ctx: CanvasRenderingContext2D, town: TownState, t: number): void {
  nightWindows = [];
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) drawTile(ctx, GROUND[r][c], c, r, t);
  }
  drawBoat(ctx, t);
  for (const [c, r] of TREES) drawTree(ctx, c, r);
  town.houses.forEach((house, i) => drawCottage(ctx, house, i));
  // Plates go on top of every cottage so a neighbour's roof can't cover them.
  for (const house of town.houses) {
    plate(ctx, px(house.x), py(house.z) + 12, house.owner ? `⌂ ${house.owner}` : 'For sale');
  }
  for (const spec of BUILDINGS) drawBuilding(ctx, spec);
  drawFountain(ctx, t);
  plate(ctx, px(80), py(78) + 14, 'Docks');
  plate(ctx, px(18), py(38), 'Homes lane');

  const tint = PHASE_TINT[town.phase] ?? null;
  if (tint) {
    ctx.fillStyle = tint;
    ctx.fillRect(0, 0, W, H);
  }
  if (town.phase === 'night' || town.phase === 'evening') {
    // Lit windows shine back through the dusk.
    for (const win of nightWindows) {
      const glow = ctx.createRadialGradient(win.x, win.y, 1, win.x, win.y, 9);
      glow.addColorStop(0, 'rgba(255, 214, 120, 0.9)');
      glow.addColorStop(1, 'rgba(255, 214, 120, 0)');
      ctx.fillStyle = glow;
      ctx.fillRect(win.x - 9, win.y - 9, 18, 18);
    }
  }
}

// ---- residents overlay -----------------------------------------------------------

interface ActiveBubble {
  text: string;
  isEmote: boolean;
  expiresAt: number;
}

function residentPos(r: TownResident, index: number): { x: number; z: number } {
  if (typeof r.x === 'number' && typeof r.z === 'number') return { x: r.x, z: r.z };
  const base = PLACE_PCT[r.place] ?? PLACE_PCT.fountain;
  return { x: base.x + (index % 3) * 3 - 3, z: base.z + (index % 2) * 3 };
}

export function TownMapCanvas({ town, accountId }: { town: TownState; accountId: string | null }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const townRef = useRef(town);
  townRef.current = town;
  const [activeBubbles, setActiveBubbles] = useState<Record<string, ActiveBubble>>({});

  // Pop a speech bubble for this tick's say/emote events.
  useEffect(() => {
    const fresh: Record<string, ActiveBubble> = {};
    for (const e of town.events) {
      if (e.tick !== town.tick || (e.type !== 'say' && e.type !== 'emote')) continue;
      const resident = town.residents.find((r) => r.name === e.name);
      if (resident) {
        fresh[resident.account_id] = {
          text: e.text,
          isEmote: e.type === 'emote',
          expiresAt: Date.now() + 7000,
        };
      }
    }
    if (Object.keys(fresh).length > 0) {
      setActiveBubbles((prev) => ({ ...prev, ...fresh }));
    }
  }, [town.tick, town.events, town.residents]);

  // Expire old bubbles.
  useEffect(() => {
    const id = setInterval(() => {
      setActiveBubbles((prev) => {
        const now = Date.now();
        const keep = Object.entries(prev).filter(([, b]) => b.expiresAt >= now);
        return keep.length === Object.keys(prev).length ? prev : Object.fromEntries(keep);
      });
    }, 1000);
    return () => clearInterval(id);
  }, []);

  // The world clock: redraw on an interval (~8fps is plenty for water glints
  // and the fountain, and unlike rAF it still paints in a hidden/backgrounded
  // pane). Draw once immediately so the map never flashes empty.
  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d');
    if (!ctx) return;
    render(ctx, townRef.current, performance.now());
    const id = setInterval(() => render(ctx, townRef.current, performance.now()), 120);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="games-town-canvas-wrap">
      <canvas ref={canvasRef} width={W} height={H} className="games-town-canvas" />
      <div className={`games-town-time-overlay ${town.phase}`} />
      <div className="games-town-canvas-overlay">
        {town.residents.map((r, i) => {
          const pos = residentPos(r, i);
          const bubble = activeBubbles[r.account_id];
          const isUser = accountId !== null && r.account_id === accountId;
          const energy = typeof r.energy === 'number' ? r.energy : 100;
          const strength = typeof r.strength === 'number' ? r.strength : 10;
          const wealth = typeof r.wealth === 'number' ? r.wealth : 15;
          const job = typeof r.job === 'string' ? r.job : 'Resident';
          const inventory = r.inventory ?? {};
          const carried = Object.entries(inventory)
            .filter(([, qty]) => qty > 0)
            .map(([item, qty]) => `${item}:${qty}`)
            .join(', ');
          return (
            <div
              key={r.account_id}
              className={`games-town-visual-resident ${r.asleep ? 'games-town-visual-resident--asleep' : ''} ${isUser ? 'games-town-visual-resident--user' : ''}`}
              style={{ left: `${pos.x}%`, top: `${pos.z}%` }}
            >
              <div className="games-town-resident-tooltip">
                <div className="tooltip-name">{r.name}</div>
                <div className="tooltip-job">💼 {job}</div>
                <div className="tooltip-stat">⚡ Energy: {Math.round(energy)}%</div>
                <div className="tooltip-stat">💪 Strength: {Math.round(strength)}</div>
                <div className="tooltip-stat">🪙 Coins: {Math.round(wealth)}</div>
                <div className="tooltip-stat">
                  🏡 {r.house_id ? `Home: ${r.house_id}` : 'No house yet'}
                </div>
                <div className="tooltip-inventory">🎒 {carried || 'Empty'}</div>
              </div>
              {bubble && (
                <div
                  className={`games-town-bubble ${bubble.isEmote ? 'games-town-bubble--emote' : ''}`}
                >
                  {bubble.text}
                </div>
              )}
              {isUser && <div className="games-town-visual-user-arrow">▼</div>}
              <div className="games-town-visual-avatar-wrapper">
                <span className="games-town-visual-avatar">{r.avatar}</span>
                {r.asleep && <span className="games-town-visual-sleep-icon">💤</span>}
              </div>
              <div className="games-town-visual-name" title={r.name}>
                {r.name}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
