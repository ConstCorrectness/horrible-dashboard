/**
 * The persisted half of a `MatchSetup`: which driver sits in your seat, and who
 * you last chose to play, **per game**.
 *
 * Kept as separate scalar settings rather than one JSON blob because settings are
 * scalars (`SettingValue`) and because each key is independently meaningful — the
 * agent's tools and the settings page can read "what drives tictactoe" without
 * parsing anything.
 *
 * `games.policy.<gameId>` is deliberately the same key the old MOVE POLICY toggle
 * wrote, so every saved harness keeps the seat it already had. The stored *value*
 * is a policy name (`bot` for the script driver), not a `Driver` — see
 * `policyName`/`driverFromPolicy` in matchmaking.ts.
 */
import { getSetting, setSetting } from '../../settings';
import { driverFromPolicy, policyName, type Driver } from './driver';

/**
 * Who fills the other seat.
 *
 * `mirror` is the one that carries a driver of its own: it is a **self-play**
 * table where this node holds both seats, so the second seat needs its own answer
 * to "how does it move" — that field is precisely the thing that used to be
 * missing and made "Play vs My Agent" undefined.
 */
export type Opponent =
  | { kind: 'ranked' }
  | { kind: 'bot'; tier: string }
  | { kind: 'open' }
  | { kind: 'mirror'; driver: Driver };

export type OpponentKind = Opponent['kind'];

const DEFAULT_DRIVER: Driver = 'agent';
const DEFAULT_OPPONENT: OpponentKind = 'bot';
const DEFAULT_BOT_TIER = 'bronze';

export const driverKey = (gameId: string) => `games.policy.${gameId}`;
export const opponentKey = (gameId: string) => `games.opponent.${gameId}`;
export const botTierKey = (gameId: string) => `games.botTier.${gameId}`;
export const mirrorDriverKey = (gameId: string) => `games.mirrorDriver.${gameId}`;

function isKind(value: unknown): value is OpponentKind {
  return value === 'ranked' || value === 'bot' || value === 'open' || value === 'mirror';
}

/**
 * Your seat's driver for a game.
 *
 * Resolution order is per-game override → the game's declared default (from the
 * catalog, which is why `catalogDefault` is passed in rather than fetched) → the
 * legacy global `games.policy` → agent. Reading the global last is what keeps a
 * pre-seat-model install from suddenly changing behaviour.
 *
 * `allowed` (the catalog's `allowed_policies`) filters every candidate, mirroring
 * the backend's own gate in `_resolve_policy_name`. Both sides have to apply it:
 * a setting saved before its game's category tightened is still on disk, and
 * without this the card would show a seat the server has quietly overruled.
 */
export function resolveDriver(
  gameId: string,
  catalogDefault?: string,
  allowed?: readonly string[],
): Driver {
  const ok = (d: Driver | null): Driver | null =>
    d && (!allowed || allowed.some((p) => driverFromPolicy(p) === d)) ? d : null;
  return (
    ok(driverFromPolicy(getSetting<string>(driverKey(gameId)))) ??
    ok(driverFromPolicy(catalogDefault)) ??
    ok(driverFromPolicy(getSetting<string>('games.policy'))) ??
    // The game's own default is allowed by construction (the backend refuses to
    // register a spec whose default it forbids), so it is the safe last word.
    driverFromPolicy(catalogDefault) ??
    DEFAULT_DRIVER
  );
}

export function setDriver(gameId: string, driver: Driver): void {
  void setSetting(driverKey(gameId), policyName(driver));
}

export function resolveMirrorDriver(gameId: string, allowed?: readonly string[]): Driver {
  const saved = driverFromPolicy(getSetting<string>(mirrorDriverKey(gameId)));
  // The sparring seat plays the same game, so it lives under the same category
  // rule as yours — a self-play table can't run a seat the game forbids.
  if (saved && (!allowed || allowed.some((p) => driverFromPolicy(p) === saved))) return saved;
  return allowed && !allowed.some((p) => driverFromPolicy(p) === DEFAULT_DRIVER)
    ? (driverFromPolicy(allowed[0]) ?? DEFAULT_DRIVER)
    : DEFAULT_DRIVER;
}

export function setMirrorDriver(gameId: string, driver: Driver): void {
  void setSetting(mirrorDriverKey(gameId), policyName(driver));
}

export function resolveBotTier(gameId: string): string {
  return getSetting<string>(botTierKey(gameId)) ?? DEFAULT_BOT_TIER;
}

export function setBotTier(gameId: string, tier: string): void {
  void setSetting(botTierKey(gameId), tier);
}

/** The opponent you last picked for this game, rebuilt from its parts. */
export function resolveOpponent(gameId: string): Opponent {
  const kind = getSetting<string>(opponentKey(gameId));
  switch (isKind(kind) ? kind : DEFAULT_OPPONENT) {
    case 'ranked':
      return { kind: 'ranked' };
    case 'open':
      return { kind: 'open' };
    case 'mirror':
      return { kind: 'mirror', driver: resolveMirrorDriver(gameId) };
    case 'bot':
      return { kind: 'bot', tier: resolveBotTier(gameId) };
  }
}

export function setOpponentKind(gameId: string, kind: OpponentKind): void {
  void setSetting(opponentKey(gameId), kind);
}
