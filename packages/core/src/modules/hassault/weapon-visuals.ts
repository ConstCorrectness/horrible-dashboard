/**
 * Which model a weapon is drawn as.
 *
 * The roster outgrew the model set: twelve weapons, five view models, five
 * props, five silhouettes. Each served weapon names the one it borrows
 * (`WeaponSpec.model`, always concrete on the wire), and every visual table in
 * this client is keyed by those five. This module is the one place a weapon id
 * or a wire index becomes a model id, so a sixth table never grows its own
 * fallback rule.
 *
 * A registry rather than an argument threaded through every drawer, because the
 * drawers (the remote avatar, the killfeed badge) are far from the loadout fetch
 * and only ever hold an index or an id. Free of React and three.
 */
import type { WeaponSpec } from './api';

/** The models, in the slot order the remote-avatar props are built in. */
export const BASE_MODELS = ['knife', 'pistol', 'assault', 'shotgun', 'sniper'] as const;
export type BaseModel = (typeof BASE_MODELS)[number];

let table: WeaponSpec[] = [];
let byId = new Map<string, WeaponSpec>();

/** Record the served loadout. Called once it arrives. */
export function setWeaponTable(specs: WeaponSpec[]): void {
  table = specs;
  byId = new Map(specs.map((s) => [s.id, s]));
}

function asBase(id: string): BaseModel | null {
  return (BASE_MODELS as readonly string[]).includes(id) ? (id as BaseModel) : null;
}

/**
 * The model a weapon is drawn as, from its spec, id or wire index. An id the
 * table has never heard of is returned as given when it is already a model, and
 * falls back to the rifle otherwise, so a newer server's weapon is drawn as a
 * gun rather than as nothing.
 */
export function visualId(weapon: WeaponSpec | string | number | null | undefined): BaseModel {
  const spec =
    typeof weapon === 'number'
      ? table[weapon]
      : typeof weapon === 'string'
        ? byId.get(weapon)
        : (weapon ?? undefined);
  const named = spec?.model ?? spec?.id ?? (typeof weapon === 'string' ? weapon : '');
  return asBase(named) ?? 'assault';
}

/** The base-model slot (0..4) a weapon's prop is drawn from. */
export function visualSlot(index: number): number {
  return BASE_MODELS.indexOf(visualId(index));
}
