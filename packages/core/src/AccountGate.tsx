import { useState, type FormEvent } from 'react';

import { setUsername } from './account';
import { refreshAccount } from './account-store';
import { SignInCard } from './SignInCard';
import { useAccount } from './useAccount';
import './signin.css';

/** The server's charset (`store.HANDLE_RE`), for pre-filling only — it decides. */
function suggest(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '')
    .slice(0, 20);
}

/**
 * Whatever this account still needs before people can add it: a sign-in, then a
 * username. Renders nothing once both are done.
 *
 * A surface that only *says* "sign in to get a username" is a dead end. The friends
 * list said exactly that, including to an account that was already signed in and
 * only lacked the name, with no control on the page for either step. An OAuth
 * account arrives with no username by design, so this state is common.
 */
export function AccountGate({ onDone }: { onDone?: () => void }) {
  const { account, signedIn, phase } = useAccount();
  const [value, setValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  if (phase === 'loading') return null;
  // Unreachable is not signed out: a sign-in form here would send a signed-in
  // person through sign-in again because the backend was still starting.
  if (phase === 'unavailable' && !account) {
    return (
      <p className="signin-note">
        Can&rsquo;t reach this computer&rsquo;s backend to check your account.{' '}
        <button type="button" className="signin-link-btn" onClick={() => void refreshAccount()}>
          Retry
        </button>
      </p>
    );
  }
  if (!signedIn) {
    return (
      <SignInCard
        intro={<p className="signin-note">Sign in or create an account to get a username.</p>}
        onSignedIn={onDone}
      />
    );
  }
  if (account?.handle) return null;

  const current = value ?? suggest(account?.display_name ?? '');
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setErr('');
    try {
      await setUsername(current.trim());
      await refreshAccount();
      onDone?.();
    } catch (e) {
      // Uniqueness and the charset are the server's; show its reason verbatim.
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="signin-card">
      <p className="signin-note">
        Signed in as {account?.display_name ?? 'you'}. Choose a username: it is unique, and it is
        how people add you.
      </p>
      <form className="signin-form" onSubmit={(e) => void submit(e)}>
        <input
          type="text"
          required
          minLength={3}
          maxLength={20}
          pattern="[A-Za-z0-9_-]{3,20}"
          placeholder="username"
          aria-label="Username"
          title="3–20 characters: a–z, 0–9, - or _"
          autoComplete="username"
          spellCheck={false}
          value={current}
          onChange={(e) => setValue(e.target.value)}
        />
        <button className="signin-submit" type="submit" disabled={busy}>
          {busy ? 'Claiming…' : 'Claim username'}
        </button>
      </form>
      {err && <p className="signin-note error">{err}</p>}
    </div>
  );
}
