/**
 * The boot sequence: what the pane is doing before you are playing, and how far
 * along it is.
 *
 * Deliberately pure — no React, no three, no fetch. The panel owns the effects and
 * feeds observations in; this file only decides which screen to show and what the
 * loading bar reads. That split is what makes it unit-testable: a core vitest run
 * has no DOM, and importing anything that reaches the module manifest dies at
 * module scope (see `__tests__/world.test.ts`).
 *
 * The progress number is **real**. Every stage below corresponds to work that
 * actually happens, weighted by roughly how long it takes, and the status line
 * names the stage rather than inventing reassurance. A loading bar that is lying
 * is worse than no loading bar, because you can't tell a slow map from a hung one.
 */

/**
 * Where the pane is.
 *
 * - `loading` — assembling the world; nothing is interactive.
 * - `signin`  — built, orbiting, waiting for an account.
 * - `enlist`  — signed in, but no callsign yet: sign-up's second half.
 * - `ready`   — everything satisfied, waiting for the player to deploy.
 * - `playing` — pointer lock is live and input belongs to the game.
 */
export type BootPhase = 'loading' | 'signin' | 'enlist' | 'ready' | 'playing';

/** One unit of startup work. `weight`s are relative and need not sum to 1. */
export interface BootStage {
  id: 'renderer' | 'install' | 'map' | 'mesh' | 'reveal';
  /** Shown in the status line, in caps. */
  label: string;
  weight: number;
}

/**
 * Weights are wall-clock guesses, and the map download dominates on purpose: it is
 * the only stage whose duration actually varies, so giving it the largest share is
 * what makes the bar move smoothly instead of hanging at one number.
 */
export const BOOT_STAGES: readonly BootStage[] = [
  { id: 'renderer', label: 'Loading renderer', weight: 2 },
  { id: 'install', label: 'Locating AssaultCube', weight: 1 },
  { id: 'map', label: 'Reading map geometry', weight: 5 },
  { id: 'mesh', label: 'Compiling geometry', weight: 2 },
  { id: 'reveal', label: 'Assembling world', weight: 2 },
];

const TOTAL_WEIGHT = BOOT_STAGES.reduce((sum, s) => sum + s.weight, 0);

/** Per-stage completion, each 0..1. */
export type BootProgress = Record<BootStage['id'], number>;

export const EMPTY_PROGRESS: BootProgress = {
  renderer: 0,
  install: 0,
  map: 0,
  mesh: 0,
  reveal: 0,
};

/** Who the node is playing as — mirrors `GET /api/hassault/session`. */
export interface HassaultSession {
  signed_in: boolean;
  account_id?: string | null;
  display_name?: string | null;
  callsign?: string | null;
  enlisted: boolean;
}

export const SIGNED_OUT: HassaultSession = { signed_in: false, enlisted: false };

function clamp01(value: number): number {
  // NaN (a zero-length map, a missing Content-Length) would otherwise poison the
  // whole weighted sum and freeze the bar at NaN%.
  if (!Number.isFinite(value)) return 0;
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** Overall completion, 0..1. */
export function bootProgress(progress: BootProgress): number {
  let done = 0;
  for (const stage of BOOT_STAGES) done += clamp01(progress[stage.id]) * stage.weight;
  return done / TOTAL_WEIGHT;
}

/**
 * Progress never runs backwards.
 *
 * Worth being explicit about: a stage can legitimately report *less* than a later
 * one already has (a re-fetch after an HTTP-cache hit, a mesh rebuild on a map
 * switch), and a bar that jumps back reads as a fault even when nothing is wrong.
 */
export function advance(prev: BootProgress, next: Partial<BootProgress>): BootProgress {
  const merged = { ...prev };
  for (const stage of BOOT_STAGES) {
    const value = next[stage.id];
    if (value === undefined) continue;
    merged[stage.id] = Math.max(merged[stage.id], clamp01(value));
  }
  return merged;
}

/** The stage currently in flight — the first that isn't finished. */
export function currentStage(progress: BootProgress): BootStage | null {
  return BOOT_STAGES.find((stage) => clamp01(progress[stage.id]) < 1) ?? null;
}

/** The caps-mono line under the progress bar. Names the real step, or the error. */
export function statusLine(progress: BootProgress, error?: string | null): string {
  if (error) return error.toUpperCase();
  const stage = currentStage(progress);
  return (stage ? stage.label : 'Ready').toUpperCase();
}

export function isLoaded(progress: BootProgress): boolean {
  return currentStage(progress) === null;
}

/**
 * Which screen to show.
 *
 * The order is the product: nothing is decided about the player until the world is
 * standing, because the sign-in screen is meant to sit *over* the finished map. And
 * `enlisted` is checked separately from `signed_in` — an account with no callsign
 * has no name to play under, so sign-up isn't finished, and the backend refuses the
 * join either way (see `channel._signed_in_callsign`).
 */
export function bootPhase(
  progress: BootProgress,
  session: HassaultSession,
  deployed: boolean,
): BootPhase {
  if (!isLoaded(progress)) return 'loading';
  if (!session.signed_in) return 'signin';
  if (!session.enlisted) return 'enlist';
  return deployed ? 'playing' : 'ready';
}

/** Whether the pane should be accepting mouse-look and movement. Pointer lock is
 * requested nowhere else, so a click on a sign-in field can't be swallowed by it. */
export function acceptsGameInput(phase: BootPhase): boolean {
  return phase === 'playing';
}

/**
 * Bytes as the loader shows them: `96 / 147 KB`, or just the amount so far when
 * the response didn't say how big it is.
 *
 * The unit is chosen from the **total**, not from each value, so it doesn't switch
 * mid-download — and KB is not a fallback here but the common case: a 128-cube map
 * is nine 16 KB planes, so a fixed MB unit would count the whole load as "0.0".
 */
export function formatBytes(loaded: number, total: number | null): string {
  const scale = total && Number.isFinite(total) ? total : loaded;
  const mb = scale >= 1_048_576;
  const unit = mb ? 'MB' : 'KB';
  const fmt = (n: number) => (mb ? (n / 1_048_576).toFixed(1) : Math.round(n / 1024).toString());
  if (!total || !Number.isFinite(total)) return `${fmt(loaded)} ${unit}`;
  return `${fmt(loaded)} / ${fmt(total)} ${unit}`;
}
