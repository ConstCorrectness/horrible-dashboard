/**
 * The client's half of shooting: trigger discipline, weapon selection, recoil.
 *
 * Nothing here decides whether anything was *hit* — that is the server's job and
 * only the server's (`backend/modules/hassault/weapons.py`). What this owns is
 * everything that has to happen on the frame the button goes down, because a gun
 * that waits a round trip to acknowledge the trigger does not feel like a gun:
 * the crosshair kicks, the magazine counts down, and the next command carries
 * `fire: true`.
 *
 * The rate limit here is not a second enforcement of the fire rate. The server
 * enforces it; this exists so a 62 rpm sniper does not send sixty `fire` flags a
 * second for the server to throw away fifty-nine of.
 *
 * Ammo is predicted the same way, and corrected the same way: the count shown is
 * decremented locally on each shot and overwritten by `you.ammo` on the next
 * snapshot. Prediction that is usually right and always corrected, which is the
 * same bargain the movement code makes.
 *
 * Deliberately free of three and of React so all of it is unit-testable headless.
 */
import type { WeaponSpec } from './api';
import type { SelfState, ShotIntent } from './net';

/** No shot intent at all — offline, dead, or between matches. */
export const NO_SHOT: ShotIntent = { fire: false, reload: false, weapon: -1, viewT: 0 };

/**
 * Upward kick per shot, in radians, as a function of the weapon.
 *
 * Tied to spread rather than to damage: a weapon that is already imprecise
 * should also be the one that climbs, and the two then reinforce each other
 * instead of being independent dials to balance.
 */
export function recoilKick(weapon: WeaponSpec): number {
  return 0.011 + weapon.spread * 0.85;
}

/** Radians per second the view drifts back down after a burst. */
const RECOVERY_RATE = 0.9;

export class ShotController {
  weapons: WeaponSpec[] = [];
  /** The slot we believe we are holding. Corrected from `you.weapon`. */
  slot = 0;
  /** Predicted magazine, so the HUD counts down on the frame you fire. */
  ammo = 0;

  private held = false;
  /** Semi-automatic weapons need the button released between shots. */
  private triggerUsed = false;
  private lastFireMs = -Infinity;
  private wantReload = false;
  private wantSlot = -1;
  /** Kick not yet recovered, so recovery cannot pull the view below where it started. */
  private owed = 0;
  private pendingKick = 0;
  private pendingYawKick = 0;

  setWeapons(specs: WeaponSpec[], slot: number): void {
    this.weapons = specs;
    this.slot = Math.max(0, Math.min(specs.length - 1, slot));
    this.ammo = this.weapon?.mag ?? 0;
  }

  get weapon(): WeaponSpec | undefined {
    return this.weapons[this.slot];
  }

  press(): void {
    this.held = true;
  }

  release(): void {
    this.held = false;
    this.triggerUsed = false;
  }

  requestReload(): void {
    this.wantReload = true;
  }

  /** Select a slot. Out-of-range numbers are ignored rather than clamped: they
   * come from a key the player pressed, and `6` means nothing, not "the last one". */
  select(slot: number): void {
    if (slot >= 0 && slot < this.weapons.length && slot !== this.slot) {
      this.wantSlot = slot;
      this.slot = slot;
      this.triggerUsed = true; // a switch does not carry the held trigger with it
    }
  }

  cycle(direction: number): void {
    if (this.weapons.length === 0) return;
    const next = (this.slot + direction + this.weapons.length) % this.weapons.length;
    this.select(next);
  }

  /**
   * The combat half of this frame's command.
   *
   * `you` is the last authoritative word on our own state; when it disagrees
   * with what we predicted — a switch the server rejected, ammo we thought we
   * had — the server wins, exactly as it does for position.
   */
  frame(nowMs: number, viewT: number, you: SelfState | null): ShotIntent {
    const intent: ShotIntent = { fire: false, reload: false, weapon: -1, viewT };
    if (this.weapons.length === 0) return intent;

    if (this.wantSlot >= 0) {
      intent.weapon = this.wantSlot;
      this.wantSlot = -1;
      // A pending switch is ours until the server answers; adopting `you.weapon`
      // now would flip the HUD back for the one round trip it takes to land.
      this.ammo = you && you.weapon === this.slot ? you.ammo : (this.weapon?.mag ?? 0);
    } else if (you) {
      this.slot = you.weapon;
      this.ammo = you.ammo;
    }

    if (this.wantReload) {
      this.wantReload = false;
      intent.reload = true;
    }

    const weapon = this.weapon;
    if (!weapon || !you || !you.alive) return intent;
    if (you.reloading) return intent;
    if (weapon.mag > 0 && this.ammo <= 0) {
      // Out. Ask for the reload the server is about to start anyway, so the HUD
      // shows it on this frame rather than on the next snapshot.
      if (this.held) intent.reload = true;
      return intent;
    }
    if (!this.held || (!weapon.auto && this.triggerUsed)) return intent;
    if (nowMs - this.lastFireMs < weapon.interval * 1000) return intent;

    this.lastFireMs = nowMs;
    this.triggerUsed = true;
    if (weapon.mag > 0) this.ammo -= 1;
    intent.fire = true;

    const kick = recoilKick(weapon);
    this.pendingKick += kick;
    // Horizontal component is signed noise rather than a pattern: a learnable
    // spray pattern is a feature of games with a much longer time-to-kill.
    this.pendingYawKick += (Math.random() - 0.5) * kick * 0.7;
    this.owed += kick;
    return intent;
  }

  /**
   * View-angle delta for this frame: the kick from any shot, plus recovery.
   *
   * Recovery never pulls below where the burst started (`owed` is what is left
   * to give back), so a player who fights the recoil down by hand does not then
   * get dragged further down when they stop.
   */
  recoil(dt: number): { yaw: number; pitch: number } {
    const pitch = this.pendingKick;
    const yaw = this.pendingYawKick;
    this.pendingKick = 0;
    this.pendingYawKick = 0;
    let recovered = 0;
    if (this.owed > 0 && pitch === 0) {
      recovered = Math.min(this.owed, RECOVERY_RATE * dt);
      this.owed -= recovered;
    }
    return { yaw, pitch: pitch - recovered };
  }

  /** Crosshair gap in pixels: wider while firing, so the spread is visible. */
  crosshairSpread(): number {
    const weapon = this.weapon;
    if (!weapon) return 4;
    return 4 + weapon.spread * 260 + this.owed * 90;
  }

  reset(): void {
    this.held = false;
    this.triggerUsed = false;
    this.lastFireMs = -Infinity;
    this.wantReload = false;
    this.wantSlot = -1;
    this.owed = 0;
    this.pendingKick = 0;
    this.pendingYawKick = 0;
  }
}
