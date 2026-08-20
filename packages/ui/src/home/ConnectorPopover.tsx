import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  beginConnect,
  CopyableLink,
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
  variant = 'anchored',
}: {
  connector: Connector;
  onClose: () => void;
  onChanged: () => void;
  /**
   * Where this is being shown. `anchored` hangs it off a tile in the home row;
   * `modal` is the shell-level dialog, which is how every other surface reaches
   * the connect flow without navigating to home first. Presentation only — the
   * flow itself is identical, which is the point of having one component.
   */
  variant?: 'anchored' | 'modal';
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState<ConnectStep | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(connector.error);
  const [values, setValues] = useState<Record<string, string>>({});
  /** The consent page could not be opened — the flow is fine, the user just
   * cannot see it. Not an error: the poll below is still running. */
  const [blocked, setBlocked] = useState(false);
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
    setBlocked(false);
    // openExternal, not window.open: under the desktop shell the webview can't
    // spawn browser windows, and OAuth belongs in the system browser anyway.
    //
    // The result is checked for the same reason the sign-in card checks it: when
    // nothing opens, "Finish signing in on the tab that opened" is a lie about a
    // tab that does not exist, and the poll below then runs its full expiry
    // against a consent page the user was never shown.
    if (first.authorize_url) {
      const url = first.authorize_url;
      void openExternal(url).then((opened) => {
        if (!opened) setBlocked(true);
      });
    }
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
      className={`integration-popover${variant === 'modal' ? ' is-modal' : ''}`}
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
            {step.verification_uri && (
              <>
                <CopyableLink
                  url={step.verification_uri}
                  label={`Open ${step.verification_uri}`}
                />{' '}
              </>
            )}
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
        blocked && step.authorize_url ? (
          <p className="home-hint">
            <span className="integration-blocked">Nothing opened.</span> Open the consent page
            yourself — this panel updates when you&apos;re done.
            <CopyableLink url={step.authorize_url} label="Open the consent page" />
          </p>
        ) : (
          <p className="home-hint">
            Finish signing in on the tab that opened — this panel updates when you&apos;re done.
          </p>
        )
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
