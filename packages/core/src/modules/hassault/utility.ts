/**
 * The client's half of throwing: what is selected, and when it leaves the hand.
 *
 * The sibling of `combat.ts`, and the split is the same. Nothing here decides
 * where a grenade lands, what it hits, or how blind anyone ends up — that is the
 * server's and only the server's (`backend/modules/hassault/grenades.py`). What
 * this owns is the two things that must happen on the frame the key goes down:
 * the count on the HUD drops, and the next command carries `throw: true`.
 *
 * **The throw is edge-triggered, and that is the whole reason this class
 * exists.** `throw` rides on a movement command, so a key simply read as "held"
 * would set the flag on every frame it is down — sixty throws a second, of which
 * the server's cooldown would accept one and silently discard the rest. The
 * player would see one grenade for a key they held, an empty pouch, and nothing
 * to explain the difference.
 *
 * Carry counts are predicted the same way `combat.ts` predicts ammo: decremented
 * locally the instant you throw, overwritten by `you.nades` on the next
 * snapshot. Usually right, always corrected.
 *
 * Deliberately free of three and of React, so all of it is unit-testable
 * headless.
 */
import type { TacticalSpec } from './api';
import type { SelfState } from './net';

/** What a frame decided about throwing. */
export interface ThrowIntent {
  throw: boolean;
  /** Slot to throw, or `-1` when nothing is going out this frame. */
  nade: number;
  lob: boolean;
}

/**
 * How a grenade left the hand.
 *
 * `overhand` is the full throw, `underhand` the short lob — the two the server
 * already knew about (`LOB_SCALE`), now reachable from the two mouse buttons
 * rather than from two keys on the far side of the keyboard.
 */
export type ThrowStyle = 'overhand' | 'underhand';

export const NO_THROW: ThrowIntent = { throw: false, nade: -1, lob: false };

/**
 * Seconds between throws, mirroring `THROW_COOLDOWN` in `match.py`.
 *
 * Not a second enforcement — the server owns it — but the same reason the fire
 * rate is mirrored in `ShotController`: without it the client spends a command
 * field every frame on a throw the server has already decided to refuse.
 */
export const THROW_COOLDOWN_MS = 900;

export class GrenadeController {
  /** The served catalogue, in slot order. Empty until `/tacticals` answers. */
  private specs: TacticalSpec[] = [];
  /** Which slot is readied. Selecting only readies; throwing is its own key. */
  private slot = 0;
  /** Predicted carry counts, keyed by grenade id. */
  private counts: Record<string, number> = {};
  private wantThrow = false;
  private wantLob = false;
  private lastThrowAt = -Infinity;
  /**
   * The slot currently **in your hand**, or `-1` when a weapon is.
   *
   * Selecting used to only *ready* a grenade — the gun stayed up and throwing
   * was its own key. Equipping is what lets the mouse mean throw and toss
   * without taking the right button away from the sniper's scope, whose whole
   * identity is that scope.
   *
   * Entirely client-side. The server has no concept of an equipped grenade and
   * needs none: `_throw` reads `command.nade` and nothing else.
   */
  private equippedSlot = -1;
  /** True for exactly the frame after a throw left the hand. */
  private threw = false;

  setSpecs(specs: TacticalSpec[]): void {
    this.specs = specs;
    if (this.counts && Object.keys(this.counts).length === 0) {
      for (const spec of specs) this.counts[spec.id] = spec.carried;
    }
  }

  get catalogue(): readonly TacticalSpec[] {
    return this.specs;
  }

  get selected(): number {
    return this.slot;
  }

  get selectedSpec(): TacticalSpec | undefined {
    return this.specs[this.slot];
  }

  /** How many of each we believe we are holding. */
  get carried(): Readonly<Record<string, number>> {
    return this.counts;
  }

  countOf(slot: number): number {
    const spec = this.specs[slot];
    return spec ? (this.counts[spec.id] ?? 0) : 0;
  }

  /**
   * Ready a slot.
   *
   * Selecting an empty slot is allowed and deliberate: the HUD then shows it
   * greyed with a zero, which is a better answer to "why did nothing happen"
   * than silently readying a different grenade.
   */
  select(slot: number): void {
    if (slot >= 0 && slot < this.specs.length) this.slot = slot;
  }

  /** Whether a grenade is in your hand rather than a weapon. */
  get equipped(): boolean {
    return this.equippedSlot >= 0;
  }

  /**
   * Take a grenade in hand: ready it *and* put the weapon away.
   *
   * What the number keys now do. Empty slots still equip — `select`'s reasoning,
   * and the auto-holster below means nobody is ever left holding nothing.
   */
  equip(slot: number): void {
    if (slot < 0 || slot >= this.specs.length) return;
    this.slot = slot;
    this.equippedSlot = slot;
  }

  /** Put the grenade away. A weapon key, Escape, dying, or throwing the last one. */
  holster(): void {
    this.equippedSlot = -1;
    // A throw queued on the frame the pouch was put away must not come out on
    // the next one.
    this.wantThrow = false;
    this.wantLob = false;
  }

  /**
   * Whether a throw left the hand on the frame just resolved.
   *
   * Read once by the panel to bring the previous weapon back up. A flag rather
   * than a callback because `frame` already returns everything else about the
   * frame, and a second channel out of this class is a second thing to keep in
   * step.
   */
  get justThrew(): boolean {
    return this.threw;
  }

  /** Step to the next slot that still has something in it. */
  cycle(): void {
    if (this.specs.length === 0) return;
    for (let i = 1; i <= this.specs.length; i += 1) {
      const next = (this.slot + i) % this.specs.length;
      if (this.countOf(next) > 0) {
        this.slot = next;
        return;
      }
    }
  }

  /**
   * A throw key went down this frame. Edge, not level — see the class docstring.
   */
  press(lob = false): void {
    this.wantThrow = true;
    this.wantLob = lob;
  }

  /**
   * What this frame sends, and the point at which the local count drops.
   *
   * Takes `you` for the same reason `ShotController.frame` does: the server's
   * answer is the truth, and adopting it here means a throw the server refused
   * (cooldown, empty, dead) puts the count back rather than leaving the HUD one
   * short until the next respawn.
   */
  frame(nowMs: number, you: SelfState | null): ThrowIntent {
    if (you?.nades) this.counts = { ...you.nades };

    const wanted = this.wantThrow;
    const lob = this.wantLob;
    this.wantThrow = false;
    this.wantLob = false;
    this.threw = false;

    // Dying puts the grenade away. Coming back holding one you readied in a
    // previous life, with the weapon stowed, is a spawn you cannot shoot from.
    if (you && !you.alive) this.equippedSlot = -1;

    if (!wanted) return NO_THROW;
    // Dead men throw nothing. Checked here rather than at the key, because the
    // key press is real input and swallowing it silently at the edge would make
    // a throw queued a frame before dying come out on respawn.
    if (you && !you.alive) return NO_THROW;
    if (nowMs - this.lastThrowAt < THROW_COOLDOWN_MS) return NO_THROW;

    const spec = this.specs[this.slot];
    if (!spec) return NO_THROW;
    if ((this.counts[spec.id] ?? 0) <= 0) return NO_THROW;

    this.lastThrowAt = nowMs;
    this.counts = { ...this.counts, [spec.id]: (this.counts[spec.id] ?? 0) - 1 };
    // Readying the next one you actually have. Standing there holding an empty
    // hand after your last smoke is a state with nothing to do in it.
    const emptied = this.counts[spec.id] <= 0;
    const intent: ThrowIntent = { throw: true, nade: this.slot, lob };
    this.threw = true;
    if (emptied) {
      this.cycle();
      // Nothing left of *anything*: put the pouch away rather than leaving the
      // weapon stowed and both mouse buttons doing nothing.
      if (this.specs.every((s) => (this.counts[s.id] ?? 0) <= 0)) this.equippedSlot = -1;
    }
    // The grenade has left the hand. The panel brings the weapon back up on the
    // same frame — a throw is one action, not a mode you have to leave.
    if (this.equippedSlot >= 0) this.equippedSlot = -1;
    return intent;
  }

  /** Spawning refills, matching `reset_loadout` on the server. */
  reset(): void {
    this.counts = {};
    for (const spec of this.specs) this.counts[spec.id] = spec.carried;
    this.lastThrowAt = -Infinity;
    this.wantThrow = false;
    this.wantLob = false;
    this.equippedSlot = -1;
    this.threw = false;
  }
}
