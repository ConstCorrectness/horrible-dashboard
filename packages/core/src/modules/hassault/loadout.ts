/**
 * The loadout a player carries: one primary, one secondary, the knife.
 *
 * Stored as `{primary, secondary}` weapon ids under `hassault.loadout`, sent to
 * the server (which validates it slot by slot, `weapons.loadout_for`) and
 * applied at the next spawn. The training range applies it here, through the
 * same fallback rule, because offline there is no server to ask.
 *
 * Free of React and three.
 */
import type { WeaponSpec } from './api';
import type { LoadoutSlots } from './net';

export const LOADOUT_KEY = 'hassault.loadout';

export type LoadoutChoice = { primary?: string; secondary?: string };

/** `weapons.DEFAULT_LOADOUT`. */
export const DEFAULT_LOADOUT = { primary: 'assault', secondary: 'pistol', melee: 'knife' } as const;

/** The stored setting, or `null` for "never chosen" (the server default). */
export function parseLoadout(raw: unknown): LoadoutChoice | null {
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const out: LoadoutChoice = {};
    const p = (parsed as Record<string, unknown>).primary;
    const s = (parsed as Record<string, unknown>).secondary;
    if (typeof p === 'string') out.primary = p;
    if (typeof s === 'string') out.secondary = s;
    return out;
  } catch {
    return null;
  }
}

export function serializeLoadout(choice: LoadoutChoice): string {
  return JSON.stringify({ primary: choice.primary, secondary: choice.secondary });
}

/** Every weapon carried in `slot`, in table order: the picker's list. */
export function weaponsInSlot(weapons: WeaponSpec[], slot: 'primary' | 'secondary'): WeaponSpec[] {
  return weapons.filter((w) => w.slot === slot);
}

/**
 * `choice` as indexes into `weapons`, slot by slot, falling back to the default
 * for an unknown id or one named for the wrong slot. A port of
 * `weapons.loadout_for` for the range; online the server's answer wins.
 */
export function loadoutSlots(weapons: WeaponSpec[], choice: LoadoutChoice | null): LoadoutSlots {
  const pick = (slot: 'primary' | 'secondary' | 'melee', wanted?: string): number => {
    const byId = (id: string) => weapons.findIndex((w) => w.id === id);
    const at = wanted ? byId(wanted) : -1;
    if (at >= 0 && (weapons[at].slot ?? '') === slot) return at;
    return Math.max(0, byId(DEFAULT_LOADOUT[slot]));
  };
  return {
    primary: pick('primary', choice?.primary),
    secondary: pick('secondary', choice?.secondary),
    melee: pick('melee'),
  };
}
