import { useEffect, useState, type FormEvent } from 'react';
import {
  askAgent,
  Avatar3D,
  getAgentStatus,
  getBackendOrigin,
  useSetting,
  type AgentStatus,
} from '@horrible/core';

import { NAME_SETTING_KEY, getUserName, SETUP_DISMISSED_KEY } from './home/constants';
import { IntegrationRow } from './home/IntegrationRow';
import { SetupCard } from './home/SetupCard';

/**
 * A chevron. Points down to put the home screen away and up to bring it back —
 * the same direction-of-travel every minimize control in the app uses.
 *
 * Drawn rather than typed: a `▾` is a font's opinion about size and baseline,
 * and it sits at a different height in each of the three themes' typefaces.
 */
function Chevron({ up }: { up?: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ transform: up ? 'rotate(180deg)' : undefined }}
    >
      <path d="M3.5 5.5 7 9l3.5-3.5" />
    </svg>
  );
}

/** The submit arrow, for the same reason. */
function SendGlyph() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2.5 7h8M7.2 3.4 10.8 7l-3.6 3.6" />
    </svg>
  );
}

/**
 * The home surface: greeting, avatar, connectors, ask bar, setup.
 *
 * `collapsed` reduces it to the ask bar alone, docked at the bottom. Everything
 * else is a *landing* screen — a greeting and an avatar are worth seeing when
 * you arrive and worth nothing when you are working over the top of them —
 * whereas the ask bar is the reason to keep the surface at all, so it is the one
 * thing the collapsed form keeps.
 *
 * The two forms share this component rather than being separate ones because
 * they share the agent request and its streaming state: splitting them would
 * mean a question asked in the strip is lost the moment it is expanded.
 */
export function HomeView({
  collapsed = false,
  onCollapsedChange,
}: {
  collapsed?: boolean;
  /** Absent where the surface has no way to be put away (the OOBE flow, tests),
   * in which case no minimize control is drawn rather than a dead one. */
  onCollapsedChange?: (collapsed: boolean) => void;
} = {}) {
  const [status, setStatus] = useState<AgentStatus | 'loading' | 'backend-down'>('loading');
  const [prompt, setPrompt] = useState('');
  const [answer, setAnswer] = useState<string | null>(null);
  const [reasoning, setReasoning] = useState('');
  const [actions, setActions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  // Subscribed, not read once: the name is a setting now, so typing it in
  // first-run setup (or in settings) has to reach the greeting without a reload.
  useSetting<string>(NAME_SETTING_KEY);
  const name = getUserName();
  const setupDismissed = useSetting<boolean>(SETUP_DISMISSED_KEY) === true;

  const refresh = () =>
    getAgentStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
  useEffect(() => {
    void refresh();
  }, []);

  const ready = typeof status === 'object' && status.configured && status.reachable;

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || busy || !ready) return;
    setBusy(true);
    setAnswer('');
    setReasoning('');
    setActions([]);
    try {
      await askAgent(prompt, {
        onToken: (delta) => setAnswer((a) => (a ?? '') + delta),
        onReasoning: (delta) => setReasoning((r) => r + delta),
        // The final answer is authoritative; fall back to the streamed text if empty.
        onAnswer: (text) => setAnswer((a) => text || a),
        onAction: (note) => setActions((a) => [...a, note]),
        onError: (msg) => setAnswer(`Something went wrong: ${msg}`),
      });
    } finally {
      setBusy(false);
    }
  };

  const askBar = (
    <form className="ask-bar" onSubmit={(e) => void ask(e)}>
      <input
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder={ready ? 'Ask your dashboard friend' : 'Finish setup below to start'}
        disabled={!ready || busy}
      />
      {typeof status === 'object' && status.model && (
        <span className="ask-model">{status.model}</span>
      )}
      <button type="submit" disabled={!ready || busy || !prompt.trim()} aria-label="Ask">
        {busy ? '…' : <SendGlyph />}
      </button>
    </form>
  );

  if (collapsed) {
    // The strip. Bottom-docked and only as wide as it needs to be, so the
    // desktop and every window over it stay visible and clickable — which is
    // the entire point of having minimized it.
    return (
      <div className="home home-strip">
        <div className="home-strip-inner">
          {onCollapsedChange && (
            <button
              type="button"
              className="home-collapse"
              onClick={() => onCollapsedChange(false)}
              aria-expanded={false}
              title="Show the home screen"
            >
              <Chevron up />
            </button>
          )}
          {askBar}
        </div>
        {/* An answer still has to land somewhere. Above the strip rather than
            expanding it, or asking a question would silently undo the collapse. */}
        {answer !== null && <div className="home-answer home-strip-answer">{answer || '…'}</div>}
      </div>
    );
  }

  return (
    <div className="home">
      <div className="home-center">
        {onCollapsedChange && (
          <button
            type="button"
            className="home-collapse home-collapse-corner"
            onClick={() => onCollapsedChange(true)}
            aria-expanded
            title="Minimize the home screen"
          >
            <Chevron />
            <span>Minimize</span>
          </button>
        )}
        <Avatar3D />
        <h1 className="home-greeting">{name ? `Let's jump in, ${name}` : "Let's jump in"}</h1>
        <IntegrationRow />
        {askBar}
        {reasoning && (
          <details className="agent-reasoning home-reasoning" open={!answer}>
            <summary>Reasoning</summary>
            <div className="agent-reasoning-body">{reasoning}</div>
          </details>
        )}
        {actions.length > 0 && (
          <ul className="home-actions">
            {actions.map((a, i) => (
              <li key={i}>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 12 12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M2 6.4 4.7 9 10 3.2" />
                </svg>
                {a}
              </li>
            ))}
          </ul>
        )}
        {answer !== null && <div className="home-answer">{answer || '…'}</div>}
        {/* The setup flow covers the model, the account and the connectors, so it
            shows whenever any of the three is outstanding — not only when the agent
            is unconfigured, which is all the old model-only card knew about. It
            hides itself once every step is done. */}
        {status !== 'loading' && !setupDismissed && (
          <SetupCard
            status={typeof status === 'object' && !ready ? status : null}
            onChanged={() => void refresh()}
          />
        )}
        {status === 'backend-down' &&
          // Shell-managed backend (desktop): it starts/restarts automatically,
          // so don't tell the user to run uvicorn by hand.
          (getBackendOrigin() ? (
            <p className="home-hint">
              Backend isn&apos;t responding yet —{' '}
              <button onClick={() => void refresh()}>retry</button>
            </p>
          ) : (
            <p className="home-hint">
              Backend unreachable — start the full stack with <code>pnpm dev</code> (or just the
              backend with <code>uv run uvicorn backend.app:app --port 8000</code>), then{' '}
              <button onClick={() => void refresh()}>retry</button>
            </p>
          ))}
      </div>
    </div>
  );
}
