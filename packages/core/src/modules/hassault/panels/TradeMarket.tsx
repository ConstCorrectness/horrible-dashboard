/**
 * The armoury's Trade & Market tab: offers between players, and the credit
 * market. Everything is the game server's (`hassault_trading.py`); this tab
 * holds no rule of its own and shows the server's reason whenever it refuses.
 *
 * A trade is offered, accepted by the recipient, and then confirmed by them
 * once the server's arming delay has passed. The countdown here is only a
 * display of `armsAt`: the server refuses an early confirm regardless.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  actOnListing,
  actOnTrade,
  getPartnerItems,
  getTradeEligibility,
  getTradeLink,
  listForSale,
  listMarket,
  listTrades,
  offerTrade,
  type Listing,
  type SkinInstance,
  type Trade,
  type TradableItem,
  type TradeEligibility,
} from '../api';

const MUTED = 'var(--text-secondary)';
const RAISED = 'var(--bg-raised)';

const heading = {
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase' as const,
};
const mono = { fontFamily: 'var(--font-mono, monospace)', color: MUTED };
const card = { padding: '0.6rem 0.8rem', background: RAISED, borderTop: '2px solid var(--accent)' };

function itemName(
  item: TradableItem | SkinInstance | { instanceId: string; defId: string },
): string {
  if ('definition' in item && item.definition) return item.definition.name;
  if ('defId' in item) return item.defId.replace(/_/g, ' ');
  return item.instanceId.slice(0, 8);
}

function message(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function TradeMarket({
  inventory,
  onChanged,
}: {
  inventory: SkinInstance[];
  onChanged: () => void;
}) {
  const [eligibility, setEligibility] = useState<TradeEligibility | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [market, setMarket] = useState<Listing[]>([]);
  const [link, setLink] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const [partner, setPartner] = useState('');
  const [partnerToken, setPartnerToken] = useState('');
  const [partnerItems, setPartnerItems] = useState<TradableItem[]>([]);
  const [give, setGive] = useState<string[]>([]);
  const [want, setWant] = useState<string[]>([]);

  const [sellItem, setSellItem] = useState('');
  const [sellPrice, setSellPrice] = useState('100');

  const refresh = useCallback(async () => {
    try {
      const [e, t, m] = await Promise.all([getTradeEligibility(), listTrades(), listMarket()]);
      setEligibility(e);
      setTrades(t);
      setMarket(m);
      setError(null);
    } catch (err) {
      setError(message(err, 'trading could not be reached'));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // A clock for the arming countdowns only; nothing is decided by it.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    try {
      await fn();
      setError(null);
      await refresh();
      onChanged();
    } catch (err) {
      setError(message(err, fallback));
    }
  };

  const me = eligibility?.accountId ?? '';
  const tradable = useMemo(() => inventory.filter((i) => i.isTradable), [inventory]);
  const toggle = (list: string[], id: string) =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  if (!eligibility) {
    return (
      <div style={card}>
        <div style={heading}>Trade &amp; Market</div>
        <div style={{ ...mono, marginTop: '0.4rem' }}>{error ?? 'Reading…'}</div>
      </div>
    );
  }

  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: '1rem' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
        <div style={card}>
          <div style={heading}>Trading</div>
          {eligibility.eligible ? (
            <div style={mono}>
              LEVEL {eligibility.level} · {eligibility.serverMatches} SERVER MATCHES · ELIGIBLE
            </div>
          ) : (
            eligibility.reasons.map((r) => (
              <div key={r} style={{ ...mono, color: 'var(--warn)' }}>
                {r.toUpperCase()}
              </div>
            ))
          )}
          <div
            style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', marginTop: '0.4rem' }}
          >
            <button
              type="button"
              onClick={() => void run(async () => setLink((await getTradeLink()).token), 'no link')}
            >
              Show trade link
            </button>
            {link && (
              <>
                <span style={mono}>{link}</span>
                <button
                  type="button"
                  onClick={() =>
                    void run(async () => setLink((await getTradeLink(true)).token), 'no link')
                  }
                >
                  Rotate
                </button>
              </>
            )}
          </div>
        </div>

        {error && <div style={{ ...mono, color: 'var(--danger)' }}>{error}</div>}

        <div style={heading}>Offers</div>
        {trades.length === 0 && <div style={mono}>No offers yet.</div>}
        {trades.map((t) => {
          const incoming = t.to === me;
          const armIn = t.armsAt ? Math.ceil(t.armsAt - now) : 0;
          return (
            <div key={t.tradeId} style={{ ...card, borderTop: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 700 }}>
                  {incoming ? `FROM ${t.from}` : `TO ${t.to}`}
                </span>
                <span style={mono}>{t.state.toUpperCase()}</span>
              </div>
              <div style={mono}>
                GIVES {t.give.length} · WANTS {t.want.length}
                {t.reason ? ` · ${t.reason.toUpperCase()}` : ''}
              </div>
              <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.3rem' }}>
                {incoming && t.state === 'offered' && (
                  <>
                    <button
                      type="button"
                      onClick={() => void run(() => actOnTrade(t.tradeId, 'accept'), 'refused')}
                    >
                      Accept
                    </button>
                    <button
                      type="button"
                      onClick={() => void run(() => actOnTrade(t.tradeId, 'decline'), 'refused')}
                    >
                      Decline
                    </button>
                  </>
                )}
                {incoming && t.state === 'accepted' && (
                  <button
                    type="button"
                    disabled={armIn > 0}
                    onClick={() => void run(() => actOnTrade(t.tradeId, 'confirm'), 'refused')}
                  >
                    {armIn > 0 ? `Confirm in ${armIn}s` : 'Confirm trade'}
                  </button>
                )}
                {!incoming && (t.state === 'offered' || t.state === 'accepted') && (
                  <button
                    type="button"
                    onClick={() => void run(() => actOnTrade(t.tradeId, 'cancel'), 'refused')}
                  >
                    Cancel
                  </button>
                )}
              </div>
            </div>
          );
        })}

        <div style={heading}>New offer</div>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <input
            type="text"
            value={partner}
            onChange={(e) => setPartner(e.target.value.trim())}
            placeholder="Partner account id"
            aria-label="Partner account id"
            style={{ flex: 1, padding: '0 0.6rem' }}
          />
          <input
            type="text"
            value={partnerToken}
            onChange={(e) => setPartnerToken(e.target.value.trim())}
            placeholder="Their trade link (if not a friend)"
            aria-label="Partner trade link"
            style={{ flex: 1, padding: '0 0.6rem' }}
          />
          <button
            type="button"
            disabled={!partner}
            onClick={() =>
              void run(
                async () => setPartnerItems(await getPartnerItems(partner, partnerToken)),
                'refused',
              )
            }
          >
            Load
          </button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
          <div>
            <div style={mono}>YOU GIVE</div>
            {tradable.map((i) => (
              <label
                key={i.instanceId}
                style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}
              >
                <input
                  type="checkbox"
                  checked={give.includes(i.instanceId)}
                  onChange={() => setGive((g) => toggle(g, i.instanceId))}
                />
                <span>{itemName(i)}</span>
              </label>
            ))}
          </div>
          <div>
            <div style={mono}>YOU ASK FOR</div>
            {partnerItems.map((i) => (
              <label
                key={i.instanceId}
                style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}
              >
                <input
                  type="checkbox"
                  checked={want.includes(i.instanceId)}
                  onChange={() => setWant((w) => toggle(w, i.instanceId))}
                />
                <span>{itemName(i)}</span>
              </label>
            ))}
          </div>
        </div>
        <button
          type="button"
          disabled={!partner || !eligibility.eligible || (give.length === 0 && want.length === 0)}
          onClick={() =>
            void run(async () => {
              await offerTrade({ to: partner, give, want, token: partnerToken || undefined });
              setGive([]);
              setWant([]);
            }, 'refused')
          }
        >
          Send offer
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
        <div style={heading}>Market</div>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <select
            value={sellItem}
            onChange={(e) => setSellItem(e.target.value)}
            aria-label="Item to sell"
            style={{ flex: 1, padding: '0 0.6rem' }}
          >
            <option value="">Pick an item to sell</option>
            {tradable.map((i) => (
              <option key={i.instanceId} value={i.instanceId}>
                {itemName(i)} · {i.wearName}
              </option>
            ))}
          </select>
          <input
            type="number"
            value={sellPrice}
            onChange={(e) => setSellPrice(e.target.value)}
            aria-label="Price in credits"
            style={{ width: 96, padding: '0 0.6rem' }}
          />
          <button
            type="button"
            disabled={!sellItem || !eligibility.eligible}
            onClick={() => void run(() => listForSale(sellItem, Number(sellPrice)), 'refused')}
          >
            List
          </button>
        </div>
        <div style={mono}>A SALE PAYS THE PRICE LESS A 10% FEE, WHICH IS BURNT.</div>
        {market.length === 0 && <div style={mono}>Nothing listed.</div>}
        {market.map((l) => {
          const mine = l.sellerId === me;
          return (
            <div
              key={l.listingId}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '0.6rem',
                padding: '0.4rem 0.8rem',
                background: RAISED,
                borderLeft: '2px solid var(--accent)',
              }}
            >
              <span style={{ flex: 1 }}>{itemName(l.item)}</span>
              <span style={{ fontFamily: 'var(--font-mono, monospace)', fontWeight: 700 }}>
                {l.price}
              </span>
              {mine ? (
                <button
                  type="button"
                  onClick={() => void run(() => actOnListing(l.listingId, 'cancel'), 'refused')}
                >
                  Cancel
                </button>
              ) : (
                <button
                  type="button"
                  disabled={!eligibility.eligible}
                  onClick={() => void run(() => actOnListing(l.listingId, 'buy'), 'refused')}
                >
                  Buy
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
