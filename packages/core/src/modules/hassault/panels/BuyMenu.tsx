/**
 * The buy menu.
 *
 * A held panel like the scoreboard rather than a mode you toggle into, and for
 * the same reason: it is read *during* the freeze while the round is being
 * decided around you, and a menu you can leave up by accident is one you die
 * behind. Number keys buy while it is held.
 *
 * **Every number on it is the server's.** Prices come off the served catalogue,
 * what you own comes off `you.mode.bought`, and whether the window is open at
 * all is `you.mode.canBuy` — not a phase check this client repeats. A menu with
 * its own copy of any of those is one that offers a purchase the server then
 * refuses, leaving the money where it was and saying nothing.
 */

import type { ModeInfo, ModeSelf } from '../net';

/** The three row states, which are three because they mean three things. */
const OWNED = '#8fce93';
const BUYABLE = 'rgba(255,255,255,0.92)';
const OUT_OF_REACH = 'rgba(255,255,255,0.38)';

export interface BuyMenuProps {
  mode: ModeInfo | null;
  mine: ModeSelf | null | undefined;
  /** Whether the key is being held. */
  open: boolean;
  /** Buy the entry at this index. The index *is* what goes on the wire. */
  onBuy: (index: number) => void;
}

export function BuyMenu({ mode, mine, open, onBuy }: BuyMenuProps) {
  const catalog = mode?.catalog ?? [];
  // A mode with no economy has no menu — and every mode but defuse arrives that
  // way, so this is the common case rather than the guard.
  if (!open || !mine || catalog.length === 0) return null;

  const money = mine.money ?? 0;
  const bought = mine.bought ?? [];
  const canBuy = mine.canBuy ?? false;

  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        transform: 'translate(-50%, -50%)',
        minWidth: 340,
        // Near-opaque, unlike the scoreboard. That one is glanced at while the
        // game carries on behind it; this is a panel being typed into, and a
        // crosshair showing through a price list is a menu you can misread.
        background: 'rgba(13,17,23,0.97)',
        border: '1px solid var(--border, #2a2a2a)',
        borderTop: '2px solid #6fa8dc',
        borderRadius: 6,
        padding: '0.6rem 0.75rem',
        fontFamily: 'monospace',
        fontSize: '0.78rem',
        // The panel is read, not clicked: buying is the number row, exactly as
        // it is in the native client. Letting the mouse through keeps the aim
        // where it was when the menu opened.
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginBottom: '0.5rem',
          letterSpacing: '0.14em',
        }}
      >
        <span style={{ color: 'rgba(255,255,255,0.92)' }}>BUY</span>
        <span style={{ color: canBuy ? OWNED : '#d1b878' }}>${money}</span>
      </div>
      {!canBuy && (
        <div style={{ color: '#d1b878', marginBottom: '0.4rem', letterSpacing: '0.1em' }}>
          THE WINDOW IS CLOSED
        </div>
      )}
      {catalog.map((item, index) => {
        const owned = bought.includes(index);
        const afford = money >= item.price;
        const colour = owned ? OWNED : afford && canBuy ? BUYABLE : OUT_OF_REACH;
        return (
          <div
            key={item.id}
            onClick={() => onBuy(index)}
            style={{ display: 'flex', gap: '0.6rem', color: colour, lineHeight: 1.7 }}
          >
            {/* 1-based, matching the key you press. The index on the wire stays
                0-based; the label is the thing being made friendly. */}
            <span style={{ opacity: 0.6, width: '1.2em' }}>{index + 1}</span>
            <span style={{ flex: 1 }}>{item.name}</span>
            <span>{owned ? 'OWNED' : `$${item.price}`}</span>
          </div>
        );
      })}
    </div>
  );
}
