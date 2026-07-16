import { useEffect, useState } from 'react';

import { registry } from '../../../registry';
import { setSetting } from '../../../settings';
import { useGames } from '../game-ws';
import { fetchStatus, signInWith, type SignInProvider } from '../games-api';
import { openGamesSection } from '../hub-section';
import { findRankedMatch } from '../matchmaking';

type Step = 'signin' | 'placement' | 'done';

/**
 * The first-run card in the Games pane's Play section — the wizard panel's
 * replacement. Two inline steps: sign in → placement match, switching to the Game
 * Board section and opening the Games Log so the first thing a new player sees is
 * their agent thinking. Sets `games.onboarded` when the placement match goes live,
 * or when dismissed.
 */
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
    <div className="games-start-hero" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <strong>🚀 New here?</strong>
        <span style={{ color: 'var(--text-dim)' }}>
          You don't play the games — you <strong>engineer the agent</strong> that plays them.
        </span>
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
        <button type="button" onClick={dismiss} title="Hide this — restart via the command palette">
          dismiss
        </button>
      </div>

      {step === 'signin' && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ color: 'var(--text-dim)' }}>
            An account holds your ratings, replays, and friends (▶ Play works without one):
          </span>
          <button
            type="button"
            className="games-play-btn"
            onClick={() => void signIn('github')}
            disabled={busy}
          >
            {busy ? 'Signing in…' : '🐙 Sign in with GitHub'}
          </button>
          <button type="button" onClick={() => void signIn('google')} disabled={busy}>
            Google instead
          </button>
          <button type="button" onClick={() => setStep('placement')}>
            skip
          </button>
          {prompt && (
            <span>
              Enter code <strong>{prompt.code}</strong> at{' '}
              <a href={prompt.url} target="_blank" rel="noreferrer">
                {prompt.url}
              </a>
            </span>
          )}
        </div>
      )}

      {step === 'placement' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--text-dim)' }}>
            {name ? `Signed in as ${name}. ` : ''}Start your <strong>placement match</strong> —
            instantly paired against a practice bot, board and your agent's live thoughts side by
            side:
          </span>
          <button
            type="button"
            className="games-play-btn"
            onClick={startPlacement}
            disabled={queued}
          >
            {queued ? 'Finding your bot…' : '🏁 Start placement match'}
          </button>
        </div>
      )}

      {step === 'done' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span>
            🎉 <strong>You're in.</strong> After the game: study the replay, branch your harness,
            and climb.
          </span>
          <button type="button" onClick={() => openGamesSection('build')}>
            Improve the harness
          </button>
        </div>
      )}

      {err && <div style={{ color: 'var(--danger, #e5534b)' }}>{err}</div>}
    </div>
  );
}
