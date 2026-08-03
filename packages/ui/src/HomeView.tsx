import { useEffect, useState, type FormEvent } from 'react';
import {
  askAgent,
  Avatar3D,
  getAgentStatus,
  getBackendOrigin,
  useSetting,
  type AgentStatus,
} from '@horrible/core';

import { NAME_KEY, SETUP_DISMISSED_KEY } from './home/constants';
import { IntegrationRow } from './home/IntegrationRow';
import { SetupCard } from './home/SetupCard';

export function HomeView() {
  const [status, setStatus] = useState<AgentStatus | 'loading' | 'backend-down'>('loading');
  const [prompt, setPrompt] = useState('');
  const [answer, setAnswer] = useState<string | null>(null);
  const [reasoning, setReasoning] = useState('');
  const [actions, setActions] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const name = localStorage.getItem(NAME_KEY);
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

  return (
    <div className="home">
      <div className="home-center">
        <Avatar3D />
        <h1 className="home-greeting">{name ? `Let's jump in, ${name}` : "Let's jump in"}</h1>
        <IntegrationRow />
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
          <button type="submit" disabled={!ready || busy || !prompt.trim()}>
            {busy ? '…' : '➤'}
          </button>
        </form>
        {reasoning && (
          <details className="agent-reasoning home-reasoning" open={!answer}>
            <summary>Reasoning</summary>
            <div className="agent-reasoning-body">{reasoning}</div>
          </details>
        )}
        {actions.length > 0 && (
          <ul className="home-actions">
            {actions.map((a, i) => (
              <li key={i}>✓ {a}</li>
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
