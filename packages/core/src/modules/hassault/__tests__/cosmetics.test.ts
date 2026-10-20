/**
 * How players look, against `cosmetics-vectors.json` (generated from
 * `backend/modules/hassault/cosmetics.py`, the reference).
 *
 * The palette is the one piece of arithmetic all three implementations share:
 * a skin drawn differently here from native, or from the server's own idea of
 * it, is the same skin looking like two different ones. Compared within a
 * colour step, because this port rounds each channel to a byte.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { WeaponSpec } from '../api';
import {
  agentFor,
  lookKey,
  parseAgents,
  parseCosmetics,
  parseCosmeticsMap,
  serializeAgents,
  sideOf,
  skinFor,
  skinsForSide,
} from '../cosmetics';
import { loadoutSlots, parseLoadouts, serializeLoadouts } from '../loadout';
import { MatchSession } from '../session';
import { paletteFor, type WeaponSkin } from '../viewmodel';

interface Vectors {
  palettes: { name: string; skin: WeaponSkin | null; palette: Record<string, number[]> }[];
  agents: { id: string; sides: string[] }[];
  wire: {
    playerId: string;
    welcome: { cosmetics: Record<string, unknown> };
    joined: { player: { id: string; team: number }; cosmetics: unknown };
    cosmetics: { playerId: string; cosmetics: unknown };
  };
  weapons: { id: string; slot: WeaponSpec['slot']; side: WeaponSpec['side'] }[];
  loadouts: { name: string; choice: unknown; sides: Record<'t' | 'ct', Record<string, string>> }[];
}

const vectors = JSON.parse(
  readFileSync(join(__dirname, 'cosmetics-vectors.json'), 'utf8'),
) as Vectors;

function channels(hex: number): number[] {
  return [(hex >> 16) & 0xff, (hex >> 8) & 0xff, hex & 0xff].map((c) => c / 255);
}

describe('the skin palette, against the Python reference', () => {
  it.each(vectors.palettes.map((c) => [c.name, c] as const))('%s', (_, c) => {
    const got = paletteFor(c.skin);
    for (const part of ['body', 'dark', 'grip', 'accent'] as const) {
      const want = c.palette[part];
      const have = channels(got[part]);
      for (let i = 0; i < 3; i += 1) {
        expect(Math.abs(have[i] - want[i]), `${part}[${i}]`).toBeLessThanOrEqual(1.5 / 255);
      }
    }
  });
});

describe('the bundle wire', () => {
  const { wire } = vectors;

  it('reads the welcome, joined and cosmetics shapes alike', () => {
    const map = parseCosmeticsMap(wire.welcome.cosmetics);
    const joined = parseCosmetics(wire.joined.cosmetics);
    const event = parseCosmetics(wire.cosmetics.cosmetics);
    expect(map[wire.playerId]).toEqual(joined);
    expect(event).toEqual(joined);
  });

  it('draws the half for the team a player is on', () => {
    const look = parseCosmetics(wire.joined.cosmetics);
    expect(sideOf(0)).toBe('t');
    expect(sideOf(1)).toBe('ct');
    expect(agentFor(look, 0)?.id).toBe('scavenger_prime');
    expect(agentFor(look, 1)?.id).not.toBe('scavenger_prime');
    expect(skinFor(look, 0, 'pistol')).toBeNull();
    const pistol = skinFor(look, 1, 'pistol');
    expect(pistol?.floatValue).toBeCloseTo(0.62);
    expect(Object.keys(skinsForSide(look, 'ct')).sort()).toEqual(['assault', 'pistol']);
    expect(lookKey(agentFor(look, 0))).not.toBe(lookKey(agentFor(look, 1)));
  });

  it('treats a missing or broken bundle as the team default', () => {
    expect(parseCosmetics(undefined)).toBeNull();
    expect(parseCosmetics({ t: { agent: { id: 'x', armor: 'red' } } })).toBeNull();
    expect(agentFor(null, 0)).toBeNull();
    expect(skinFor(undefined, 0, 'assault')).toBeNull();
  });

  it('keeps each player’s look in the session, from every event that carries one', () => {
    const session = new MatchSession();
    const receive = (event: string, data: unknown) =>
      (session as unknown as { receive(m: unknown): void }).receive({ event, data });
    receive('welcome', { room: 'r', map: 'm', playerId: 'me', players: [], ...wire.welcome });
    expect(Object.keys(session.state.cosmetics)).toEqual([wire.playerId]);
    receive('joined', { player: { id: 'p2' }, cosmetics: wire.joined.cosmetics });
    expect(Object.keys(session.state.cosmetics).sort()).toEqual(['p1', 'p2']);
    receive('cosmetics', { playerId: 'p2', cosmetics: wire.cosmetics.cosmetics });
    receive('left', { playerId: 'p1' });
    expect(Object.keys(session.state.cosmetics)).toEqual(['p2']);
  });
});

describe('loadouts per side, against `weapons.loadouts_for`', () => {
  const table = vectors.weapons.map(
    (w) => ({ id: w.id, name: w.id, slot: w.slot, side: w.side }) as WeaponSpec,
  );

  it.each(vectors.loadouts.map((c) => [c.name, c] as const))('%s', (_, c) => {
    const raw =
      typeof c.choice === 'string' || c.choice === null ? c.choice : JSON.stringify(c.choice);
    const parsed = parseLoadouts(raw);
    for (const side of ['t', 'ct'] as const) {
      const slots = loadoutSlots(table, parsed?.[side], side);
      expect({
        primary: table[slots.primary].id,
        secondary: table[slots.secondary].id,
        melee: table[slots.melee].id,
      }).toEqual(c.sides[side]);
    }
  });

  it('round-trips through the stored setting', () => {
    const choice = { t: { primary: 'raider' }, ct: { primary: 'lancer', secondary: 'pistol' } };
    expect(parseLoadouts(serializeLoadouts(choice))).toEqual(choice);
    expect(parseAgents(serializeAgents({ t: 'a', ct: 'b' }))).toEqual({ t: 'a', ct: 'b' });
    expect(parseAgents('nope')).toEqual({});
  });
});
