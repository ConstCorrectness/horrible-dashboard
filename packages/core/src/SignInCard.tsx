import { useEffect, useState, type FormEvent } from 'react';

import {
  fetchAuthProviders,
  oauthSignIn,
  signInWithPassword,
  signUpWithPassword,
  type AuthProviders,
  type SignInPrompt,
  type SignInProvider,
} from './account';
import { refreshAccount } from './account-store';
import { CopyableLink } from './CopyableLink';
import { PROVIDER_MARKS } from './provider-marks';
import './signin.css';

/**
 * The app's sign-in, in one place.
 *
 * There used to be three of these — HorribleAssault's boot overlay, the games
 * lobby sidebar and the games first-run hero — each with its own copy of the OAuth
 * dance and its own idea of what to show when something failed. They had drifted:
 * one still called the device-only entry point and had no branch at all for "the
 * page could not be opened", so it rendered `Enter code  at ` with the code and
 * URL both blank. Three copies of a flow this fiddly is three chances to be
 * subtly wrong, and it took the worst one to be discovered by a user.
 *
 * It lives in `packages/core` because `hassault` and `games` are core modules and
 * a core module must not import `packages/ui` — the same reason `Avatar3D` is here.
 *
 * What it will not do is pretend. Every state it can end up in says what is
 * actually true: which server it is talking to when a provider is unconfigured,
 * that nothing opened when nothing opened, and — when even the fallback link
 * fails — the raw address, because at that point the user's own browser is the
 * only mechanism left that works.
 */
export function SignInCard({
  onSignedIn,
  /** Rendered above the provider buttons. Callers own their own headings. */
  intro,
  className,
}: {
  onSignedIn?: () => void;
  intro?: React.ReactNode;
  className?: string;
}) {
  const [mode, setMode] = useState<'in' | 'up'>('in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [callsign, setCallsign] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const [prompt, setPrompt] = useState<SignInPrompt | null>(null);
  const [providers, setProviders] = useState<AuthProviders>({ server: '', flows: {} });

  useEffect(() => {
    fetchAuthProviders()
      .then(setProviders)
      .catch(() => {
        /* unknown means "keep the buttons enabled" — click-time errors take over */
      });
  }, []);

  // Only when the server *positively* reports neither flow. An older or
  // unreachable server says nothing, and that must not disable a working button.
  const unavailable = (provider: SignInProvider): boolean => {
    const f = providers.flows[provider];
    return f != null && !f.device && !f.web;
  };
  const oauthOff = unavailable('github') && unavailable('google');
  const passwordWorks = providers.flows.local?.password !== false;
  /** Device flow needs no window at all, so it's the escape hatch worth offering
   * by name once we know opening one didn't work. */
  const deviceAvailable = (provider: SignInProvider): boolean =>
    providers.flows[provider]?.device !== false;

  const finish = async () => {
    await refreshAccount();
    onSignedIn?.();
  };

  const oauth = async (provider: SignInProvider, prefer?: 'device') => {
    setBusy(provider);
    setErr('');
    try {
      await oauthSignIn(provider, setPrompt, prefer ? { prefer } : {});
      await finish();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy('local');
    setErr('');
    try {
      if (mode === 'up') await signUpWithPassword(email, password, callsign.trim());
      else await signInWithPassword(email, password);
      await finish();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  /** Which provider the visible prompt belongs to — the one we're busy with, or
   * the last one, so the "use a code instead" button targets the right flow. */
  const promptProvider: SignInProvider =
    busy === 'google' ? 'google' : busy === 'github' ? 'github' : 'github';

  return (
    <div className={`signin-card${className ? ` ${className}` : ''}`}>
      {intro}

      <div className="signin-providers">
        {(['github', 'google'] as const).map((provider) => (
          <button
            key={provider}
            type="button"
            className="signin-provider-btn"
            onClick={() => void oauth(provider)}
            disabled={busy != null || unavailable(provider)}
            title={unavailable(provider) ? `${provider} sign-in is not configured` : undefined}
          >
            {PROVIDER_MARKS[provider]}
            {busy === provider
              ? 'Waiting…'
              : `Continue with ${provider === 'github' ? 'GitHub' : 'Google'}`}
          </button>
        ))}
      </div>

      {oauthOff && (
        <p className="signin-note warn">
          GitHub and Google sign-in aren&rsquo;t configured on the game server
          {providers.server ? (
            <>
              {' '}
              this node uses (<code>{providers.server}</code>)
            </>
          ) : null}
          .{passwordWorks ? ' Use email and password below.' : ''}
        </p>
      )}

      {prompt && (
        <div className="signin-prompt">
          {prompt.blocked ? (
            // Nothing opened, so do not claim anything did. The sign-in is still
            // running and still polling — all that is missing is the user seeing
            // the page, and their own click is a gesture no pop-up blocker refuses.
            // If that fails too, CopyableLink hands over the address itself.
            <>
              <span className="warn">Your browser blocked the sign-in window.</span>{' '}
              <CopyableLink
                url={prompt.url}
                label={`Open the ${prompt.code ? 'code page' : 'sign-in page'}`}
              />
              {prompt.code ? (
                <>
                  {' '}
                  and enter <strong className="signin-code">{prompt.code}</strong>
                </>
              ) : (
                deviceAvailable(promptProvider) && (
                  <>
                    {' '}
                    Or{' '}
                    <button
                      type="button"
                      className="signin-link-btn"
                      onClick={() => void oauth(promptProvider, 'device')}
                    >
                      use a code instead
                    </button>{' '}
                    — that flow opens nothing.
                  </>
                )
              )}
            </>
          ) : (
            <>
              {prompt.code ? (
                <>
                  Enter <strong className="signin-code">{prompt.code}</strong> at{' '}
                  <CopyableLink url={prompt.url} label={prompt.url} />
                </>
              ) : (
                <>
                  Finish in the window that opened —{' '}
                  <CopyableLink url={prompt.url} label="reopen" />
                </>
              )}
            </>
          )}
        </div>
      )}

      <div className="signin-divider">
        <span>or</span>
      </div>

      <form className="signin-form" onSubmit={(e) => void submit(e)}>
        <input
          type="email"
          required
          autoComplete="email"
          placeholder="you@example.com"
          aria-label="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          type="password"
          required
          minLength={8}
          autoComplete={mode === 'up' ? 'new-password' : 'current-password'}
          placeholder={mode === 'up' ? 'Password (8+ characters)' : 'Password'}
          aria-label="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {mode === 'up' && (
          <input
            placeholder="Callsign (optional)"
            aria-label="Callsign"
            maxLength={20}
            value={callsign}
            onChange={(e) => setCallsign(e.target.value)}
          />
        )}
        <button className="signin-submit" type="submit" disabled={busy != null}>
          {busy === 'local' ? 'Working…' : mode === 'up' ? 'Create account' : 'Sign in'}
        </button>
      </form>

      {err && <p className="signin-note error">{err}</p>}

      <p className="signin-note">
        {mode === 'up' ? 'Already have an account?' : 'New here?'}{' '}
        <button
          type="button"
          className="signin-link-btn"
          onClick={() => {
            setMode(mode === 'up' ? 'in' : 'up');
            setErr('');
          }}
        >
          {mode === 'up' ? 'Sign in' : 'Create an account'}
        </button>
      </p>
    </div>
  );
}
