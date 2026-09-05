import { useCallback, useContext, useEffect, useRef, useState } from 'react';

import { PaneInstanceContext } from '../../../agent-context';
import { useCapture } from '../../../keymap';
import { toastsStore } from '../../../toasts';
import { arcadeInput, ensureConnected, startArcadeFighter, useGames } from '../game-ws';
import { FighterCanvas } from './FighterCanvas';

const KEYS = ['a', 'd', 'w', 's', 'u', 'j', 'k'];

/**
 * The Plaza arcade cabinet: a human-played, unrated fighter. Held keys stream to
 * the node (which answers each tick instantly), so you actually play with the
 * keyboard — separate from the ranked bot-fighter ladder. Controls: A/D move,
 * W jump, S block, U light, J heavy, K special.
 *
 * The New-match button connects the node itself. This pane used to gate the button
 * on `connected` and never call `ensureConnected`, which made it the one play flow
 * in the module that could not start a match: opened fresh from the start menu the
 * button was permanently disabled, and it only appeared to work if the Games lobby
 * happened to have connected earlier in the same session. Every other entry point
 * (`matchmaking.ts`, `LobbyPanel`, `ChallengesPanel`) connects on demand — see the
 * note above `ensureConnected` in game-ws.ts: the UI never shows Connect buttons.
 */
export function FighterArcadePanel() {
  const { board, over } = useGames();
  const held = useRef<Set<string>>(new Set());
  const [focused, setFocused] = useState(false);
  const [starting, setStarting] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // A game wants the keyboard, so it takes capture and the shell suppresses its own
  // bindings (`t`, `n`, `mod+1..9`) rather than the pane racing them. Escape gives
  // it back — the arcade has no menu of its own to spend it on.
  const capture = useCapture({
    mode: 'keyboard',
    escape: 'release',
    instanceId: useContext(PaneInstanceContext),
    viewId: 'games.arcade',
    onRelease: () => setFocused(false),
  });
  const requestCapture = capture.request;
  const releaseCapture = capture.release;

  useEffect(() => {
    const el = boxRef.current;
    if (!el || !focused) return;
    const push = () => arcadeInput([...held.current]);
    const down = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if (!KEYS.includes(k)) return;
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
    // Bound to the pane, not to `window`. On the window these fired for the whole
    // app while this pane held focus, and the `keyup` handler ran even when it did
    // not — so a key released elsewhere still reached the arcade.
    el.addEventListener('keydown', down);
    el.addEventListener('keyup', up);
    return () => {
      el.removeEventListener('keydown', down);
      el.removeEventListener('keyup', up);
    };
  }, [focused]);

  const start = useCallback(async () => {
    setStarting(true);
    try {
      // `true` = self-play: this node holds both seats, which is what the button
      // has always said it does and what makes a human-vs-itself bout possible.
      await ensureConnected(true);
      startArcadeFighter();
      boxRef.current?.focus();
    } catch (err) {
      toastsStore.add('error', 'Arcade', err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }, []);

  const isFighter = board?.game === 'fighter';

  return (
    <div
      ref={boxRef}
      tabIndex={0}
      onFocus={() => {
        setFocused(true);
        requestCapture();
      }}
      onBlur={() => {
        setFocused(false);
        releaseCapture();
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
        <strong>Arcade Fighter</strong>
        <button type="button" onClick={() => void start()} disabled={starting}>
          {starting ? 'Connecting…' : 'New match (self-play)'}
        </button>
        <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
          A/D move · W jump · S block · U/J/K attacks
        </span>
      </div>
      {isFighter && !focused && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem', marginBottom: '0.3rem' }}>
          Click here to capture the keyboard.
        </div>
      )}
      {isFighter ? (
        <>
          <FighterCanvas board={board} />
          {over && (
            <div style={{ textAlign: 'center', fontWeight: 800 }}>
              {over.winner === null ? 'Draw' : `${over.winner === 0 ? 'Blue' : 'Orange'} wins!`}
            </div>
          )}
        </>
      ) : (
        <div style={{ color: 'var(--text-dim)' }}>
          Hit “New match” for a self-play bout, or challenge a Plaza occupant to a fighter (unrated)
          for a human-vs-human one.
        </div>
      )}
    </div>
  );
}
