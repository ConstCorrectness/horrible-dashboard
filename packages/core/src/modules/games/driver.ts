/**
 * The seat vocabulary: what can sit in a seat, and what to call it.
 *
 * Its own file so the layering stays acyclic — `driver` (vocabulary) is imported
 * by `seat` (persistence), which is imported by `matchmaking` (actions). Putting
 * these next to `startMatch` would make `seat` and `matchmaking` import each other.
 */

/** How a seat picks its moves. `agent` runs the harness's model loop, `script` its
 * Python bot, `manual` hands the board to the human, `random` picks a legal move. */
export type Driver = 'manual' | 'agent' | 'script' | 'random';

export const DRIVERS: Driver[] = ['manual', 'agent', 'script', 'random'];

export const DRIVER_LABEL: Record<Driver, string> = {
  manual: 'You',
  agent: 'My Agent',
  script: 'My Script',
  random: 'Random',
};

export const DRIVER_ICON: Record<Driver, string> = {
  manual: '🎮',
  agent: '🧠',
  script: '🤖',
  random: '🎲',
};

export const DRIVER_HINT: Record<Driver, string> = {
  manual: 'You play the board yourself. Unrated.',
  agent: 'Your harness drives: context, tools and the model pick each move.',
  script: 'Your harness’s Python bot picks each move — no model, no latency.',
  random: 'A uniformly random legal move. The baseline everything is measured against.',
};

/** The wire name for a driver. The backend policy registry predates the seat model
 * and calls the script driver `bot` (backend/modules/games/policy.py:make_policy),
 * so translate at this seam rather than renaming a stored setting out from under
 * everyone's saved harnesses. */
export function policyName(driver: Driver): string {
  return driver === 'script' ? 'bot' : driver;
}

export function driverFromPolicy(policy: string | undefined | null): Driver | null {
  if (policy === 'bot') return 'script';
  if (policy === 'manual' || policy === 'agent' || policy === 'random') return policy;
  return null;
}
