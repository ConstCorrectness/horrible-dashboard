import { useEffect, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import { RoomsPanel } from './RoomsPanel';
import {
  completeClubhouseAuth,
  connectClubhouseWithToken,
  disconnectClubhouse,
  getClubhouseStatus,
  startClubhouseAuth,
  type ClubhouseStatus,
} from './api';

/**
 * How the account gets linked. `sms` is the Clubdeck-style flow (we ask
 * Clubhouse to text a code); `code` skips that request because Clubhouse's
 * anti-bot gate can block it — the user gets the code from the real app and
 * types it here; `token` pastes a session from another logged-in client.
 */
type AuthMethod = 'sms' | 'code' | 'token';

const METHOD_LABELS: Record<AuthMethod, string> = {
  sms: 'Text me a code',
  code: 'I have a code',
  token: 'Auth token',
};

/**
 * Clubdeck-style account onboarding: phone number -> SMS code -> connected
 * profile. All Clubhouse traffic goes through the backend; the browser never
 * sees the auth token.
 */
export function ClubhouseWidget() {
  const [status, setStatus] = useState<ClubhouseStatus | 'loading' | 'backend-down'>('loading');
  const [method, setMethod] = useState<AuthMethod>('sms');
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [userId, setUserId] = useState('');

  const refresh = () =>
    getClubhouseStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
  useEffect(() => {
    void refresh();
  }, []);

  // Expose the connection state so the agent knows whose account is linked.
  useAgentContext(() => {
    if (status === 'loading' || status === 'backend-down') return { state: status };
    if (status.connected) {
      return { connected: true, name: status.name ?? null, username: status.username ?? null };
    }
    return { connected: false, method, step };
  });

  if (status === 'loading') return <p>Checking…</p>;
  if (status === 'backend-down') {
    return <p className="widget-error">Backend unreachable — is it running on port 8000?</p>;
  }

  if (status.connected) {
    return (
      <div
        className="ch-rooms-panel-root"
        style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}
      >
        <div
          className="ch-rooms-profile-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0.75rem 1.25rem',
            borderBottom: '1px solid var(--border)',
            background: 'var(--bg-raised)',
          }}
        >
          <div
            className="ch-connected"
            style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}
          >
            {status.photo_url && (
              <img
                className="ch-avatar"
                src={status.photo_url}
                alt=""
                style={{ width: '32px', height: '32px', borderRadius: '50%' }}
              />
            )}
            <div className="ch-profile">
              <strong style={{ display: 'block', fontSize: '0.9rem', color: 'var(--text)' }}>
                {status.name ?? 'Connected'}
              </strong>
              {status.username && (
                <span
                  className="ch-username"
                  style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}
                >
                  @{status.username}
                </span>
              )}
            </div>
          </div>
          <button
            className="ch-btn-disconnect"
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: '6px',
              padding: '0.25rem 0.6rem',
              fontSize: '0.75rem',
              cursor: 'pointer',
              color: 'var(--text-dim)',
            }}
            onClick={() => {
              void disconnectClubhouse().then(() => {
                setMethod('sms');
                setStep('phone');
                void refresh();
              });
            }}
          >
            Disconnect
          </button>
        </div>
        <div style={{ flex: 1, minHeight: 0 }}>
          <RoomsPanel />
        </div>
      </div>
    );
  }

  const sendCode = async () => {
    setBusy(true);
    setError(null);
    try {
      await startClubhouseAuth(phone.trim());
      setStep('code');
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    setError(null);
    try {
      await completeClubhouseAuth(phone.trim(), code.trim());
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const connectToken = async () => {
    setBusy(true);
    setError(null);
    try {
      await connectClubhouseWithToken(token.trim(), Number(userId.trim()));
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const phoneValid = /^\+\d{7,15}$/.test(phone.trim());
  const codeValid = /^\d{4,8}$/.test(code.trim());

  return (
    <div className="ch-onboarding">
      <div className="ch-methods" role="tablist">
        {(Object.keys(METHOD_LABELS) as AuthMethod[]).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={method === m}
            className={`ch-method${method === m ? ' is-active' : ''}`}
            disabled={busy}
            onClick={() => {
              setMethod(m);
              setStep('phone');
              setError(null);
            }}
          >
            {METHOD_LABELS[m]}
          </button>
        ))}
      </div>
      {method === 'token' ? (
        <>
          <p className="dashboard-hint">Paste a Clubhouse session from another logged-in client.</p>
          <form
            className="ch-token-form"
            onSubmit={(e) => {
              e.preventDefault();
              void connectToken();
            }}
          >
            <input
              value={token}
              placeholder="auth token"
              spellCheck={false}
              onChange={(e) => setToken(e.target.value)}
            />
            <input
              inputMode="numeric"
              value={userId}
              placeholder="user id"
              onChange={(e) => setUserId(e.target.value.replace(/\D/g, ''))}
            />
            <button type="submit" disabled={busy || !token.trim() || !userId.trim()}>
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          </form>
        </>
      ) : method === 'code' ? (
        <>
          <p className="dashboard-hint">
            Already got a code? Request one in the Clubhouse app, then enter the number it's
            registered to and the code you were sent.
          </p>
          <form
            className="ch-token-form"
            onSubmit={(e) => {
              e.preventDefault();
              void verify();
            }}
          >
            <input
              type="tel"
              value={phone}
              placeholder="+1 555 123 4567"
              onChange={(e) => setPhone(e.target.value.replace(/[^\d+]/g, ''))}
            />
            <input
              inputMode="numeric"
              value={code}
              placeholder="123456"
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            />
            <button type="submit" disabled={busy || !phoneValid || !codeValid}>
              {busy ? 'Verifying…' : 'Verify'}
            </button>
          </form>
        </>
      ) : step === 'phone' ? (
        <>
          <p className="dashboard-hint">
            Connect your Clubhouse account — enter the phone number it's registered to.
          </p>
          <form
            className="widget-form"
            onSubmit={(e) => {
              e.preventDefault();
              void sendCode();
            }}
          >
            <input
              type="tel"
              value={phone}
              placeholder="+1 555 123 4567"
              onChange={(e) => setPhone(e.target.value.replace(/[^\d+]/g, ''))}
            />
            <button type="submit" disabled={busy || !phoneValid}>
              {busy ? 'Sending…' : 'Send code'}
            </button>
          </form>
        </>
      ) : (
        <>
          <p className="dashboard-hint">Enter the verification code Clubhouse texted to {phone}.</p>
          <form
            className="widget-form"
            onSubmit={(e) => {
              e.preventDefault();
              void verify();
            }}
          >
            <input
              inputMode="numeric"
              value={code}
              placeholder="123456"
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            />
            <button type="submit" disabled={busy || !codeValid}>
              {busy ? 'Verifying…' : 'Verify'}
            </button>
          </form>
          <button className="ch-back" disabled={busy} onClick={() => setStep('phone')}>
            Different number
          </button>
        </>
      )}
      {error && <p className="widget-error">{error}</p>}
    </div>
  );
}
