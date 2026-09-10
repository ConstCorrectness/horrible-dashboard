/**
 * Whether a buy-menu row buys or sells: decided off the server's `sellable`,
 * never off `bought`.
 */
import { describe, expect, it } from 'vitest';

import { tradeFor } from '../trade';

describe('tradeFor', () => {
  it('buys a row the server has not listed as sellable', () => {
    expect(tradeFor(2, { bought: [], sellable: [] })).toEqual({ buy: 2 });
  });

  it('sells a row the server listed as sellable', () => {
    expect(tradeFor(2, { bought: [2], sellable: [2] })).toEqual({ sell: 2 });
  });

  it('does not sell something merely owned — a picked-up gun was never paid for', () => {
    expect(tradeFor(0, { bought: [0], sellable: [] })).toEqual({ buy: 0 });
  });

  it('sends nothing for no selection, and buys when there is no envelope yet', () => {
    expect(tradeFor(-1, { sellable: [3] })).toEqual({});
    expect(tradeFor(4, undefined)).toEqual({ buy: 4 });
  });
});
