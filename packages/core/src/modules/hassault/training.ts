/**
 * The training range: a gun that works when there is no server to ask.
 *
 * Training is where the movement is meant to be learnable — the chained jump,
 * the shoot-jump — and for a long time it had no weapons at all, because
 * `ShotController` needs a `SelfState` and offline there is nobody to send one.
 * So the range plays that part locally: it owns ammo, reloads, a set of static
 * targets, and the hitscan against them.
 *
 * **Nothing here is authoritative and none of it goes on a wire.** In a match
 * the server owns every one of these decisions and this file is not consulted.
 * That is also why it can be this simple: no rewind, no lag compensation, no
 * validation — there is exactly one client and it is the only thing that exists.
 *
 * Targets are `PlayerRow`s on purpose. The avatar pool already draws those, so a
 * dummy is a body like any other rather than a second rendering path that would
 * drift from the one people actually shoot at in matches.
 *
 * Free of three and of React, like the rest of the game's logic files.
 */
import type { WeaponSpec } from './api';
import type { PlayerRow, SelfState } from './net';
import { currentHitbox } from './hitbox';
import { spawnAt } from './player';
import {
  aimVector,
  damageAt,
  eyePosition,
  rayHitsBody,
  raycastWorld,
  spreadVector,
  type Vec,
} from './trace';
import type { World } from './world';

/** How many dummies the range puts out, at most. */
export const MAX_TARGETS = 6;

/** Seconds a downed target stays down before it stands back up. */
export const TARGET_RESPAWN = 3.0;

/** A target's health. Two body shots from the rifle, one from the sniper. */
export const TARGET_HP = 100;

/**
 * Falloff start per weapon id.
 *
 * The wire does not carry `falloffStart` — the browser never needed it, because
 * in a match the server does this arithmetic. Rather than widen the API for a
 * number only the range reads, it is approximated from what *is* served: full
 * damage out to a third of the weapon's range. The sniper and knife are flat in
 * the real table and stay flat here because their falloff begins at their range.
 *
 * This is the one number in this file that is a deliberate approximation of the
 * server's, and it is only ever used to colour a damage figure on a dummy.
 */
function falloffStart(weapon: WeaponSpec): number {
  if (weapon.id === 'sniper' || weapon.id === 'knife') return weapon.range;
  return weapon.range / 3;
}

export interface TargetHit {
  id: string;
  damage: number;
  head: boolean;
  killed: boolean;
}

export interface RangeShot {
  origin: Vec;
  /** One endpoint per pellet — a wall, a body, or the end of the shot's range. */
  ends: Vec[];
  hits: TargetHit[];
}

interface Target {
  id: string;
  name: string;
  x: number;
  y: number;
  z: number;
  hp: number;
  alive: boolean;
  /** Seconds until it stands back up; only meaningful while down. */
  downFor: number;
}

/**
 * The local stand-in for everything the match server would otherwise own.
 *
 * Deliberately shaped so the panel's online and offline paths differ in *where*
 * the state comes from and nowhere else: `selfState()` returns the same
 * `SelfState` the socket would have delivered, so `ShotController` and the HUD
 * cannot tell the difference and neither needs an offline branch.
 */
export class TrainingRange {
  private targets: Target[] = [];
  private weapons: WeaponSpec[] = [];
  private slot = 0;
  private ammo: number[] = [];
  private reserve: number[] = [];
  private reloadIn = 0;
  private pendingHits: TargetHit[] = [];

  setWeapons(specs: WeaponSpec[], slot: number): void {
    this.weapons = specs;
    this.slot = Math.max(0, Math.min(specs.length - 1, slot));
    this.ammo = specs.map((w) => w.mag);
    this.reserve = specs.map((w) => w.reserve);
    this.reloadIn = 0;
  }

  /**
   * Put dummies out on the map's own spawn points.
   *
   * Spawn points rather than anywhere clever: a bundled map guarantees every one
   * of them is standable (that is what `test_hassault_bundled` checks), so this
   * cannot put a target inside a wall on a map it has never seen. Nearest first,
   * and never the one the player is standing on.
   */
  place(world: World, fromX: number, fromY: number): void {
    const points = world
      .spawns()
      .map((entity) => {
        const placed = spawnAt(world, entity);
        return {
          x: placed.x,
          y: placed.y,
          z: placed.z,
          d: Math.hypot(placed.x - fromX, placed.y - fromY),
        };
      })
      // Two cubes is a body's width; anything nearer is the point we spawned on.
      .filter((p) => p.d > 2.5)
      .sort((a, b) => a.d - b.d)
      .slice(0, MAX_TARGETS);

    this.targets = points.map((p, i) => ({
      id: `dummy${i}`,
      name: `Dummy ${i + 1}`,
      x: p.x,
      y: p.y,
      z: p.z,
      hp: TARGET_HP,
      alive: true,
      downFor: 0,
    }));
  }

  /** Whether the range has anything to shoot at. */
  get populated(): boolean {
    return this.targets.length > 0;
  }

  select(slot: number): void {
    if (slot >= 0 && slot < this.weapons.length && slot !== this.slot) {
      this.slot = slot;
      // A switch cancels a reload, exactly as it does server-side: otherwise the
      // timer keeps running on a weapon you are no longer holding and fills it
      // while you are somewhere else.
      this.reloadIn = 0;
    }
  }

  requestReload(): void {
    const weapon = this.weapons[this.slot];
    if (!weapon || weapon.mag <= 0 || this.reloadIn > 0) return;
    if (this.ammo[this.slot] >= weapon.mag) return;
    if (this.reserve[this.slot] === 0) return;
    this.reloadIn = weapon.reloadTime;
  }

  /** Advance reload timers and stand downed targets back up. */
  update(dt: number): void {
    if (this.reloadIn > 0) {
      this.reloadIn -= dt;
      if (this.reloadIn <= 0) {
        this.reloadIn = 0;
        this.finishReload();
      }
    }
    for (const target of this.targets) {
      if (target.alive) continue;
      target.downFor -= dt;
      if (target.downFor <= 0) {
        target.alive = true;
        target.hp = TARGET_HP;
      }
    }
  }

  private finishReload(): void {
    const weapon = this.weapons[this.slot];
    if (!weapon) return;
    const want = weapon.mag - this.ammo[this.slot];
    const have = this.reserve[this.slot];
    // `-1` is unlimited, and stays unlimited: decrementing it would turn the
    // sidearm's bottomless reserve into 4 billion rounds on the first reload.
    const taken = have < 0 ? want : Math.min(want, have);
    this.ammo[this.slot] += taken;
    if (have > 0) this.reserve[this.slot] = have - taken;
  }

  /**
   * Resolve one trigger pull against the world and the dummies.
   *
   * The same shape as the server's `resolve_shot` and for the same reason: the
   * caller wants endpoints to draw tracers to whether or not anything was hit.
   */
  fire(
    world: World,
    x: number,
    y: number,
    z: number,
    eye: number,
    yaw: number,
    pitch: number,
    scoped: number,
    rand: () => number = Math.random,
  ): RangeShot | null {
    const weapon = this.weapons[this.slot];
    if (!weapon) return null;
    if (weapon.mag > 0) {
      if (this.ammo[this.slot] <= 0) return null;
      this.ammo[this.slot] -= 1;
    }

    const origin = eyePosition(x, y, z, eye);
    const direction = aimVector(yaw, pitch);
    // The scope's whole mechanical effect, mirroring `effective_spread`
    // server-side: which cone this pull uses.
    const cone = scoped > 0 ? weapon.spread : weapon.hipfireSpread;
    const ends: Vec[] = [];
    const hits: TargetHit[] = [];

    for (let pellet = 0; pellet < Math.max(1, weapon.pellets); pellet++) {
      const [pdx, pdy, pdz] = spreadVector(direction, cone, rand);
      const wall = raycastWorld(world, origin, [pdx, pdy, pdz], weapon.range);

      let best: { distance: number; target: Target } | null = null;
      for (const target of this.targets) {
        if (!target.alive) continue;
        const distance = rayHitsBody(origin, [pdx, pdy, pdz], [target.x, target.y, target.z]);
        // A body behind a wall is not a target; the wall is nearer, and this
        // comparison is the whole of cover.
        if (distance === null || distance >= wall) continue;
        if (best === null || distance < best.distance) best = { distance, target };
      }

      if (best === null) {
        ends.push([origin[0] + pdx * wall, origin[1] + pdy * wall, origin[2] + pdz * wall]);
        continue;
      }

      const { distance, target } = best;
      const point: Vec = [
        origin[0] + pdx * distance,
        origin[1] + pdy * distance,
        origin[2] + pdz * distance,
      ];
      // Relative to the top of the body, so the head is where the head is.
      // The live spec, so a head band tuned in the lab moves where a headshot
      // starts in Train too — practising against a different body than the one
      // the match server resolves against is worse than not practising.
      const spec = currentHitbox();
      const head = point[2] >= target.z + (spec.standingHeight - spec.headBand);
      const amount =
        damageAt(weapon, distance, falloffStart(weapon)) * (head ? weapon.headMultiplier : 1);
      target.hp -= amount;
      const killed = target.hp <= 0;
      if (killed) {
        target.alive = false;
        target.downFor = TARGET_RESPAWN;
      }
      hits.push({ id: target.id, damage: amount, head, killed });
      ends.push(point);
    }

    if (hits.length > 0) this.pendingHits.push(...hits);
    return { origin, ends, hits };
  }

  /**
   * What a snapshot would have told us about ourselves.
   *
   * Hitmarkers drain on read, exactly as the server drains them when it builds a
   * private view — so each one is shown once whichever half of the game produced
   * it, and the HUD needs no offline branch to avoid a marker that never clears.
   */
  selfState(): SelfState {
    const weapon = this.weapons[this.slot];
    const hits = this.pendingHits.map((h) => ({
      victim: h.id,
      damage: h.damage,
      head: h.head,
      killed: h.killed,
    }));
    this.pendingHits = [];
    return {
      // Nothing shoots back on the range, so this is always true — the dummies
      // are targets, not opponents, and a training death would only interrupt.
      hp: 100,
      alive: true,
      weapon: this.slot,
      ammo: this.ammo[this.slot] ?? 0,
      reserve: this.reserve[this.slot] ?? 0,
      reloading: this.reloadIn > 0,
      reloadIn: this.reloadIn,
      respawnIn: 0,
      protected: false,
      kills: 0,
      deaths: 0,
      mag: weapon?.mag ?? 0,
      hits,
    };
  }

  /** The dummies as bodies the avatar pool can draw. */
  rows(): PlayerRow[] {
    return this.targets.map((target) => ({
      id: target.id,
      name: target.name,
      // Team 1 throughout: they render in the opposing colour, which is the
      // colour a thing you are meant to shoot at should be.
      team: 1,
      x: target.x,
      y: target.y,
      z: target.z,
      yaw: 0,
      pitch: 0,
      ground: true,
      stale: false,
      rtt: 0,
      hp: Math.max(0, target.hp),
      alive: target.alive,
      weapon: 0,
      kills: 0,
      deaths: 0,
      bot: true,
      crouch: 0,
    }));
  }

  reset(): void {
    this.targets = [];
    this.pendingHits = [];
    this.reloadIn = 0;
    this.ammo = this.weapons.map((w) => w.mag);
    this.reserve = this.weapons.map((w) => w.reserve);
  }
}
