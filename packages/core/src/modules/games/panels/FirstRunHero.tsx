import { useEffect, useState, type FormEvent, type ReactNode } from 'react';

import { openDrawer } from '../client-drawer';
import { setSetting } from '../../../settings';
import { useAccount } from '../../../useAccount';
import { useGames } from '../game-ws';
import { openGamesSection } from '../hub-section';
import { startPlacement as beginPlacement } from '../matchmaking';
import { apiPost } from '../../../api';
import { patchProfile } from '../profile-api';

type Step = 'callsign' | 'placement' | 'done';

const COPY: Record<Step, { title: ReactNode; sub: ReactNode }> = {
  callsign: {
    title: (
      <>
        Choose your <em>Callsign</em>.
      </>
    ),
    sub: (
      <>
        Your callsign (@handle) is how friends find you across the ladder, the peer fabric, and in
        AgentTown. Pick a unique username to register your client.
      </>
    ),
  },
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
        Your client identity is registered. Observe your agent living, working, and socializing in
        AgentTown, or climb the competitive matchmaking ladder.
      </>
    ),
  },
};

export function FirstRunHero() {
  const { matchSeats } = useGames();
  const { account, refresh } = useAccount();
  const [step, setStep] = useState<Step>(account?.handle ? 'placement' : 'callsign');
  const [queued, setQueued] = useState(false);
  const [handleInput, setHandleInput] = useState(account?.handle?.replace(/^@/, '') || '');
  const [displayNameInput, setDisplayNameInput] = useState(account?.display_name || '');
  const [savingCallsign, setSavingCallsign] = useState(false);
  const [callsignError, setCallsignError] = useState<string | null>(null);

  const name = account?.display_name || account?.handle || null;

  // The placement match went live → onboarding is done.
  useEffect(() => {
    if (step === 'placement' && queued && matchSeats) {
      setStep('done');
      void setSetting('games.onboarded', true);
    }
  }, [step, queued, matchSeats]);

  const handleSaveCallsign = async (e: FormEvent) => {
    e.preventDefault();
    const raw = handleInput.trim().replace(/^@/, '').toLowerCase();
    if (raw.length < 3 || raw.length > 20 || !/^[a-z0-9_-]+$/.test(raw)) {
      setCallsignError('Callsign must be 3–20 lowercase alphanumeric characters, dashes or underscores.');
      return;
    }
    setSavingCallsign(true);
    setCallsignError(null);
    try {
      // 1. Update display name in local social identity
      if (displayNameInput.trim()) {
        await apiPost('/api/social/me', {
          display_name: displayNameInput.trim(),
          avatar: '🤖',
        }).catch(() => {});
      }
      // 2. Patch game profile
      await patchProfile({
        display_name: displayNameInput.trim() || raw,
        status_text: 'Living in AgentTown 🏛',
      }).catch(() => {});
      // 3. Bind handle with person key
      await apiPost('/api/social/handle/bind', {}).catch(() => {});
      refresh();
      setStep('placement');
    } catch (err) {
      setCallsignError(err instanceof Error ? err.message : 'Could not register callsign');
    } finally {
      setSavingCallsign(false);
    }
  };

  const startPlacement = () => {
    openGamesSection('board');
    openDrawer('log');
    void beginPlacement('tictactoe');
    setQueued(true);
  };

  const dismiss = () => void setSetting('games.onboarded', true);

  return (
    <div className="games-hero">
      <div className="games-hero-top">
        <span className="games-eyebrow">
          {step === 'callsign' ? '1. Identity' : step === 'placement' ? '2. Placement' : 'Ready'}
        </span>
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
        {step === 'placement' && name ? `Registered as ${name}. ` : ''}
        {COPY[step].sub}
      </p>

      {/* Step 1: Choose Callsign & Profile */}
      {step === 'callsign' && (
        <form onSubmit={handleSaveCallsign} style={{ marginTop: '0.85rem', maxWidth: 420 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <span style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--accent)' }}>@</span>
              <input
                type="text"
                value={handleInput}
                onChange={(e) => setHandleInput(e.target.value)}
                placeholder="username"
                autoFocus
                style={{
                  flex: 1,
                  background: 'var(--bg-tertiary, #161b22)',
                  color: 'var(--text-primary, #c9d1d9)',
                  border: '1px solid var(--border-dim, #30363d)',
                  borderRadius: 6,
                  padding: '7px 10px',
                  fontSize: 13,
                  outline: 'none',
                }}
              />
            </div>
            <input
              type="text"
              value={displayNameInput}
              onChange={(e) => setDisplayNameInput(e.target.value)}
              placeholder="Display Name (e.g. Alice)"
              style={{
                background: 'var(--bg-tertiary, #161b22)',
                color: 'var(--text-primary, #c9d1d9)',
                border: '1px solid var(--border-dim, #30363d)',
                borderRadius: 6,
                padding: '7px 10px',
                fontSize: 13,
                outline: 'none',
              }}
            />
            {callsignError && (
              <div style={{ color: 'var(--danger, #f85149)', fontSize: 12 }}>{callsignError}</div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.2rem' }}>
              <button
                type="submit"
                className="games-play-btn"
                disabled={savingCallsign || !handleInput.trim()}
              >
                {savingCallsign ? 'Saving…' : 'Confirm Callsign & Continue →'}
              </button>
              <button
                type="button"
                className="games-ghost-btn"
                onClick={() => setStep('placement')}
              >
                Skip for now
              </button>
            </div>
          </div>
        </form>
      )}

      {/* Step 2: Placement Match */}
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
          <button
            type="button"
            className="games-ghost-btn"
            onClick={() => setStep('callsign')}
          >
            Edit Callsign
          </button>
        </div>
      )}

      {/* Step 3: Done */}
      {step === 'done' && (
        <div className="games-hero-actions" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="games-play-btn"
            style={{ flex: '0 0 auto' }}
            onClick={() => openGamesSection('social')}
          >
            🏛 Visit AgentTown
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ flex: '0 0 auto' }}
            onClick={() => openGamesSection('build')}
          >
            🛠 Improve the harness
          </button>
        </div>
      )}
    </div>
  );
}
