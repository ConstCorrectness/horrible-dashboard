import { useEffect, useState } from 'react';

import {
  completeClubhouseAuth,
  disconnectClubhouse,
  getClubhouseStatus,
  startClubhouseAuth,
  type ClubhouseStatus,
} from './api';

/**
 * Clubdeck-style account onboarding: phone number -> SMS code -> connected
 * profile. All Clubhouse traffic goes through the backend; the browser never
 * sees the auth token.
 */
export function ClubhouseWidget() {
  const [status, setStatus] = useState<ClubhouseStatus | 'loading' | 'backend-down'>('loading');
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    getClubhouseStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
  useEffect(() => {
    void refresh();
  }, []);

  if (status === 'loading') return <p>Checking…</p>;
  if (status === 'backend-down') {
    return <p className="widget-error">Backend unreachable — is it running on port 8000?</p>;
  }

  if (status.connected) {
    return (
      <div className="ch-connected">
        {status.photo_url && <img className="ch-avatar" src={status.photo_url} alt="" />}
        <div className="ch-profile">
          <strong>{status.name ?? 'Connected'}</strong>
          {status.username && <span className="ch-username">@{status.username}</span>}
        </div>
        <button
          onClick={() => {
            void disconnectClubhouse().then(() => {
              setStep('phone');
              void refresh();
            });
          }}
        >
          Disconnect
        </button>
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

  return (
    <div className="ch-onboarding">
      {step === 'phone' ? (
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
            <button type="submit" disabled={busy || !/^\+\d{7,15}$/.test(phone.trim())}>
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
              placeholder="1234"
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
            />
            <button type="submit" disabled={busy || !/^\d{4,8}$/.test(code.trim())}>
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
