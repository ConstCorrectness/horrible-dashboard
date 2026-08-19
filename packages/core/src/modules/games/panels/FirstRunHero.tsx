import { useEffect, useState, type FormEvent, type ReactNode } from 'react';

import { openDrawer } from '../client-drawer';
import { setSetting } from '../../../settings';
import { useAccount } from '../../../useAccount';
import { useGames } from '../game-ws';
import { openGamesSection } from '../hub-section';
import { startPlacement as beginPlacement } from '../matchmaking';
import { apiPost } from '../../../api';
import { patchProfile } from '../profile-api';

type Step = 'username' | 'placement' | 'done';

const COPY: Record<Step, { title: ReactNode; sub: ReactNode }> = {
  username: {
    title: (
      <>
        Choose your <em>Username</em>.
      </>
    ),
    sub: (
      <>
        Your username (@handle) is how friends find you across the ladder, the peer fabric, and in
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
  const [step, setStep] = useState<Step>(account?.handle ? 'placement' : 'username');
  const [queued, setQueued] = useState(false);
  const [handleInput, setHandleInput] = useState(
    account?.handle?.replace(/^@/, '') || account?.suggested_handle || '',
  );
  const [displayNameInput, setDisplayNameInput] = useState(account?.display_name || '');
  const [savingUsername, setSavingUsername] = useState(false);
  const [usernameError, setUsernameError] = useState<string | null>(null);

  const name = account?.display_name || account?.handle || null;

  // The placement match went live → onboarding is done.
  useEffect(() => {
    if (step === 'placement' && queued && matchSeats) {
      setStep('done');
      void setSetting('games.onboarded', true);
    }
  }, [step, queued, matchSeats]);

  const handleSaveUsername = async (e: FormEvent) => {
    e.preventDefault();
    const raw = handleInput.trim().replace(/^@/, '').toLowerCase();
    if (raw.length < 3 || raw.length > 20 || !/^[a-z0-9_-]+$/.test(raw)) {
      setUsernameError('Username must be 3–20 lowercase alphanumeric characters, dashes or underscores.');
      return;
    }
    setSavingUsername(true);
    setUsernameError(null);
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
      setUsernameError(err instanceof Error ? err.message : 'Could not register username');
    } finally {
      setSavingUsername(false);
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
          {step === 'username' ? '1. Identity' : step === 'placement' ? '2. Placement' : 'Ready'}
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

      {/* Step 1: Choose Username & Profile */}
      {step === 'username' && (
        <form onSubmit={handleSaveUsername} style={{ marginTop: '0.85rem', maxWidth: 420 }}>
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
            {usernameError && (
              <div style={{ color: 'var(--danger, #f85149)', fontSize: 12 }}>{usernameError}</div>
            )}
            <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.2rem' }}>
              <button
                type="submit"
                className="games-play-btn"
                disabled={savingUsername || !handleInput.trim()}
              >
                {savingUsername ? 'Saving…' : 'Confirm Username & Continue →'}
              </button>
              {/* No "Skip for now". A username is not a preference this step is
                  collecting — it is the identity the ladder, the killfeed, the
                  friends roster and `@handle` resolution all key on, and skipping
                  it produced an account that could play but could not be found or
                  added by anyone. The step is short precisely so it can be
                  required. */}
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
            onClick={() => setStep('username')}
          >
            Edit Username
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
