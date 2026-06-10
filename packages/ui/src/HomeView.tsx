import { useEffect, useState, type FormEvent } from 'react';
import {
  DEFAULT_AGENT_MODEL,
  getAgentStatus,
  pullAgentModel,
  saveAgentConfig,
  streamAgentChat,
  type AgentStatus,
} from '@horrible/core';

import { Avatar3D } from './Avatar3D';

const NAME_KEY = 'horrible.userName';

export function HomeView() {
  const [status, setStatus] = useState<AgentStatus | 'loading' | 'backend-down'>('loading');
  const [prompt, setPrompt] = useState('');
  const [answer, setAnswer] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = localStorage.getItem(NAME_KEY);

  const refresh = () =>
    getAgentStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
  useEffect(() => {
    void refresh();
  }, []);

  const ready = typeof status === 'object' && status.configured && status.ollama_reachable;

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || busy || !ready) return;
    setBusy(true);
    setAnswer('');
    try {
      await streamAgentChat(prompt, (token) => setAnswer((a) => (a ?? '') + token));
    } catch (err) {
      setAnswer(`Something went wrong: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="home">
      <div className="home-center">
        <Avatar3D />
        <h1 className="home-greeting">{name ? `Let's jump in, ${name}` : "Let's jump in"}</h1>
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
        {answer !== null && <div className="home-answer">{answer || '…'}</div>}
        {typeof status === 'object' && !ready && (
          <OnboardingCard status={status} onChanged={() => void refresh()} />
        )}
        {status === 'backend-down' && (
          <p className="home-hint">
            Backend unreachable — start it with{' '}
            <code>uv run uvicorn backend.app:app --port 8000</code>, then{' '}
            <button onClick={() => void refresh()}>retry</button>
          </p>
        )}
      </div>
    </div>
  );
}

function OnboardingCard({ status, onChanged }: { status: AgentStatus; onChanged: () => void }) {
  const [model, setModel] = useState(status.model ?? DEFAULT_AGENT_MODEL);
  const [name, setName] = useState(localStorage.getItem(NAME_KEY) ?? '');
  const [pullState, setPullState] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hasModel = status.available_models.includes(model);

  const doPull = async () => {
    setError(null);
    setPullState('starting…');
    try {
      await pullAgentModel(model, (p) => {
        const pct =
          p.total && p.completed ? ` ${Math.round((p.completed / p.total) * 100)}%` : '';
        setPullState(`${p.status ?? 'pulling'}${pct}`);
      });
      setPullState(null);
      onChanged();
    } catch (e) {
      setPullState(null);
      setError(String(e));
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (name.trim()) localStorage.setItem(NAME_KEY, name.trim());
      await saveAgentConfig(model, status.endpoint);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="onboarding">
      <h2>Set up your dashboard friend</h2>
      <p className="home-hint">
        Answers come from a local model running on your machine — nothing leaves it.
      </p>
      <ol>
        <li>
          <span className={status.ollama_reachable ? 'step done' : 'step'}>1</span>
          {status.ollama_reachable ? (
            <>
              Ollama is running at <code>{status.endpoint}</code>
            </>
          ) : (
            <>
              Install and start{' '}
              <a href="https://ollama.com" target="_blank" rel="noreferrer">
                Ollama
              </a>
              <button onClick={onChanged}>Re-check</button>
            </>
          )}
        </li>
        <li>
          <span className={hasModel ? 'step done' : 'step'}>2</span>
          Local model:
          <input
            list="agent-models"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            spellCheck={false}
          />
          <datalist id="agent-models">
            {[...new Set([DEFAULT_AGENT_MODEL, ...status.available_models])].map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          {!hasModel &&
            status.ollama_reachable &&
            (pullState ? (
              <span className="pull-progress">{pullState}</span>
            ) : (
              <button onClick={() => void doPull()}>Pull model</button>
            ))}
        </li>
        <li>
          <span className="step">3</span>
          Call me:
          <input value={name} placeholder="optional" onChange={(e) => setName(e.target.value)} />
          <button
            className="primary"
            disabled={!status.ollama_reachable || !hasModel || saving}
            onClick={() => void save()}
          >
            {saving ? 'Saving…' : 'Finish setup'}
          </button>
        </li>
      </ol>
      {error && <p className="widget-error">{error}</p>}
    </section>
  );
}
