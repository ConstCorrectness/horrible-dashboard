import { useEffect, useState, type ReactNode } from 'react';

import { registry } from '../../../registry';
import { setSetting } from '../../../settings';
import { useGames } from '../game-ws';
import { fetchStatus, signInWith, type SignInProvider } from '../games-api';
import { openGamesSection } from '../hub-section';
import { findRankedMatch } from '../matchmaking';

type Step = 'signin' | 'placement' | 'done';

/**
 * The first-run hero in the Games pane's Play section — the wizard panel's
 * replacement, and the one surface here allowed to be loud. Two inline steps: sign
 * in → placement match, switching to the Game Board section and opening the Games
 * Log so the first thing a new player sees is their agent thinking. Sets
 * `games.onboarded` when the placement match goes live, or when dismissed.
 *
 * Each step gets its own headline, because the headline is doing the teaching: the
 * premise of the whole module (you engineer the agent, you don't play) has to land
 * before the sign-in button means anything. Display type is sized against the pane's
 * container query, not the viewport — see `.games-hero` in games.css.
 */

// Headline copy per step. The <em> is the italic accent word the line lands on
// (styled by .games-hero-title em); keep it to one word — the emphasis is the point.
const COPY: Record<Step, { title: ReactNode; sub: ReactNode }> = {
  signin: {
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
        An account holds your ratings, replays, and friends, but ▶ Play works without one.
      </>
    ),
  },
  placement: {
    title: (
      <>
        Time to <em>place</em> you.
      </>
    ),
    sub: (
      <>
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
  const [step, setStep] = useState<Step>('signin');
  const [name, setName] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<{ code: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [queued, setQueued] = useState(false);

  useEffect(() => {
    fetchStatus()
      .then((s) => {
        if (s.signed_in) {
          setName(s.display_name);
          setStep('placement');
        }
      })
      .catch(() => undefined);
  }, []);

  // The placement match went live → onboarding is done.
  useEffect(() => {
    if (step === 'placement' && queued && matchSeats) {
      setStep('done');
      void setSetting('games.onboarded', true);
    }
  }, [step, queued, matchSeats]);

  const signIn = async (provider: SignInProvider) => {
    setBusy(true);
    setErr('');
    try {
      const display = await signInWith(provider, (code, url) => setPrompt({ code, url }));
      setName(display);
      setStep('placement');
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
      setPrompt(null);
    }
  };

  const startPlacement = () => {
    openGamesSection('board');
    registry.openPanel('games.log');
    void findRankedMatch('tictactoe', 'standard', true);
    setQueued(true);
  };

  const dismiss = () => void setSetting('games.onboarded', true);

  const steps: [Step, string][] = [
    ['signin', 'Sign in'],
    ['placement', 'Placement match'],
  ];
  const idx = steps.findIndex(([s]) => s === (step === 'done' ? 'placement' : step));

  return (
    <div className="games-hero">
      <div className="games-hero-top">
        <span className="games-eyebrow">New here</span>
        <span className="games-onboard-steps" style={{ marginLeft: 'auto' }}>
          {steps.map(([s, label], i) => (
            <span key={s} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
              <span
                className={`games-onboard-dot${i === idx && step !== 'done' ? ' active' : ''}${
                  i < idx || step === 'done' ? ' done' : ''
                }`}
              >
                {i < idx || step === 'done' ? '✓' : i + 1}
              </span>
              <span style={{ color: i === idx ? 'inherit' : 'var(--text-dim)' }}>{label}</span>
              {i < steps.length - 1 && <span style={{ color: 'var(--text-dim)' }}>→</span>}
            </span>
          ))}
        </span>
        <button
          type="button"
          className="games-ghost-btn"
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

      {step === 'signin' && (
        <div className="games-hero-actions">
          <button
            type="button"
            className="games-play-btn"
            style={{ flex: '0 0 auto' }}
            onClick={() => void signIn('github')}
            disabled={busy}
          >
            {busy ? 'Signing in…' : '🐙 Sign in with GitHub'}
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            onClick={() => void signIn('google')}
            disabled={busy}
          >
            Google instead
          </button>
          <button type="button" className="games-ghost-btn" onClick={() => setStep('placement')}>
            skip
          </button>
          {prompt && (
            <span className="games-hero-note">
              Enter code <strong>{prompt.code}</strong> at{' '}
              <a href={prompt.url} target="_blank" rel="noreferrer">
                {prompt.url}
              </a>
            </span>
          )}
        </div>
      )}

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

      {err && <div style={{ color: 'var(--danger, #e5534b)' }}>{err}</div>}
    </div>
  );
}
