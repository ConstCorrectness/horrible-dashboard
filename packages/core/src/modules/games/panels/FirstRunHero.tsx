import { useEffect, useState, type ReactNode } from 'react';

import { openDrawer } from '../client-drawer';
import { setSetting } from '../../../settings';
import { useAccount } from '../../../useAccount';
import { useGames } from '../game-ws';
import { openGamesSection } from '../hub-section';
import { findRankedMatch } from '../matchmaking';

type Step = 'placement' | 'done';

/**
 * The first-run hero in the Games pane's Play section — the wizard panel's
 * replacement, and the one surface here allowed to be loud. One inline step now:
 * the placement match, switching to the Game Board section and opening the Games
 * Log so the first thing a new player sees is their agent thinking. Sets
 * `games.onboarded` when the placement match goes live, or when dismissed.
 *
 * **Sign-in used to be step one here, and it had a skip button.** Both are gone: the
 * pane is gated (`GamesSignIn`), so nothing signed-out reaches this component, and
 * the skip led somewhere that did not work — its own copy promised "▶ Play works
 * without one" while every start flow in matchmaking.ts calls `ensureConnected`
 * against a server that refuses anonymous play. That sentence is what taught users
 * to walk into the `invalid token` toast.
 *
 * The headline is doing the teaching: the premise of the whole module (you engineer
 * the agent, you don't play) has to land before the placement match means anything.
 * Display type is sized against the pane's container query, not the viewport — see
 * `.games-hero` in games.css.
 */

// Headline copy per step. The <em> is the italic accent word the line lands on
// (styled by .games-hero-title em); keep it to one word — the emphasis is the point.
const COPY: Record<Step, { title: ReactNode; sub: ReactNode }> = {
  placement: {
    title: (
      <>
        You don't play the games.
        <br />
        You <em>engineer</em> the agent that does.
      </>
    ),
    sub: (
      <>
        Your opponent is another player's harness — the tools they wrote, the context they fed it.
        One match against a practice bot sets your opening rating. The board and your agent's live
        thoughts sit side by side — watch how it reasons before you change a thing.
      </>
    ),
  },
  done: {
    title: (
      <>
        You're <em>in</em>.
      </>
    ),
    sub: (
      <>
        Study the replay, branch your harness, climb. The agent only gets as good as you build it.
      </>
    ),
  },
};
export function FirstRunHero() {
  const { matchSeats } = useGames();
  const { account } = useAccount();
  const [step, setStep] = useState<Step>('placement');
  const [queued, setQueued] = useState(false);
  const name = account?.display_name ?? null;

  // The placement match went live → onboarding is done.
  useEffect(() => {
    if (step === 'placement' && queued && matchSeats) {
      setStep('done');
      void setSetting('games.onboarded', true);
    }
  }, [step, queued, matchSeats]);

  const startPlacement = () => {
    openGamesSection('board');
    openDrawer('log');
    void findRankedMatch('tictactoe', 'standard', true);
    setQueued(true);
  };

  const dismiss = () => void setSetting('games.onboarded', true);

  return (
    <div className="games-hero">
      <div className="games-hero-top">
        <span className="games-eyebrow">New here</span>
        {/* The step dots went with the sign-in step. A progress indicator over a
            single step measures nothing — it just took up the row. */}
        <button
          type="button"
          className="games-ghost-btn"
          style={{ marginLeft: 'auto' }}
          onClick={dismiss}
          title="Hide this — restart via the command palette"
        >
          dismiss
        </button>
      </div>

      <h1 className="games-hero-title">{COPY[step].title}</h1>
      <p className="games-hero-sub">
        {step === 'placement' && name ? `Signed in as ${name}. ` : ''}
        {COPY[step].sub}
      </p>

      {step === 'placement' && (
        <div className="games-hero-actions">
          <button
            type="button"
            className="games-play-btn"
            style={{ flex: '0 0 auto' }}
            onClick={startPlacement}
            disabled={queued}
          >
            {queued ? 'Finding your bot…' : '🏁 Start placement match'}
          </button>
        </div>
      )}

      {step === 'done' && (
        <div className="games-hero-actions">
          <button
            type="button"
            className="games-play-btn"
            style={{ flex: '0 0 auto' }}
            onClick={() => openGamesSection('build')}
          >
            Improve the harness
          </button>
        </div>
      )}
    </div>
  );
}
