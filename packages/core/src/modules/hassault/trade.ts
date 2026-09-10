/**
 * Whether pressing a buy-menu row buys it or sells it back.
 *
 * Decided off `you.mode.sellable`, the server's list, and never off `bought`.
 * Owning something is not the same as having paid for it — a rifle picked up
 * off a teammate's drop is owned and not refundable — and a client guessing the
 * difference would send sales the server refuses, which from the keyboard looks
 * exactly like a menu that does not work.
 *
 * Pure and free of React and three, so it is unit-tested headless.
 */
import type { ModeSelf } from './net';

/** At most one of the two is set; neither means "nothing to send". */
export interface Trade {
  buy?: number;
  sell?: number;
}

export function tradeFor(index: number, mine: ModeSelf | null | undefined): Trade {
  if (index < 0) return {};
  return (mine?.sellable ?? []).includes(index) ? { sell: index } : { buy: index };
}
