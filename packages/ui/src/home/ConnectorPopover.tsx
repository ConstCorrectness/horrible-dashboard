import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  beginConnect,
  disconnectConnector,
  openExternal,
  pollUntilDone,
  submitConnect,
  type Connector,
  type ConnectStep,
} from '@horrible/core';

/**
 * The panel behind a tile: what's granted and a Disconnect when connected, the
 * connect flow when not.
 *
 * One component drives all three connector kinds because the backend returns one step
 * shape — `device` shows a code, `redirect` opens a tab, `form` renders fields and may
 * return another form.
 */
export function ConnectorPopover({
  connector,
  onClose,
  onChanged,
}: {
  connector: Connector;
  onClose: () => void;
  onChanged: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState<ConnectStep | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(connector.error);
  const [values, setValues] = useState<Record<string, string>>({});
  const cancelled = useRef<AbortController | null>(null);

  // Close on outside click or Escape. The popover is transient chrome — trapping the
  // user in it would be worse than losing an in-flight code they can re-request.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    ref.current?.focus();
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      cancelled.current?.abort();
    };
  }, [onClose]);

  const settle = (result: ConnectStep) => {
    if (result.connected) {
      setStep(null);
      onChanged();
      onClose();
      return true;
    }
    if (result.error) {
      setError(result.error);
      setStep(null);
      return true;
    }
    return false;
  };

  /** Drive an oauth flow to completion: show the step, then poll. */
  const runOauth = async (first: ConnectStep) => {
    setStep(first);
    // openExternal, not window.open: under the desktop shell the webview can't
    // spawn browser windows, and OAuth belongs in the system browser anyway.
    if (first.authorize_url) void openExternal(first.authorize_url);
    const controller = new AbortController();
    cancelled.current = controller;
    const result = await pollUntilDone(connector.id, {
      intervalS: first.interval ?? undefined,
      expiresInS: first.expires_in ?? undefined,
      signal: controller.signal,
    });
    if (!controller.signal.aborted) settle(result);
  };

  /** Show a form step, seeding the inputs with whatever the backend prefilled. */
  const showForm = (next: ConnectStep) => {
    setValues(Object.fromEntries(next.fields.map((f) => [f.name, f.value ?? ''])));
    setStep(next);
  };

  /** Advance to whatever the backend handed back, whichever kind of step it is. */
  const advance = async (next: ConnectStep) => {
    if (settle(next)) return;
    if (next.step === 'form') {
      showForm(next);
      return;
    }
    await runOauth(next);
  };

  const start = async (options: Record<string, unknown> = {}) => {
    setBusy(true);
    setError(null);
    try {
      await advance(await beginConnect(connector.id, options));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // A form step may hand back another form (phone → SMS code), or an oauth step —
      // which is how supplying client credentials chains straight into consent without
      // making the user press Connect a second time.
      await advance(await submitConnect(connector.id, values));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      await disconnectConnector(connector.id);
      onChanged();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="integration-popover"
      role="dialog"
      aria-label={connector.label}
      tabIndex={-1}
      ref={ref}
    >
      <header className="integration-popover-head">
        <strong>{connector.label}</strong>
        {connector.account && <span className="home-hint">{connector.account.label}</span>}
      </header>

      {error && <p className="widget-error">{error}</p>}

      {/* An in-flight step wins over the connected view: that's what lets an already
          connected account be reconfigured (rotate a secret, switch Cloud project)
          without disconnecting first. */}
      {step?.step === 'device' ? (
        <>
          <p className="home-hint">Enter this code at {step.verification_uri}:</p>
          <code className="integration-code">{step.user_code}</code>
          <p className="home-hint">
            <a href={step.verification_uri ?? '#'} target="_blank" rel="noreferrer">
              Open {step.verification_uri}
            </a>{' '}
            — this panel updates once you approve.
          </p>
        </>
      ) : step?.step === 'form' ? (
        <form className="integration-form" onSubmit={(e) => void send(e)}>
          {step.fields.map((f) => (
            <label key={f.name}>
              {f.label || f.name}
              <input
                type={f.secret ? 'password' : 'text'}
                placeholder={f.placeholder}
                value={values[f.name] ?? ''}
                autoComplete="off"
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              />
              {f.help && <span className="integration-field-help">{f.help}</span>}
            </label>
          ))}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? '…' : 'Continue'}
          </button>
        </form>
      ) : step?.step === 'redirect' ? (
        <p className="home-hint">
          Finish signing in on the tab that opened — this panel updates when you&apos;re done.
        </p>
      ) : connector.connected ? (
        <>
          <p className="home-hint">{connector.blurb}</p>
          {connector.scopes.length > 0 && (
            <ul className="integration-scopes">
              {connector.scopes.map((s) => (
                <li key={s.id}>
                  <strong>{s.label}</strong>
                  {s.description && <span className="home-hint"> {s.description}</span>}
                </li>
              ))}
            </ul>
          )}
          <div className="integration-actions">
            <button onClick={() => void disconnect()} disabled={busy}>
              {busy ? '…' : 'Disconnect'}
            </button>
            {connector.configurable && (
              <button onClick={() => void start({ reconfigure: true })} disabled={busy}>
                Reconfigure
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <p className="home-hint">{connector.blurb}</p>
          <button className="primary" onClick={() => void start()} disabled={busy}>
            {busy
              ? 'Starting…'
              : connector.configurable && !connector.configured
                ? `Set up ${connector.label}`
                : `Connect ${connector.label}`}
          </button>
        </>
      )}
    </div>
  );
}
