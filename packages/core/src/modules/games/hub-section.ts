/**
 * Opening the Games hub. The hub is now a single **Play** view — Ladder,
 * Challenges, Replays, Players, and Profile are standalone panels reachable from
 * the left activity rail and the command palette (see index.tsx), not internal
 * tabs, so there's no longer a "which section" store here.
 */
import { registry } from '../../registry';

/** Open (or focus) the Games hub (the Play view). */
export function openGamesHub(): void {
  registry.openPanel('games.lobby');
}
