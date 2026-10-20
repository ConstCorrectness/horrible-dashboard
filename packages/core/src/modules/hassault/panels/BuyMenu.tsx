/**
 * The buy menu, and the purse that sits on the HUD in a buy mode.
 *
 * A held panel like the scoreboard rather than a mode you toggle into, and for
 * the same reason: it is read *during* the freeze while the round is being
 * decided around you, and a menu you can leave up by accident is one you die
 * behind. While it is held the number row works it, CS-style: a key opens a
 * group (`buy-menu.ts`), a second key buys a row in it, `0` goes back.
 *
 * **Every number on it is the server's.** Prices and sides come off the served
 * catalogue, what you own off `you.mode.bought`, and whether a buy would land
 * off `you.mode.canBuy` (with `buyIn` and `inBuyZone` saying why not) — not a
 * phase check this client repeats. A menu with its own copy of any of those is
 * one that offers a purchase the server then refuses, leaving the money where
 * it was and saying nothing.
 */

import { RollingNumber } from '../../../DataList';
import { buyGroups, buyNotice } from '../buy-menu';
import type { ModeInfo, ModeSelf } from '../net';

/** The four row states, which are four because they mean four things. */
const OWNED = '#8fce93';
/**
 * Owned *and* paid for in this window, so pressing the row sells it back. Its
 * own colour rather than `OWNED`: a row that looks finished but refunds you on
 * a keypress is a misclick waiting to happen.
 */
const SELLABLE = '#e5b567';
const BUYABLE = 'rgba(255,255,255,0.92)';
const OUT_OF_REACH = 'rgba(255,255,255,0.38)';
const WARN = '#d1b878';
const MUTED = 'var(--text-secondary, rgba(255,255,255,0.55))';

const dollars = (n: number) => `$${Math.round(n)}`;

export interface BuyMenuProps {
  mode: ModeInfo | null;
  mine: ModeSelf | null | undefined;
  /** Our team, which decides the side's rows. */
  team: number | undefined;
  /** Whether the key is being held. */
  open: boolean;
  /** The open group (`buy-menu.ts`), or `null` at the top level. */
  group: number | null;
  /** Buy the entry at this catalogue index. The index *is* what goes on the wire. */
  onBuy: (index: number) => void;
  /** Open a group, or (`null`) go back to the top. */
  onGroup: (group: number | null) => void;
}

const rowStyle = {
  display: 'flex',
  gap: '0.6rem',
  lineHeight: 1.7,
  alignItems: 'center',
} as const;

export function BuyMenu({ mode, mine, team, open, group, onBuy, onGroup }: BuyMenuProps) {
  const catalog = mode?.catalog ?? [];
  // A mode with no economy has no menu — and every mode but defuse arrives that
  // way, so this is the common case rather than the guard.
  if (!open || !mine || catalog.length === 0) return null;

  const money = mine.money ?? 0;
  const bought = mine.bought ?? [];
  const sellable = mine.sellable ?? [];
  const canBuy = mine.canBuy ?? false;
  const notice = buyNotice(mine);
  const groups = buyGroups(catalog, team);
  const current = group !== null ? groups[group] : undefined;

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        transform: 'translate(-50%, -50%)',
        minWidth: 360,
        // Near-opaque, unlike the scoreboard. That one is glanced at while the
        // game carries on behind it; this is a panel being typed into, and a
        // crosshair showing through a price list is a menu you can misread.
        background: 'rgba(13,17,23,0.97)',
        border: '1px solid var(--border)',
        borderTop: '2px solid var(--accent)',
        clipPath: 'polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 0 100%)',
        padding: '0.6rem 0.75rem',
        fontFamily: 'monospace',
        fontSize: '0.78rem',
        // Read, and clicked only as a convenience: buying is the number row,
        // exactly as it is in the native client.
        pointerEvents: 'auto',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: '0.5rem',
          letterSpacing: '0.14em',
          fontWeight: 700,
        }}
      >
        <span style={{ color: 'rgba(255,255,255,0.92)' }}>
          {current ? `BUY / ${current.label}` : 'BUY'}
        </span>
        <span style={{ color: canBuy ? OWNED : WARN }}>
          <RollingNumber value={money} format={dollars} />
        </span>
      </div>
      <div style={{ color: notice ? WARN : MUTED, marginBottom: '0.4rem', letterSpacing: '0.1em' }}>
        {notice || `CLOSES IN ${Math.ceil(mine.buyIn ?? 0)}S`}
      </div>

      {!current &&
        groups.map((g, i) => (
          <div
            key={g.category}
            onClick={() => onGroup(i)}
            style={{ ...rowStyle, color: BUYABLE, letterSpacing: '0.14em', cursor: 'pointer' }}
          >
            <span style={{ color: MUTED, width: '1.2em' }}>{i + 1}</span>
            <span style={{ flex: 1, fontWeight: 700 }}>{g.label}</span>
            <span style={{ color: MUTED }}>{g.rows.length}</span>
          </div>
        ))}

      {current?.rows.map(({ index, item }, i) => {
        const owned = bought.includes(index);
        const refundable = sellable.includes(index);
        const afford = money >= item.price;
        const colour = refundable
          ? SELLABLE
          : owned
            ? OWNED
            : afford && canBuy
              ? BUYABLE
              : OUT_OF_REACH;
        return (
          <div
            key={item.id}
            onClick={() => onBuy(index)}
            style={{ ...rowStyle, color: colour, cursor: 'pointer' }}
          >
            {/* 1-based, matching the key you press within the group. The
                index on the wire is the catalogue's. */}
            <span style={{ color: MUTED, width: '1.2em' }}>{i + 1}</span>
            <span style={{ flex: 1 }}>{item.name}</span>
            <span>{refundable ? `SELL +$${item.price}` : owned ? 'OWNED' : `$${item.price}`}</span>
          </div>
        );
      })}

      <div
        style={{ color: MUTED, marginTop: '0.4rem', letterSpacing: '0.1em', fontSize: '0.7rem' }}
      >
        {current ? '0 BACK' : 'NUMBER KEYS PICK A GROUP'}
      </div>
    </div>
  );
}

/**
 * The purse, always on screen in a mode with money. Counts toward each change
 * (`RollingNumber`), so a kill reward reads as one landing.
 */
export function MoneyCounter({ mine }: { mine: ModeSelf | null | undefined }) {
  if (!mine || mine.money === undefined) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: 16,
        bottom: 132,
        padding: '0.2rem 0.6rem',
        background: 'rgba(13,17,23,0.72)',
        borderLeft: '2px solid var(--accent)',
        fontFamily: 'monospace',
        fontSize: '18px',
        fontWeight: 700,
        letterSpacing: '0.08em',
        color: mine.canBuy ? OWNED : 'rgba(255,255,255,0.88)',
        pointerEvents: 'none',
      }}
    >
      <RollingNumber value={mine.money} format={dollars} />
      {mine.canBuy && (
        <span
          style={{
            marginLeft: '0.6rem',
            fontSize: '0.7rem',
            color: MUTED,
            letterSpacing: '0.14em',
          }}
        >
          BUY {Math.ceil(mine.buyIn ?? 0)}S
        </span>
      )}
    </div>
  );
}
