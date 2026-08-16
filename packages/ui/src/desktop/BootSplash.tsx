/**
 * The boot screen: what you see between opening the app and arriving at the
 * desktop.
 *
 * It narrates the **real** boot (`bootStore`), not a timer. A fake bar that
 * always takes 800ms says nothing when the backend is not answering, which is
 * exactly the moment the user needs to be told something — so a slow step shows
 * as a slow step, and a failed one says which and keeps going.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { bootStore } from '@horrible/core';

/** After this long the step list appears on its own. See below. */
const DETAIL_AFTER_MS = 1200;

export function BootSplash() {
  const state = useSyncExternalStore(bootStore.subscribe, bootStore.getSnapshot);
  const [showDetail, setShowDetail] = useState(false);

  useEffect(() => {
    // A fast boot should be a logo and a fade, not a wall of log lines — but a
    // boot that stalls has to explain itself without the user having to ask. So
    // the detail is time-gated rather than always on or behind a toggle.
    const t = setTimeout(() => setShowDetail(true), DETAIL_AFTER_MS);
    return () => clearTimeout(t);
  }, []);

  const failed = state.steps.filter((s) => s.error);
  const current = state.steps.find((s) => s.ms === undefined);

  return (
    <div className="os-boot" role="status" aria-live="polite">
      <div className="os-boot-center">
        <img className="os-boot-logo" src="/logo.svg" alt="" aria-hidden="true" />
        <h1 className="os-boot-title">horrible-dashboard</h1>
        {state.phase === 'failed' ? (
          <>
            <p className="os-boot-fatal">Startup failed.</p>
            <pre className="os-boot-trace">{state.fatal}</pre>
          </>
        ) : (
          <p className="os-boot-step">{current?.label ?? 'Starting…'}</p>
        )}
        <div className={`os-boot-bar${state.phase === 'failed' ? ' is-failed' : ''}`}>
          {/* Indeterminate on purpose: the number of steps is known but their
              durations are not remotely comparable, so a percentage would sit at
              90% through the one step that actually takes time. */}
          <span className="os-boot-bar-fill" />
        </div>
        {(showDetail || failed.length > 0) && (
          <ul className="os-boot-steps">
            {state.steps.map((s) => (
              <li
                key={s.id}
                className={s.error ? 'is-error' : s.ms === undefined ? 'is-running' : 'is-done'}
              >
                <span className="os-boot-mark" aria-hidden="true">
                  {s.error ? '!' : s.ms === undefined ? '·' : '✓'}
                </span>
                <span className="os-boot-label">{s.label}</span>
                {s.ms !== undefined && <span className="os-boot-ms">{s.ms} ms</span>}
                {s.error && <span className="os-boot-error">{s.error}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
