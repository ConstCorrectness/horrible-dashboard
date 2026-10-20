/**
 * The armoury's Store tab: credits, keys, the cases you own, and opening one.
 *
 * Every number is the game server's (`hassault_wallet.py`), through the node:
 * the balance, the key price, the odds (`cases.py`) and the skin a case made.
 * The reel is decided before it moves. The server has rolled the skin by the
 * time the answer arrives, so the strip is built around that result and only
 * slides to it; nothing here can change what came out.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { RollingNumber } from '../../../DataList';
import {
  buyCaseKey,
  getWallet,
  listCases,
  openCase,
  type CaseSpec,
  type SkinDefinition,
  type SkinInstance,
  type Wallet,
} from '../api';

const MUTED = 'var(--text-secondary)';
const BORDER = 'var(--border)';
const RAISED = 'var(--bg-raised)';

/** How many tiles the reel holds, and where the winner sits in it. */
const REEL_TILES = 40;
const WIN_AT = 33;
const TILE_W = 112;
/** How long the slide takes; the result is revealed when it ends. */
const SPIN_MS = 4200;

const heading = {
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase' as const,
};
const mono = { fontFamily: 'var(--font-mono, monospace)', color: MUTED };

const percent = (p: number) => (p >= 0.1 ? `${(p * 100).toFixed(1)}%` : `${(p * 100).toFixed(2)}%`);

/** A reel of filler drawn from the case, with the real result at `WIN_AT`. */
function buildReel(contents: SkinDefinition[], won: SkinDefinition): SkinDefinition[] {
  const tiles: SkinDefinition[] = [];
  for (let i = 0; i < REEL_TILES; i += 1) {
    tiles.push(i === WIN_AT ? won : contents[Math.floor(Math.random() * contents.length)]);
  }
  return tiles;
}

function Tile({ skin, index }: { skin: SkinDefinition; index: number }) {
  return (
    <div
      style={{
        flex: `0 0 ${TILE_W - 8}px`,
        marginRight: 8,
        padding: '0.4rem',
        background: RAISED,
        borderTop: `2px solid ${skin.rarityColor}`,
        // Staggered in, capped so a long strip does not take seconds to arrive.
        animation: `hd-case-tile-in 240ms ease-out ${Math.min(index, 12) * 25}ms both`,
      }}
    >
      <div style={{ fontSize: '12px', fontWeight: 700 }}>{skin.name}</div>
      <div style={{ ...mono, fontSize: '11px' }}>{skin.weaponId}</div>
    </div>
  );
}

export function CaseStore({ onOpened }: { onOpened: () => void }) {
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [cases, setCases] = useState<CaseSpec[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reel, setReel] = useState<{ tiles: SkinDefinition[]; moved: boolean } | null>(null);
  const [won, setWon] = useState<(SkinInstance & { caseId: string }) | null>(null);
  const [revealed, setRevealed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setWallet(await getWallet());
      setError(null);
    } catch (err) {
      setWallet(null);
      setError(err instanceof Error ? err.message : 'the wallet could not be read');
    }
  }, []);

  useEffect(() => {
    void refresh();
    void listCases()
      .then(setCases)
      .catch(() => setCases([]));
  }, [refresh]);

  const byId = useMemo(() => new Map(cases.map((c) => [c.id, c])), [cases]);
  const owned = useMemo(() => {
    const counts = new Map<string, { name: string; instances: string[] }>();
    for (const c of wallet?.cases ?? []) {
      const entry = counts.get(c.caseId) ?? { name: c.name, instances: [] };
      entry.instances.push(c.instanceId);
      counts.set(c.caseId, entry);
    }
    return [...counts.entries()];
  }, [wallet]);

  const buyKey = async () => {
    setBusy(true);
    try {
      await buyCaseKey();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not buy a key');
    } finally {
      setBusy(false);
    }
  };

  const open = async (caseId: string, instanceId: string) => {
    const spec = byId.get(caseId);
    setBusy(true);
    setRevealed(false);
    try {
      const made = await openCase(instanceId);
      const skin = made.definition ?? spec?.contents.find((s) => s.id === made.skinId);
      setWon(made);
      if (spec && skin) {
        setReel({ tiles: buildReel(spec.contents, skin), moved: false });
        // Next frame-ish: set the target so the transition has a start to leave.
        setTimeout(() => setReel((r) => (r ? { ...r, moved: true } : r)), 50);
      }
      // The reveal never waits on a transition event: a backgrounded tab may
      // never fire one, and the skin is already in the inventory either way.
      setTimeout(() => setRevealed(true), spec && skin ? SPIN_MS + 120 : 0);
      await refresh();
      onOpened();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not open the case');
    } finally {
      setBusy(false);
    }
  };

  if (!wallet) {
    return (
      <div style={{ padding: '1rem', borderTop: '2px solid var(--accent)', background: RAISED }}>
        <div style={heading}>Store</div>
        <div style={{ ...mono, marginTop: '0.4rem' }}>{error ?? 'Reading the wallet…'}</div>
      </div>
    );
  }

  const offset = reel?.moved ? -(WIN_AT * TILE_W) + TILE_W * 1.5 : 0;

  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', gap: '1rem' }}
    >
      <style>{`@keyframes hd-case-tile-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }`}</style>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            padding: '0.6rem 0.8rem',
            background: RAISED,
            borderTop: '2px solid var(--accent)',
          }}
        >
          <span style={heading}>Credits</span>
          <span
            style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '21px', fontWeight: 700 }}
          >
            <RollingNumber value={wallet.balance} />
          </span>
          <span style={mono}>{wallet.keys} KEYS</span>
          <button
            type="button"
            onClick={() => void buyKey()}
            disabled={busy || wallet.balance < wallet.keyPrice}
          >
            Buy key · {wallet.keyPrice}
          </button>
        </div>

        {error && <div style={{ ...mono, color: 'var(--danger)' }}>{error}</div>}

        {reel && (
          <div
            style={{
              position: 'relative',
              overflow: 'hidden',
              border: `1px solid ${BORDER}`,
              padding: '0.5rem 0',
            }}
          >
            <div
              style={{
                position: 'absolute',
                left: TILE_W * 1.5 + TILE_W / 2 - 4,
                top: 0,
                bottom: 0,
                width: 2,
                background: 'var(--accent)',
                zIndex: 1,
              }}
            />
            <div
              style={{
                display: 'flex',
                transform: `translateX(${offset}px)`,
                transition: reel.moved
                  ? `transform ${SPIN_MS}ms cubic-bezier(0.12, 0.8, 0.2, 1)`
                  : 'none',
              }}
            >
              {reel.tiles.map((skin, i) => (
                <Tile key={i} skin={skin} index={i} />
              ))}
            </div>
          </div>
        )}

        {won && revealed && (
          <div
            style={{
              padding: '0.6rem 0.8rem',
              background: RAISED,
              borderTop: `2px solid ${won.definition?.rarityColor ?? 'var(--accent)'}`,
            }}
          >
            <div style={heading}>{won.definition?.name ?? won.skinId}</div>
            <div style={mono}>
              {won.wearName} · FLOAT {won.floatValue.toFixed(5)} · SEED {won.patternSeed}
              {won.statTrackerKills !== null ? ' · STATTRAK' : ''}
            </div>
          </div>
        )}

        <div style={heading}>Your cases</div>
        {owned.length === 0 && (
          <div style={mono}>No cases yet. The week's first win drops one.</div>
        )}
        {owned.map(([caseId, entry]) => (
          <div
            key={caseId}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '0.5rem 0.8rem',
              background: RAISED,
              borderLeft: '2px solid var(--accent)',
            }}
          >
            <span style={{ fontWeight: 700 }}>{entry.name}</span>
            <span style={mono}>×{entry.instances.length}</span>
            <button
              type="button"
              onClick={() => void open(caseId, entry.instances[0])}
              disabled={busy || wallet.keys < 1}
              title={wallet.keys < 1 ? 'Opening a case takes a key' : undefined}
            >
              Open
            </button>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
        <div style={heading}>Odds</div>
        {cases.map((c) => (
          <div key={c.id} style={{ padding: '0.5rem 0.8rem', background: RAISED }}>
            <div style={{ fontWeight: 700, marginBottom: '0.3rem' }}>{c.name}</div>
            {c.odds.map((o) => (
              <div
                key={o.rarity}
                style={{ ...mono, display: 'flex', justifyContent: 'space-between' }}
              >
                <span>{o.rarity.replace('_', ' ').toUpperCase()}</span>
                <span>{percent(o.chance)}</span>
              </div>
            ))}
          </div>
        ))}
        <div style={heading}>Ledger</div>
        {wallet.history.slice(0, 8).map((row, i) => (
          <div key={i} style={{ ...mono, display: 'flex', justifyContent: 'space-between' }}>
            <span>{row.kind.toUpperCase()}</span>
            <span style={{ color: row.delta >= 0 ? 'var(--success)' : 'var(--danger)' }}>
              {row.delta >= 0 ? '+' : ''}
              {row.delta}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
