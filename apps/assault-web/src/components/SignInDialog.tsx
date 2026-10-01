import { useEffect, useRef, useState, type FormEvent } from 'react';
import { GITHUB_MARK, GOOGLE_MARK } from '@horrible/core';
import {
  claimHandle,
  fetchProviders,
  signInWithPassword,
  signInWithProvider,
  signUpWithPassword,
  useAuth,
  type Provider,
  type ProviderFlows,
} from '../auth';

type Step = 'in' | 'up' | 'handle';

/**
 * Sign in, create an account, or — for an account that has none yet — choose the
 * username that will show on the scoreboard.
 *
 * The username step is part of signing in, not a settings page: an OAuth account
 * arrives without one on purpose (a provider login is a suggestion, not a claim),
 * and until it has one the match socket keeps playing it as the guest callsign.
 */
export function SignInDialog({
  initialStep = 'in',
  onClose,
}: {
  initialStep?: Step;
  onClose: () => void;
}) {
  const { account } = useAuth();
  const [step, setStep] = useState<Step>(initialStep);
  const [providers, setProviders] = useState<ProviderFlows>({});
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [username, setUsername] = useState('');
  const [handle, setHandle] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void fetchProviders().then(setProviders);
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Signed in without a username: move to choosing one, pre-filled with the
  // server's suggestion (which is not reserved — it can still be taken).
  const after = (handleNow: string | null | undefined, suggested?: string | null) => {
    if (handleNow) {
      onClose();
      return;
    }
    setHandle(suggested ?? '');
    setStep('handle');
  };

  useEffect(() => {
    if (step === 'handle' && !handle && account?.suggested_handle)
      setHandle(account.suggested_handle);
  }, [step, handle, account]);

  const run = async (key: string, task: () => Promise<void>) => {
    setBusy(key);
    setError('');
    try {
      await task();
    } catch (err) {
      if ((err as Error)?.name !== 'AbortError') setError((err as Error)?.message || String(err));
    } finally {
      setBusy(null);
    }
  };

  const oauth = (provider: Provider) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    void run(provider, async () => {
      const acct = await signInWithProvider(provider, controller.signal);
      after(acct.handle, acct.suggested_handle);
    });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (step === 'handle') {
      if (!/^[a-z0-9_-]{3,20}$/.test(handle)) {
        setError('A username is 3–20 characters: a–z, 0–9, - or _.');
        return;
      }
      void run('handle', async () => {
        await claimHandle(handle);
        onClose();
      });
      return;
    }
    if (!email.includes('@')) {
      setError('Enter the email address for your account.');
      return;
    }
    if (step === 'in' && !password) {
      setError('Enter your password.');
      return;
    }
    // MIN_PASSWORD_LEN and HANDLE_RE in backend/games_server — checked here only
    // to answer before a round trip; the server is the authority.
    if (step === 'up' && password.length < 8) {
      setError('Passwords are at least 8 characters.');
      return;
    }
    if (step === 'up' && !/^[a-z0-9_-]{3,20}$/.test(username)) {
      setError('A username is 3–20 characters: a–z, 0–9, - or _.');
      return;
    }
    void run('password', async () => {
      const acct =
        step === 'up'
          ? await signUpWithPassword(email, password, username)
          : await signInWithPassword(email, password);
      after(acct.handle, acct.suggested_handle);
    });
  };

  // Only greyed when the server *positively* says the flow isn't configured; an
  // older server that can't say keeps the button live.
  const webOff = (p: Provider) => providers[p]?.web === false;

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="signin-title">
        <div className="dialog-head">
          <div>
            <h2 id="signin-title" className="display">
              {step === 'in' ? 'Sign in' : step === 'up' ? 'Create account' : 'Choose a username'}
            </h2>
            <p>
              {step === 'handle'
                ? 'This is the name on the scoreboard. It is permanent once claimed.'
                : 'One account for the browser game and the desktop app.'}
            </p>
          </div>
          <button type="button" className="close" onClick={onClose}>
            Close
          </button>
        </div>

        <form className="dialog-body" onSubmit={submit}>
          {step !== 'handle' && (
            <>
              <div className="providers">
                {(['github', 'google'] as const).map((p) => (
                  <button
                    key={p}
                    type="button"
                    className="btn"
                    disabled={busy !== null || webOff(p)}
                    title={
                      webOff(p)
                        ? `This server has no ${p === 'github' ? 'GitHub' : 'Google'} sign-in set up`
                        : undefined
                    }
                    onClick={() => oauth(p)}
                  >
                    {p === 'github' ? GITHUB_MARK : GOOGLE_MARK}
                    {busy === p ? 'Waiting…' : p === 'github' ? 'GitHub' : 'Google'}
                  </button>
                ))}
              </div>
              {webOff('github') && webOff('google') && (
                <p className="hint">
                  This server has no GitHub or Google sign-in set up. Use email instead.
                </p>
              )}
              {(busy === 'github' || busy === 'google') && (
                <p className="hint">
                  Finish signing in in the window that opened. Nothing opened?{' '}
                  <button type="button" className="link" onClick={() => abortRef.current?.abort()}>
                    Cancel
                  </button>{' '}
                  and allow pop-ups for this site.
                </p>
              )}
              <div className="divider">or with email</div>
              <label className="field">
                <span>Email</span>
                <input
                  type="email"
                  className="input"
                  autoComplete="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setError('');
                  }}
                />
              </label>
              <label className="field">
                <span>Password</span>
                <input
                  type="password"
                  className="input"
                  autoComplete={step === 'up' ? 'new-password' : 'current-password'}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setError('');
                  }}
                />
              </label>
              {step === 'up' && (
                <label className="field">
                  <span>Username</span>
                  <input
                    type="text"
                    className="input"
                    autoComplete="username"
                    placeholder="night_owl"
                    maxLength={20}
                    value={username}
                    onChange={(e) => {
                      setUsername(e.target.value.toLowerCase());
                      setError('');
                    }}
                  />
                </label>
              )}
            </>
          )}

          {step === 'handle' && (
            <label className="field">
              <span>Username</span>
              <input
                type="text"
                className="input"
                autoComplete="username"
                maxLength={20}
                value={handle}
                onChange={(e) => {
                  setHandle(e.target.value.toLowerCase());
                  setError('');
                }}
                autoFocus
              />
            </label>
          )}

          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={busy !== null}>
              {busy === 'password' || busy === 'handle'
                ? 'Working…'
                : step === 'in'
                  ? 'Sign in'
                  : step === 'up'
                    ? 'Create account'
                    : 'Claim username'}
            </button>
          </div>

          {step !== 'handle' && (
            <p className="hint">
              {step === 'in' ? 'New here? ' : 'Already have an account? '}
              <button
                type="button"
                className="link"
                onClick={() => {
                  setStep(step === 'in' ? 'up' : 'in');
                  setError('');
                }}
              >
                {step === 'in' ? 'Create an account' : 'Sign in'}
              </button>
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
