import { useEffect, useRef, useState } from 'react';

import { arcadeInput, startArcadeFighter, useGames } from '../game-ws';
import { FighterCanvas } from './FighterCanvas';

const KEYS = ['a', 'd', 'w', 's', 'u', 'j', 'k'];

/**
 * The Plaza arcade cabinet: a human-played, unrated fighter. Held keys stream to
 * the node (which answers each tick instantly), so you actually play with the
 * keyboard — separate from the ranked bot-fighter ladder. Controls: A/D move,
 * W jump, S block, U light, J heavy, K special.
 */
export function FighterArcadePanel() {
  const { board, over, connected } = useGames();
  const held = useRef<Set<string>>(new Set());
  const [focused, setFocused] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const push = () => arcadeInput([...held.current]);
    const down = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if (!KEYS.includes(k) || !focused) return;
      e.preventDefault();
      if (!held.current.has(k)) {
        held.current.add(k);
        push();
      }
    };
    const up = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if (!KEYS.includes(k)) return;
      if (held.current.delete(k)) push();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, [focused]);

  const isFighter = board?.game === 'fighter';

  return (
    <div
      ref={boxRef}
      tabIndex={0}
      onFocus={() => setFocused(true)}
      onBlur={() => {
        setFocused(false);
        held.current.clear();
        arcadeInput([]);
      }}
      style={{
        padding: '0.6rem',
        outline: focused ? '2px solid var(--accent, #6ea8fe)' : 'none',
        height: '100%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.4rem' }}>
        <strong>🕹 Arcade Fighter</strong>
        <button type="button" onClick={() => startArcadeFighter()} disabled={!connected}>
          New match (self-play)
        </button>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
          A/D move · W jump · S block · U/J/K attacks
        </span>
      </div>
      {!focused && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem', marginBottom: '0.3rem' }}>
          Click here to capture the keyboard.
        </div>
      )}
      {isFighter ? (
        <>
          <FighterCanvas board={board} />
          {over && (
            <div style={{ textAlign: 'center', fontWeight: 800 }}>
              {over.winner === null
                ? '🤝 Draw'
                : `🏆 ${over.winner === 0 ? 'Blue' : 'Orange'} wins!`}
            </div>
          )}
        </>
      ) : (
        <div style={{ color: 'var(--text-dim)' }}>
          Start a match to play. Challenge a Plaza occupant to a fighter (unrated) for a
          human-vs-human bout, or hit “New match” for self-play.
        </div>
      )}
    </div>
  );
}
