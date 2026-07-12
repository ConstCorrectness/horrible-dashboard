import { useEffect, useState } from 'react';

import { apiGet, apiPost, apiPut } from '../../../api';
import { setSetting } from '../../../settings';
import { revealRegionView } from '../../../layout/controller';
import { registry } from '../../../registry';
import { gamesConnect, gamesQueueJoin, profileGet, profileSet, useGames } from '../game-ws';
import { fetchStatus, signInWith, type SignInProvider } from '../games-api';

const AVATARS = ['🤖', '🦾', '🧠', '👾', '🐙', '🦊', '🐲', '⚡', '🛠', '🎯', '🃏', '🚀'];

interface Template {
  id: string;
  game_id: string;
  title: string;
  blurb: string;
  loadout: Record<string, unknown>;
}

type Step = 'signin' | 'identity' | 'loadout' | 'placement' | 'done';

/**
 * First-run onboarding: OAuth in → claim a handle + avatar → ship a first
 * harness from a template → placement match against a practice bot, with the
 * board and the Agent Thoughts pane side by side so the very first thing a new
 * player sees is their agent thinking.
 */
export function OnboardingPanel() {
  const { connected, social, matchSeats } = useGames();
  const [step, setStep] = useState<Step>('signin');
  const [name, setName] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<{ code: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [handle, setHandle] = useState('');
  const [avatar, setAvatar] = useState('🤖');
  const [templates, setTemplates] = useState<Template[]>([]);
  const [picked, setPicked] = useState<Template | null>(null);
  const [queued, setQueued] = useState(false);

  useEffect(() => {
    fetchStatus()
      .then((s) => {
        if (s.signed_in) {
          setName(s.display_name);
          setStep('identity');
        }
      })
      .catch(() => undefined);
    apiGet<{ templates: Template[] }>('/games/loadout-templates')
      .then((r) => setTemplates(r.templates))
      .catch(() => setTemplates([]));
  }, []);

  // The placement match went live → the wizard's job is done.
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
      setStep('identity');
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
      setPrompt(null);
    }
  };

  const claimIdentity = () => {
    if (!connected) gamesConnect(false);
    // Give the connection a beat, then set profile (the node auto-connects too).
    setTimeout(() => {
      profileSet(avatar, undefined, handle || undefined);
      profileGet();
    }, 600);
    setStep('loadout');
  };

  const shipLoadout = async () => {
    if (picked) {
      await apiPut(`/games/loadout/${picked.game_id}`, picked.loadout);
      await apiPost(`/games/loadout/${picked.game_id}/versions`, {
        label: 'starter',
        loadout: picked.loadout,
      }).catch(() => undefined);
    }
    setStep('placement');
  };

  const startPlacement = () => {
    if (!connected) gamesConnect(false);
    revealRegionView('games.board');
    revealRegionView('games.thoughts');
    gamesQueueJoin(picked?.game_id ?? 'tictactoe', 'standard', true);
    setQueued(true);
  };

  const stepDot = (n: number, active: boolean, done: boolean) => (
    <span className={`games-onboard-dot${active ? ' active' : ''}${done ? ' done' : ''}`}>
      {done ? '✓' : n}
    </span>
  );
  const order: Step[] = ['signin', 'identity', 'loadout', 'placement'];
  const idx = order.indexOf(step === 'done' ? 'placement' : step);

  return (
    <div className="games-onboard">
      <h2 style={{ margin: 0 }}>🕹 Welcome to the Arena</h2>
      <p style={{ color: 'var(--text-dim)', marginTop: '0.2rem' }}>
        You don't play the games — you <strong>engineer the agent</strong> that plays them. Four
        steps and your agent is on the ladder.
      </p>
      <div className="games-onboard-steps">
        {order.map((s, i) => (
          <span key={s} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
            {stepDot(i + 1, i === idx && step !== 'done', i < idx || step === 'done')}
            <span style={{ color: i === idx ? 'inherit' : 'var(--text-dim)' }}>
              {['Sign in', 'Identity', 'First harness', 'Placement'][i]}
            </span>
            {i < order.length - 1 && <span style={{ color: 'var(--text-dim)' }}>→</span>}
          </span>
        ))}
      </div>

      {step === 'signin' && (
        <div className="games-onboard-card">
          <p>
            Sign in with GitHub to get your account on the central game server — it holds your
            ratings, replays, and friends.
          </p>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
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
          </div>
          {prompt && (
            <p>
              Enter code <strong>{prompt.code}</strong> at{' '}
              <a href={prompt.url} target="_blank" rel="noreferrer">
                {prompt.url}
              </a>
            </p>
          )}
          {err && <p style={{ color: 'var(--danger, #e5534b)' }}>{err}</p>}
          <p style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
            Just exploring? Local self-play works without an account — close this and hit Connect
            (self-play) in the lobby. Ranked play needs the account.
          </p>
        </div>
      )}

      {step === 'identity' && (
        <div className="games-onboard-card">
          <p>
            Signed in as <strong>{name}</strong>. Pick how the arena sees you:
          </p>
          <label>
            handle{' '}
            <input
              value={handle}
              onChange={(e) => setHandle(e.target.value.toLowerCase())}
              placeholder="3-20 chars: a-z 0-9 _ -"
              style={{ fontFamily: 'monospace' }}
            />
          </label>
          <div className="games-onboard-avatars">
            {AVATARS.map((a) => (
              <button
                key={a}
                type="button"
                className={a === avatar ? 'games-avatar-pick active' : 'games-avatar-pick'}
                onClick={() => setAvatar(a)}
              >
                {a}
              </button>
            ))}
          </div>
          <button type="button" className="games-play-btn" onClick={claimIdentity}>
            That's me →
          </button>
        </div>
      )}

      {step === 'loadout' && (
        <div className="games-onboard-card">
          <p>
            Your agent plays with the <strong>harness</strong> you build: a strategy prompt plus
            real Python tools it can call mid-game. Start from a template:
          </p>
          <div className="games-onboard-templates">
            {templates.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`games-onboard-template${picked?.id === t.id ? ' active' : ''}`}
                onClick={() => setPicked(t)}
              >
                <strong>{t.title}</strong>
                <span style={{ color: 'var(--text-dim)' }}>{t.game_id}</span>
                <span>{t.blurb}</span>
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button
              type="button"
              className="games-play-btn"
              disabled={!picked}
              onClick={() => void shipLoadout()}
            >
              Ship it →
            </button>
            <button
              type="button"
              onClick={() => {
                registry.openPanel('games.loadout');
              }}
            >
              or open the full harness editor
            </button>
            <button type="button" onClick={() => setStep('placement')}>
              skip
            </button>
          </div>
        </div>
      )}

      {step === 'placement' && (
        <div className="games-onboard-card">
          <p>
            Time for your first <strong>placement match</strong> — instantly paired against a
            practice bot. Watch the board and your agent's live thoughts side by side.
          </p>
          <button
            type="button"
            className="games-play-btn"
            onClick={startPlacement}
            disabled={queued}
          >
            {queued ? 'Finding your bot…' : '🏁 Start placement match'}
          </button>
          <p style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
            Five placement games set your starting tier. Set <code>games.policy</code> to{' '}
            <code>agent</code> in Settings so a model (not the random fallback) drives your seat.
          </p>
        </div>
      )}

      {step === 'done' && (
        <div className="games-onboard-card">
          <p>
            🎉 <strong>You're in.</strong> The match is live — the board popped and your agent's
            thoughts are streaming. After the game: study the replay, branch your harness (Save as
            new version), and climb.
          </p>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="button" onClick={() => registry.openPanel('games.lobby')}>
              Open the lobby
            </button>
            <button type="button" onClick={() => registry.openPanel('games.loadout')}>
              Improve the harness
            </button>
          </div>
        </div>
      )}

      {social.profile && step !== 'signin' && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
          {social.profile.avatar} {social.profile.handle ?? social.profile.account_id} · Lv{' '}
          {social.profile.level}
        </div>
      )}
    </div>
  );
}
