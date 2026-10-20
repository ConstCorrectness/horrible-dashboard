/**
 * The loadout a player carries: one primary, one secondary, the knife, per side.
 *
 * Stored under `hassault.loadout` as `{t: {primary, secondary}, ct: {...}}`
 * weapon ids (an older flat `{primary, secondary}` means both sides). The
 * setting follows the account, so the game server reads it at join
 * (`weapons.loadouts_for`); a pick made mid-match is also sent, and either way
 * it applies at the next spawn, for the side the player is then on. The
 * training range applies it here, through the same fallback rule, because
 * offline there is no server to ask.
 *
 * Free of React and three.
 */
import type { WeaponSpec } from './api';
import type { Side } from './cosmetics';
import type { LoadoutSlots } from './net';

export const LOADOUT_KEY = 'hassault.loadout';

export type LoadoutChoice = { primary?: string; secondary?: string };
export type Loadouts = Partial<Record<Side, LoadoutChoice>>;

/** `weapons.DEFAULT_LOADOUT`. */
export const DEFAULT_LOADOUT = { primary: 'assault', secondary: 'pistol', melee: 'knife' } as const;

function readChoice(raw: unknown): LoadoutChoice {
  const out: LoadoutChoice = {};
  if (!raw || typeof raw !== 'object') return out;
  const p = (raw as Record<string, unknown>).primary;
  const s = (raw as Record<string, unknown>).secondary;
  if (typeof p === 'string') out.primary = p;
  if (typeof s === 'string') out.secondary = s;
  return out;
}

function readObject(raw: unknown): Record<string, unknown> | null {
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * The stored setting, per side, or `null` for "never chosen" (the server
 * default). The flat older shape is read as the same choice on both sides.
 */
export function parseLoadouts(raw: unknown): Loadouts | null {
  const parsed = readObject(raw);
  if (!parsed) return null;
  const perSide = ['t', 'ct'].some((s) => parsed[s] && typeof parsed[s] === 'object');
  if (!perSide) {
    const flat = readChoice(parsed);
    return { t: flat, ct: { ...flat } };
  }
  return { t: readChoice(parsed.t), ct: readChoice(parsed.ct) };
}

export function serializeLoadouts(choice: Loadouts): string {
  const side = (c?: LoadoutChoice) => ({ primary: c?.primary, secondary: c?.secondary });
  return JSON.stringify({ t: side(choice.t), ct: side(choice.ct) });
}

/** The flat older shape, kept for what still reads one choice. */
export function parseLoadout(raw: unknown): LoadoutChoice | null {
  const parsed = readObject(raw);
  return parsed ? readChoice(parsed) : null;
}

/** Every weapon carried in `slot`, in table order, optionally only those
 * `side` may carry: the picker's list. */
export function weaponsInSlot(
  weapons: WeaponSpec[],
  slot: 'primary' | 'secondary',
  side?: Side,
): WeaponSpec[] {
  return weapons.filter(
    (w) => w.slot === slot && (!side || !w.side || w.side === 'any' || w.side === side),
  );
}

/**
 * `choice` as indexes into `weapons`, slot by slot, falling back to the default
 * for an unknown id, one named for the wrong slot, or (given a `side`) one the
 * other side carries. A port of `weapons.loadout_for` for the range; online the
 * server's answer wins.
 */
export function loadoutSlots(
  weapons: WeaponSpec[],
  choice: LoadoutChoice | null | undefined,
  side?: Side,
): LoadoutSlots {
  const pick = (slot: 'primary' | 'secondary' | 'melee', wanted?: string): number => {
    const byId = (id: string) => weapons.findIndex((w) => w.id === id);
    const at = wanted ? byId(wanted) : -1;
    const spec = at >= 0 ? weapons[at] : undefined;
    const sideOk = !side || !spec?.side || spec.side === 'any' || spec.side === side;
    if (spec && (spec.slot ?? '') === slot && sideOk) return at;
    return Math.max(0, byId(DEFAULT_LOADOUT[slot]));
  };
  return {
    primary: pick('primary', choice?.primary),
    secondary: pick('secondary', choice?.secondary),
    melee: pick('melee'),
  };
}
