/**
 * How each player looks: their agent and their weapon skins, per side.
 *
 * The bundle is the server's (`backend/modules/hassault/cosmetics.py`): built
 * from the account's own rows on the game server, and sent in the welcome, in
 * `joined` and as a `cosmetics` event. Never in a snapshot. Both sides travel
 * together; the half drawn is the one for the team the player is on now.
 *
 * Read leniently, like everything else on the wire: an older server sends no
 * bundle, and a player without one is drawn exactly as before (the team's
 * default agent, bare metal).
 *
 * Free of React and three.
 */
import type { WeaponSkin } from './viewmodel';

export type Side = 't' | 'ct';

export interface AgentLook {
  id: string;
  /** `#rrggbb`: the wash over the operator's uniform. */
  armor: string;
  /** `#rrggbb`: trim, nameplate stripe and bare-metal accents. */
  trim: string;
}

export interface SkinEntry {
  skinId: string;
  name: string;
  baseColor: string;
  accentColor: string;
  patternType: string;
  floatValue: number;
  seed: number;
  statTrak: number | null;
}

export interface CosmeticsSide {
  agent: AgentLook;
  /** By weapon id: the gun's own id, not the model it borrows. */
  weapons: Record<string, SkinEntry>;
}

export type Cosmetics = Record<Side, CosmeticsSide>;

/** One served agent, `GET /api/hassault/agents`. */
export interface AgentSpec {
  id: string;
  name: string;
  faction: 'arc' | 'halon' | 'neutral';
  sides: Side[];
  bodyColor: string;
  armorColor: string;
  trimColor: string;
  visorColor: string;
  skinToneColor: string;
  bootColor: string;
  roughness: number;
  metalness: number;
}

export const AGENTS_KEY = 'hassault.agents';
export const SIDES: readonly Side[] = ['t', 'ct'];

/** `weapons.side_of`: ARC (team 0) plays T, HALON (team 1) CT. */
export function sideOf(team: number | undefined): Side {
  return team === 1 ? 'ct' : 't';
}

const HEX = /^#[0-9a-f]{6}$/i;

function readSide(raw: unknown): CosmeticsSide | null {
  if (!raw || typeof raw !== 'object') return null;
  const half = raw as Record<string, unknown>;
  const a = (half.agent ?? {}) as Record<string, unknown>;
  if (typeof a.id !== 'string' || typeof a.armor !== 'string' || !HEX.test(a.armor)) return null;
  const weapons: Record<string, SkinEntry> = {};
  const listed = half.weapons;
  if (listed && typeof listed === 'object') {
    for (const [id, value] of Object.entries(listed as Record<string, unknown>)) {
      if (!value || typeof value !== 'object') continue;
      const e = value as Record<string, unknown>;
      if (typeof e.baseColor !== 'string' || typeof e.accentColor !== 'string') continue;
      weapons[id] = {
        skinId: String(e.skinId ?? ''),
        name: String(e.name ?? ''),
        baseColor: e.baseColor,
        accentColor: e.accentColor,
        patternType: String(e.patternType ?? 'solid'),
        floatValue: typeof e.floatValue === 'number' ? e.floatValue : 0,
        seed: typeof e.seed === 'number' ? e.seed : 0,
        statTrak: typeof e.statTrak === 'number' ? e.statTrak : null,
      };
    }
  }
  return {
    agent: { id: a.id, armor: a.armor, trim: typeof a.trim === 'string' ? a.trim : a.armor },
    weapons,
  };
}

/** A bundle off the wire, or `null` when it is missing or unreadable. */
export function parseCosmetics(raw: unknown): Cosmetics | null {
  if (!raw || typeof raw !== 'object') return null;
  const t = readSide((raw as Record<string, unknown>).t);
  const ct = readSide((raw as Record<string, unknown>).ct);
  return t && ct ? { t, ct } : null;
}

/** The welcome's `{playerId: bundle}`, keeping only readable bundles. */
export function parseCosmeticsMap(raw: unknown): Record<string, Cosmetics> {
  const out: Record<string, Cosmetics> = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const [id, value] of Object.entries(raw as Record<string, unknown>)) {
    const parsed = parseCosmetics(value);
    if (parsed) out[id] = parsed;
  }
  return out;
}

/** The agent a player is wearing on their current team, if the server said. */
export function agentFor(
  look: Cosmetics | null | undefined,
  team: number | undefined,
): AgentLook | null {
  return look?.[sideOf(team)].agent ?? null;
}

/** The skin on `weaponId` for a player's current team, as the drawers take it. */
export function skinFor(
  look: Cosmetics | null | undefined,
  team: number | undefined,
  weaponId: string | undefined,
): WeaponSkin | null {
  if (!look || !weaponId) return null;
  const entry = look[sideOf(team)].weapons[weaponId];
  if (!entry) return null;
  return {
    id: entry.skinId,
    name: entry.name,
    baseColor: entry.baseColor,
    accentColor: entry.accentColor,
    patternType: entry.patternType,
    floatValue: entry.floatValue,
  };
}

/** Every skin a bundle puts on one side, keyed by weapon id: the first-person
 * view model's table, which used to be built from the whole inventory. */
export function skinsForSide(
  look: Cosmetics | null | undefined,
  side: Side,
): Record<string, WeaponSkin> {
  const out: Record<string, WeaponSkin> = {};
  if (!look) return out;
  for (const id of Object.keys(look[side].weapons)) {
    const skin = skinFor(look, side === 'ct' ? 1 : 0, id);
    if (skin) out[id] = skin;
  }
  return out;
}

/** The stored `hassault.agents` setting, `{t: id, ct: id}`. Unknown is `{}`. */
export function parseAgents(raw: unknown): Partial<Record<Side, string>> {
  if (typeof raw !== 'string' || !raw.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Partial<Record<Side, string>> = {};
    for (const side of SIDES) {
      const v = (parsed as Record<string, unknown>)[side];
      if (typeof v === 'string') out[side] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function serializeAgents(choice: Partial<Record<Side, string>>): string {
  return JSON.stringify({ t: choice.t, ct: choice.ct });
}

/** A stable key for "has this player's look changed", for the avatar cache. */
export function lookKey(agent: AgentLook | null): string {
  return agent ? `${agent.id}|${agent.armor}|${agent.trim}` : '';
}
