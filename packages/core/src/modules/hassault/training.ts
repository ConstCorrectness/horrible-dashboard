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
import type { ItemReach, ItemSpec, WeaponSpec } from './api';
import type { ItemRow, LoadoutSlots, PickedItem, PlayerRow, SelfState } from './net';
import { visualId } from './weapon-visuals';
import { currentHitbox, heightAt } from './hitbox';
import { spawnAt } from './player';
import {
  DrillMover,
  summarise,
  type DrillEvent,
  type DrillSpec,
  type DrillStats,
} from './training-drill';
import {
  aimVector,
  damageAt,
  eyePosition,
  rayHitsBody,
  FACE_NONE,
  raycastWorldFace,
  applySpray,
  shotCone,
  spreadVector,
  sprayOffset,
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
 * Falloff start: the served `falloffStart`.
 *
 * The range used to guess it as a third of the weapon's range, which matched no
 * weapon in the server's table (the rifle falls off from 80, not range/3), so a
 * dummy took different damage than the same shot in a match. The guess survives
 * only for a backend older than the field.
 */
export function falloffStart(weapon: WeaponSpec): number {
  if (typeof weapon.falloffStart === 'number') return weapon.falloffStart;
  if (weapon.id === 'sniper' || weapon.id === 'knife') return weapon.range;
  return weapon.range / 3;
}

export interface TargetHit {
  id: string;
  damage: number;
  head: boolean;
  killed: boolean;
  wallbang?: boolean;
}

export interface RangeShot {
  origin: Vec;
  /** One endpoint per pellet — a wall, a body, or the end of the shot's range. */
  ends: Vec[];
  /**
   * Which surface each pellet stopped against, parallel to `ends`.
   *
   * The same shape the server puts on the wire (`ShotFx.faces`), so the panel
   * draws bullet marks through one code path in Train and in a match.
   * `FACE_NONE` for a pellet that hit a target or ran out of range.
   */
  faces: number[];
  hits: TargetHit[];
  wallbang?: boolean;
  shatteredWindows?: string[];
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
  /** In a drill, the replayed body: its position, facing and crouch. */
  mover?: DrillMover;
}

/** How the shooter was moving when they fired: `ShotContext` minus the parts
 * the range already knows (the scope, the time since its last shot). */
export interface RangeMotion {
  speed: number;
  airborne: boolean;
  crouch: number;
}

const STANDING: RangeMotion = { speed: 0, airborne: false, crouch: 0 };

/** Seconds of countdown before a drill's clock starts. */
export const DRILL_COUNTDOWN = 3;

/** Where a drill is: counting down, running, or finished with a score. */
export interface DrillStatus {
  id: string;
  name: string;
  phase: 'countdown' | 'running' | 'done';
  /** Seconds left in the current phase; 0 once done. */
  remaining: number;
  /** Set once `phase` is `done`. */
  stats: DrillStats | null;
}

/** A target can be shot: standing, and shown by its schedule. */
function hittable(target: Target): boolean {
  return target.alive && (target.mover?.visible ?? true);
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
  /**
   * The map's items, as the server resolved them, and when each is back.
   *
   * The range resolves **only the ammunition half** of a pickup. Health and
   * armour mean nothing where nothing shoots back (`hp` here is a constant 100),
   * and faking them would teach a resource cycle the range does not have. The
   * items still *draw*, because knowing where they are and when they are back is
   * most of what there is to learn from them.
   */
  private items: { row: ItemRow; spec: ItemSpec; backAt: number }[] = [];
  /** Every item on the map, takeable or not — the range draws them all. */
  private itemRows: ItemRow[] = [];
  private reach: ItemReach | null = null;
  private clock = 0;
  private pendingPickups: PickedItem[] = [];

  /**
   * The drill being run, if any. Its targets replace the free range's dummies
   * and move by replaying the served schedule (`training-drill.ts`); its event
   * log is what the results card is scored from.
   */
  private drill: DrillSpec | null = null;
  private drillWorld: World | null = null;
  private drillPhase: DrillStatus['phase'] = 'countdown';
  private countdown = 0;
  private tickAcc = 0;
  private ticksDone = 0;
  private events: DrillEvent[] = [];
  private stats: DrillStats | null = null;
  /** Which targets were hittable after the last tick, to log appear/hide. */
  private shown = new Set<string>();

  /**
   * Start `spec` on `world`. The targets stand still through the countdown and
   * start their schedules together when it ends.
   */
  startDrill(spec: DrillSpec, world: World): void {
    const tickDt = 1 / spec.tickRate;
    this.drill = spec;
    this.drillWorld = world;
    this.drillPhase = 'countdown';
    this.countdown = DRILL_COUNTDOWN;
    this.tickAcc = 0;
    this.ticksDone = 0;
    this.events = [];
    this.stats = null;
    this.shown.clear();
    this.targets = spec.targets.map((t, i) => {
      const mover = new DrillMover(t, tickDt);
      return {
        id: t.id,
        name: `Target ${i + 1}`,
        x: mover.state.x,
        y: mover.state.y,
        z: mover.state.z,
        hp: spec.hp,
        alive: true,
        downFor: 0,
        mover,
      };
    });
  }

  /** Leave the drill, back to the free range (with no dummies until `place`). */
  stopDrill(): void {
    this.drill = null;
    this.drillWorld = null;
    this.targets = [];
    this.events = [];
    this.stats = null;
    this.shown.clear();
  }

  /** The drill's state for the HUD and the results card, or `null` if none. */
  drillStatus(): DrillStatus | null {
    const drill = this.drill;
    if (!drill) return null;
    const tickDt = 1 / drill.tickRate;
    const remaining =
      this.drillPhase === 'countdown'
        ? this.countdown
        : this.drillPhase === 'running'
          ? Math.max(0, (drill.ticks - this.ticksDone) * tickDt)
          : 0;
    return { id: drill.id, name: drill.name, phase: this.drillPhase, remaining, stats: this.stats };
  }

  /** Seconds since the drill's clock started: the time every event is stamped with. */
  private get drillClock(): number {
    return this.drill ? this.ticksDone / this.drill.tickRate : 0;
  }

  /** Log `appear` and `hide` for every target whose hittability just changed. */
  private noteVisibility(): void {
    if (this.drillPhase !== 'running') return;
    const t = this.drillClock;
    for (const target of this.targets) {
      const now = hittable(target);
      const was = this.shown.has(target.id);
      if (now && !was) {
        this.shown.add(target.id);
        this.events.push({ t, kind: 'appear', target: target.id });
      } else if (!now && was) {
        this.shown.delete(target.id);
        // A kill already ended this appearance in the log; only a schedule
        // hiding a standing target is a `hide`.
        if (target.alive) this.events.push({ t, kind: 'hide', target: target.id });
      }
    }
  }

  private updateDrill(dt: number): void {
    const drill = this.drill;
    const world = this.drillWorld;
    if (!drill || !world || this.drillPhase === 'done') return;
    if (this.drillPhase === 'countdown') {
      this.countdown -= dt;
      if (this.countdown > 0) return;
      this.drillPhase = 'running';
      this.noteVisibility();
      return;
    }
    // Whole fixed ticks only, whatever the frame rate: that is what makes the
    // schedule replay to the positions the server simulated.
    const tickDt = 1 / drill.tickRate;
    this.tickAcc += Math.min(dt, 0.25);
    while (this.tickAcc >= tickDt && this.ticksDone < drill.ticks) {
      this.tickAcc -= tickDt;
      for (const target of this.targets) {
        const mover = target.mover;
        if (!mover) continue;
        mover.advance(world);
        target.x = mover.state.x;
        target.y = mover.state.y;
        target.z = mover.state.z;
      }
      this.ticksDone += 1;
      this.noteVisibility();
    }
    if (this.ticksDone >= drill.ticks) {
      this.drillPhase = 'done';
      this.stats = summarise(this.events, drill.seconds);
    }
  }

  /** The range's loadout, by slot, as `MatchPlayer.loadout` is. */
  private slots: LoadoutSlots | null = null;

  /** Carry `slots`: what the number keys select, and the only weapons held. */
  setLoadout(slots: LoadoutSlots): void {
    this.slots = slots;
    this.slot = slots.primary;
    this.reloadIn = 0;
    this.sprayIndex = 0;
  }

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

  /**
   * Put still dummies exactly on `points` — a drill station's own posts, for
   * free practice on a range map. `place` stands them on spawn points, and on
   * hd_aim every other spawn is another lane's shooter mark, behind a wall.
   */
  placeOn(points: { x: number; y: number; z: number }[]): void {
    this.targets = points.slice(0, MAX_TARGETS).map((p, i) => ({
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

  /**
   * Put the map's items out, from the placements the server resolved.
   *
   * Takes `reach` too rather than hardcoding a radius: Train decides its own
   * pickups locally, and a copy of that number here would be a range where items
   * come off the floor at a different distance than they do in a match.
   */
  placeItems(rows: ItemRow[], kinds: ItemSpec[], reach: ItemReach): void {
    const byKind = new Map(kinds.map((k) => [k.kind, k]));
    this.reach = reach;
    // Every item is *drawn*, including the ones the range will not give you:
    // the map's item layout is a real thing to learn, and a range missing half
    // of it would teach a map that does not exist. Only the ammunition ones
    // ever go away — see the note on `items`.
    this.itemRows = rows;
    this.items = rows.flatMap((row) => {
      const spec = byKind.get(row.kind);
      // Ammunition only, and silence is correct here: a health pack on the range
      // is a thing to run past, and drawing it while refusing to give anything
      // is the honest depiction of that.
      if (!spec || !spec.mags) return [];
      return [{ row, spec, backAt: 0 }];
    });
  }

  /** Every item on the map, for the renderer. */
  placements(): ItemRow[] {
    return this.itemRows;
  }

  /** Ids of items currently taken, for the renderer. */
  takenIds(): number[] {
    return this.items.filter((i) => i.backAt > this.clock).map((i) => i.row.id);
  }

  /**
   * Take anything the body at `(x, y, z)` is standing on.
   *
   * Called from the render loop with the player's own position, which is the
   * offline equivalent of the server collecting after a step.
   */
  collect(x: number, y: number, z: number): void {
    const reach = this.reach;
    if (!reach) return;
    for (const item of this.items) {
      if (item.backAt > this.clock) continue;
      if (Math.hypot(item.row.x - x, item.row.y - y) > reach.radius) continue;
      const dz = z - item.row.z;
      if (dz < -reach.below || dz > reach.above) continue;

      let rounds = 0;
      this.weapons.forEach((weapon, index) => {
        // `-1` is unlimited and stays unlimited, the same rule `finishReload`
        // follows: topping up a bottomless reserve would report rounds nobody
        // received.
        if (weapon.reserve < 0) return;
        const gain = Math.min(
          Math.round(weapon.mag * item.spec.mags),
          weapon.reserve - this.reserve[index],
        );
        if (gain <= 0) return;
        this.reserve[index] += gain;
        rounds += gain;
      });
      // An item that can give nothing is not consumed — the same rule the server
      // follows, and for the same reason: taking it off the floor having gained
      // nothing makes the map's rhythm a lie.
      if (rounds === 0) continue;
      item.backAt = this.clock + item.spec.respawn;
      this.pendingPickups.push({ item: item.row.id, kind: item.row.kind, rounds });
    }
  }

  select(slot: number): void {
    const held = this.slots ? Object.values(this.slots).includes(slot) : true;
    if (slot >= 0 && slot < this.weapons.length && slot !== this.slot && held) {
      this.slot = slot;
      // A switch cancels a reload, exactly as it does server-side: otherwise the
      // timer keeps running on a weapon you are no longer holding and fills it
      // while you are somewhere else.
      this.reloadIn = 0;
      // And puts you back at the top of the pattern, the same rule
      // `_handle_combat` follows: a weapon you just drew must not fire from
      // halfway down someone else's recoil curve.
      this.sprayIndex = 0;
    }
  }

  requestReload(): void {
    const weapon = this.weapons[this.slot];
    if (!weapon || weapon.mag <= 0 || this.reloadIn > 0) return;
    if (this.ammo[this.slot] >= weapon.mag) return;
    if (this.reserve[this.slot] === 0) return;
    this.reloadIn = weapon.reloadTime;
    // A magazine change is the end of a burst by definition — `_begin_reload`.
    this.sprayIndex = 0;
  }

  /**
   * How far into the spray pattern this burst is, and when it last fired.
   *
   * Kept here rather than in `ShotController` because on the range there is no
   * server to adopt an index from — this class plays both parts. Measured on
   * `clock`, the range's own simulated time, which is the same quantity
   * `player.sim_time` is on the server.
   */
  private sprayIndex = 0;
  private lastRangeFireAt = -Infinity;

  /** Advance reload timers and stand downed targets back up. */
  update(dt: number): void {
    this.clock += dt;
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
        target.hp = this.drill?.hp ?? TARGET_HP;
      }
    }
    this.updateDrill(dt);
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
    physics3d?: import('./physics-rapier').RapierPhysicsWorld,
    world3d?: import('./world3d').World3D,
    /**
     * How the shooter was moving, for the accuracy model. Omitted is standing
     * still, which is what every caller that predates the model meant.
     */
    motion: RangeMotion = STANDING,
  ): RangeShot | null {
    const weapon = this.weapons[this.slot];
    if (!weapon) return null;
    if (weapon.mag > 0) {
      if (this.ammo[this.slot] <= 0) return null;
      this.ammo[this.slot] -= 1;
    }

    const origin = eyePosition(x, y, z, eye);
    // **The recoil pattern applies here too, and it has to.** A range that
    // taught a different recoil than a match would be worse than no range: the
    // whole point of Train is learning the spray, and learning the wrong one is
    // the failure this cannot have — the same argument the cone below already
    // makes for the scope.
    //
    // The index is kept here rather than in `ShotController` because the range
    // is the server as well as the client — there is nobody to adopt it from.
    // The reset gate is the served `sprayReset`, on the range's own clock.
    const sinceLastShot = this.clock - this.lastRangeFireAt;
    if (sinceLastShot > (weapon.sprayReset ?? 0)) {
      this.sprayIndex = 0;
    }
    this.lastRangeFireAt = this.clock;
    const [aimYaw, aimPitch] = applySpray(yaw, pitch, sprayOffset(weapon, this.sprayIndex));
    this.sprayIndex += 1;
    const direction = aimVector(aimYaw, aimPitch);
    // The accuracy model, exactly as the server fires it (`weapons.shot_cone`):
    // the residual cone with the scope and pattern applied, then first-shot,
    // crouch, movement, air and bloom. A range that let you run and gun would
    // teach the one habit a match punishes.
    const cone = shotCone(weapon, {
      speed: motion.speed,
      airborne: motion.airborne,
      crouch: motion.crouch,
      scoped,
      sinceLastShot,
    });
    const ends: Vec[] = [];
    const faces: number[] = [];
    const hits: TargetHit[] = [];
    const allShattered: string[] = [];
    let hadWallbang = false;

    for (let pellet = 0; pellet < Math.max(1, weapon.pellets); pellet++) {
      const [pdx, pdy, pdz] = spreadVector(direction, cone, rand);
      let wall = weapon.range;
      let face = 0;
      let penFactor = 1.0;
      let pelletWallbang = false;

      if (physics3d) {
        const rayResult = physics3d.castRayPenetrating(
          { x: origin[0], y: origin[1], z: origin[2] },
          { x: pdx, y: pdy, z: pdz },
          weapon.range,
          visualId(weapon),
        );
        wall = rayResult.dist;
        penFactor = rayResult.damageFactor;
        pelletWallbang = rayResult.wallbang;
        if (rayResult.shatteredWindows.length > 0) {
          for (const wid of rayResult.shatteredWindows) {
            allShattered.push(wid);
            world3d?.shatterWindow?.(wid);
          }
        }
      } else {
        const res = raycastWorldFace(world, origin, [pdx, pdy, pdz], weapon.range);
        wall = res.distance;
        face = res.face;
      }

      let best: { distance: number; target: Target } | null = null;
      for (const target of this.targets) {
        if (!hittable(target)) continue;
        // A crouching drill target is a shorter body, exactly as a crouching
        // player is: the head moves, which is what the crouch-peek drill is for.
        const height = heightAt(target.mover?.state.crouch ?? 0);
        const distance = rayHitsBody(
          origin,
          [pdx, pdy, pdz],
          [target.x, target.y, target.z],
          undefined,
          height,
        );
        // A body behind an impenetrable wall is not a target. If penetrated, damage carries through!
        if (distance === null) continue;
        if (distance >= wall && penFactor <= 0.05) continue;
        if (best === null || distance < best.distance) best = { distance, target };
      }

      if (best === null) {
        ends.push([origin[0] + pdx * wall, origin[1] + pdy * wall, origin[2] + pdz * wall]);
        faces.push(face);
        continue;
      }

      const { distance, target } = best;
      const point: Vec = [
        origin[0] + pdx * distance,
        origin[1] + pdy * distance,
        origin[2] + pdz * distance,
      ];
      // Relative to the top of the body, so the head is where the head is,
      // standing or crouched.
      const spec = currentHitbox();
      const tall = heightAt(target.mover?.state.crouch ?? 0);
      const head = point[2] >= target.z + (tall - spec.headBand);
      const nutshot =
        !head && point[2] >= target.z + tall * 0.38 && point[2] <= target.z + tall * 0.55;
      const limbs = !head && !nutshot && point[2] < target.z + tall * 0.38;
      // A heads-only drill still counts a body shot as a hit; it just does nothing.
      const bodyCounts = !(this.drill?.headOnly ?? false);
      const mult = head
        ? weapon.headMultiplier
        : !bodyCounts
          ? 0
          : nutshot
            ? 1.5
            : limbs
              ? 0.75
              : 1.0;
      const wallbangHit = pelletWallbang || distance > wall;
      if (wallbangHit) hadWallbang = true;
      const finalDamageFactor = distance > wall ? penFactor : 1.0;
      const amount = Math.round(
        damageAt(weapon, distance, falloffStart(weapon)) * mult * finalDamageFactor,
      );
      target.hp -= amount;
      const killed = target.hp <= 0;
      if (killed) {
        target.alive = false;
        target.downFor = this.drill?.respawn ?? TARGET_RESPAWN;
      }
      hits.push({ id: target.id, damage: amount, head, killed, wallbang: wallbangHit });
      ends.push(point);
      faces.push(FACE_NONE);
    }

    if (hits.length > 0) this.pendingHits.push(...hits);
    if (this.drill && this.drillPhase === 'running') {
      this.events.push({
        t: this.drillClock,
        kind: 'shot',
        hits: hits.map((h) => ({ target: h.id, head: h.head, killed: h.killed })),
      });
      // A kill ends the appearance in the log; the target is no longer shown.
      for (const h of hits) if (h.killed) this.shown.delete(h.id);
    }
    return {
      origin,
      ends,
      faces,
      hits,
      wallbang: hadWallbang,
      shatteredWindows: allShattered.length > 0 ? allShattered : undefined,
    };
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
      // The same two fields a match sends, so the number keys and the wheel
      // behave identically in Train.
      ...(this.slots
        ? { held: [this.slots.primary, this.slots.secondary, this.slots.melee], slots: this.slots }
        : {}),
      hits,
      picked: this.drainPickups(),
    };
  }

  private drainPickups(): PickedItem[] {
    const out = this.pendingPickups;
    this.pendingPickups = [];
    return out;
  }

  /** The dummies as bodies the avatar pool can draw. */
  rows(): PlayerRow[] {
    // A pop-up hidden by its schedule is not drawn at all: a body standing there
    // unhittable would be a target that lies.
    const drawn = this.targets.filter((t) => t.mover?.visible ?? true);
    return drawn.map((target) => ({
      id: target.id,
      name: target.name,
      // Team 1 throughout: they render in the opposing colour, which is the
      // colour a thing you are meant to shoot at should be.
      team: 1,
      x: target.x,
      y: target.y,
      z: target.z,
      yaw: target.mover?.state.yaw ?? 0,
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
      crouch: target.mover?.state.crouch ?? 0,
    }));
  }

  reset(): void {
    this.stopDrill();
    this.targets = [];
    this.pendingHits = [];
    this.reloadIn = 0;
    this.ammo = this.weapons.map((w) => w.mag);
    this.reserve = this.weapons.map((w) => w.reserve);
    this.pendingPickups = [];
    // The items stay placed — a reset restocks *you*, and putting every item
    // back at the same moment would hide the respawn cycle the range exists to
    // let you learn.
    for (const item of this.items) item.backAt = 0;
  }
}
