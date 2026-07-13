import { useCallback, useEffect, useState } from 'react';

import { registry } from '../../../registry';
import { useGames } from '../game-ws';
import { fetchStatus, signInWith, signOut, type SignInProvider } from '../games-api';
import { setHubSection, useHubSection, type HubSection } from '../hub-section';
import { ChallengesPanel } from './ChallengesPanel';
import { ConnectionChip } from './ConnectionChip';
import { LeaderboardPanel } from './LeaderboardPanel';
import { PlaySection } from './PlaySection';
import { ProfilePanel } from './ProfilePanel';
import { ReplayBrowserPanel } from './ReplayBrowserPanel';
import { RosterPanel } from './RosterPanel';

/** Sign-in status + device-flow sign-in (GitHub or Google — two different Google
 * accounts are two distinct players, handy for testing across machines). Identity
 * lives on the node (the JWT is held server-side); this just reflects and toggles it. */
function SignIn() {
  const { social } = useGames();
  const [name, setName] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<{ code: string; url: string } | null>(null);
  const [busy, setBusy] = useState<SignInProvider | null>(null);
  const [err, setErr] = useState('');

  const refresh = useCallback(() => {
    fetchStatus()
      .then((s) => setName(s.signed_in ? s.display_name : null))
      .catch(() => setName(null));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const signIn = async (provider: SignInProvider) => {
    setBusy(provider);
    setErr('');
    try {
      const display = await signInWith(provider, (code, url) => setPrompt({ code, url }));
      setName(display);
    } catch (e) {
      setErr(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(null);
      setPrompt(null);
    }
  };

  if (name) {
    return (
      <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.78rem' }}>
        {social.profile && (
          <span className="games-level-badge" title={`${social.profile.xp} XP`}>
            {social.profile.avatar} Lv {social.profile.level}
          </span>
        )}
        <span style={{ color: 'var(--text-dim)' }}>
          <strong>{name}</strong>
        </span>
        <button type="button" onClick={() => void signOut().then(() => setName(null))}>
          Sign out
        </button>
      </span>
    );
  }
  return (
    <span style={{ fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
      <button type="button" onClick={() => void signIn('github')} disabled={busy !== null}>
        {busy === 'github' ? 'Signing in…' : 'Sign in'}
      </button>
      <button type="button" onClick={() => void signIn('google')} disabled={busy !== null}>
        {busy === 'google' ? '…' : 'Google'}
      </button>
      {prompt && (
        <span style={{ color: 'var(--text-dim)' }}>
          code <strong>{prompt.code}</strong> at{' '}
          <a href={prompt.url} target="_blank" rel="noreferrer">
            {prompt.url}
          </a>
        </span>
      )}
      {err && <span style={{ color: 'var(--danger, #e5534b)' }}>{err}</span>}
    </span>
  );
}

const SECTIONS: [HubSection, string][] = [
  ['play', 'Play'],
  ['ladder', 'Ladder'],
  ['challenges', 'Challenges'],
  ['replays', 'Replays'],
  ['players', 'Players'],
  ['profile', 'Profile'],
];

/**
 * The Games hub — the module's single entry point. One pane, internal tabs:
 * Play (matchmaking) plus Ladder / Challenges / Replays / Players / Profile
 * folded in as sections. Connection is implicit (see matchmaking.ts); the only
 * connection UI left is the status chip.
 */
export function LobbyPanel() {
  const section = useHubSection();

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        height: '100%',
        overflow: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
        <ConnectionChip />
        <SignIn />
        {/* The Plaza + town auto-connect the node, so they're reachable either way. */}
        <button
          type="button"
          style={{ marginLeft: 'auto' }}
          title="The Plaza — hang out with real players, chat, and challenge them"
          onClick={() => registry.openPanel('games.plaza')}
        >
          🏛 Plaza
        </button>
        <button
          type="button"
          title="AgentTown — spawn your agent in the social fish tank"
          onClick={() => registry.openPanel('games.town')}
        >
          🏘 AgentTown
        </button>
      </div>

      <div className="games-hub-tabs">
        {SECTIONS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={section === id ? 'games-hub-tab active' : 'games-hub-tab'}
            onClick={() => setHubSection(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {section === 'play' && <PlaySection />}
      {section === 'ladder' && <LeaderboardPanel />}
      {section === 'challenges' && <ChallengesPanel />}
      {section === 'replays' && <ReplayBrowserPanel />}
      {section === 'players' && <RosterPanel />}
      {section === 'profile' && <ProfilePanel />}
    </div>
  );
}
