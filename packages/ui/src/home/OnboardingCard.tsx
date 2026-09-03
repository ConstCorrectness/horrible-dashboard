import { useMemo, useState } from 'react';
import {
  DEFAULT_AGENT_MODEL,
  DEFAULT_VLLM_MODEL,
  pullAgentModel,
  saveAgentConfig,
  saveProviderKey,
  spawnVllm,
  stopVllm,
  type AgentStatus,
  type DetectedProvider,
} from '@horrible/core';

import { getUserName, setUserName } from './constants';

/** First-run setup for the model: pick a provider, get a model, name yourself.
 * Shown on home until the agent is both configured and reachable.
 *
 * A provider is either a **local server** (Ollama, LM Studio, llama.cpp, vLLM) or a
 * **hosted API** (OpenAI, Anthropic, Gemini, OpenRouter). The difference shows up in
 * exactly one place — what an unreachable provider needs from you. A local one needs
 * installing and starting; a hosted one needs a key, entered here rather than sending
 * the user off to the settings page mid-onboarding. The key is written straight to the
 * backend's encrypted store and never comes back. */
export function OnboardingCard({
  status,
  onChanged,
}: {
  status: AgentStatus;
  onChanged: () => void;
}) {
  const providers = status.providers;
  // Default to the configured provider, else the first reachable one, else Ollama.
  const initialKind =
    status.provider ?? providers.find((p) => p.reachable)?.kind ?? providers[0]?.kind ?? 'ollama';
  const [providerKind, setProviderKind] = useState(initialKind);
  const provider = useMemo(
    () => providers.find((p) => p.kind === providerKind) ?? providers[0],
    [providers, providerKind],
  );

  const [model, setModel] = useState(status.model ?? DEFAULT_AGENT_MODEL);
  const [name, setName] = useState(getUserName);
  const [pullState, setPullState] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!provider) return null;

  const hasModel = provider.models.includes(model);
  // Ollama needs the model pulled first; OpenAI-dialect providers serve whatever
  // is already loaded and hosted ones serve everything in their catalog, so
  // reachability + a model name is enough for both.
  const canFinish =
    provider.reachable && model.trim().length > 0 && (hasModel || !provider.can_pull) && !saving;

  const pickProvider = (p: DetectedProvider) => {
    setProviderKind(p.kind);
    setError(null);
    // Switch to a model the new provider actually offers.
    if (!p.models.includes(model)) {
      setModel(p.models[0] ?? (p.kind === 'ollama' ? DEFAULT_AGENT_MODEL : ''));
    }
  };

  const doPull = async () => {
    setError(null);
    setPullState('starting…');
    try {
      await pullAgentModel(model, (p) => {
        const pct = p.total && p.completed ? ` ${Math.round((p.completed / p.total) * 100)}%` : '';
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
      if (name.trim()) await setUserName(name);
      await saveAgentConfig(model, provider.kind, provider.endpoint);
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
        {provider.hosted
          ? `Answers come from ${provider.label}, so your prompts leave this machine. Pick a local provider below to keep everything here.`
          : 'Answers come from a local model running on your machine — nothing leaves it.'}
      </p>
      <ol>
        <li>
          <span className={provider.reachable ? 'step done' : 'step'}>1</span>
          <div className="onboarding-field">
            Choose a provider:
            <div className="provider-list">
              {providers.map((p) => (
                <button
                  key={p.kind}
                  type="button"
                  className={`provider-option${p.kind === providerKind ? ' selected' : ''}`}
                  onClick={() => pickProvider(p)}
                >
                  <span className={`provider-dot${p.reachable ? ' on' : ''}`} />
                  {p.label}
                </button>
              ))}
            </div>
            <ProviderStatus provider={provider} onChanged={onChanged} />
          </div>
        </li>
        <li>
          <span className={hasModel ? 'step done' : 'step'}>2</span>
          <div className="onboarding-field">
            {provider.hosted ? 'Model:' : 'Local model:'}
            <input
              list="agent-models"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              spellCheck={false}
              placeholder={
                provider.kind === 'ollama'
                  ? DEFAULT_AGENT_MODEL
                  : provider.hosted
                    ? 'model id'
                    : 'loaded model id'
              }
            />
            <datalist id="agent-models">
              {[...new Set(provider.models)].map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            {provider.can_pull &&
              provider.reachable &&
              !hasModel &&
              (pullState ? (
                <span className="pull-progress">{pullState}</span>
              ) : (
                <button onClick={() => void doPull()}>Pull model</button>
              ))}
          </div>
        </li>
        <li>
          <span className="step">3</span>
          Call me:
          <input value={name} placeholder="optional" onChange={(e) => setName(e.target.value)} />
          <button className="primary" disabled={!canFinish} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Finish setup'}
          </button>
        </li>
      </ol>
      {error && <p className="widget-error">{error}</p>}
    </section>
  );
}

/** Per-provider reachability detail + install/spawn affordances. */
function ProviderStatus({
  provider,
  onChanged,
}: {
  provider: DetectedProvider;
  onChanged: () => void;
}) {
  if (provider.reachable) {
    return (
      <p className="home-hint">
        {provider.label} is running at <code>{provider.endpoint}</code>
      </p>
    );
  }
  if (provider.hosted) {
    return <ApiKeyControls provider={provider} onChanged={onChanged} />;
  }
  if (provider.kind === 'vllm') {
    return <VllmControls onChanged={onChanged} />;
  }
  return (
    <p className="home-hint">
      Not detected at <code>{provider.endpoint}</code>. Install and start{' '}
      <a href={provider.install_url} target="_blank" rel="noreferrer">
        {provider.label}
      </a>
      , then <button onClick={onChanged}>Re-check</button>
    </p>
  );
}

/** Key entry for a hosted provider. The one thing standing between an unreachable
 * hosted provider and a usable one, so it belongs in the flow rather than behind a
 * link to settings. Write-only: nothing reads a key back, here or anywhere. */
function ApiKeyControls({
  provider,
  onChanged,
}: {
  provider: DetectedProvider;
  onChanged: () => void;
}) {
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await saveProviderKey(provider.kind, key);
      setKey('');
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="api-key-controls">
      <p className="home-hint">
        {provider.label} runs in the cloud, so answers do leave this machine. Paste an API key to
        use it — it is stored encrypted here and never sent to the browser.{' '}
        {provider.api_key_url && (
          <a href={provider.api_key_url} target="_blank" rel="noreferrer">
            Get a key
          </a>
        )}
      </p>
      <input
        type="password"
        value={key}
        spellCheck={false}
        autoComplete="off"
        placeholder="Paste API key"
        onChange={(e) => setKey(e.target.value)}
      />
      <button disabled={saving || key.trim() === ''} onClick={() => void save()}>
        {saving ? 'Saving…' : 'Save key'}
      </button>
      {error && <p className="widget-error">{error}</p>}
    </div>
  );
}

/** Spawn/stop the backend vLLM server. Lives in status.vllm, fetched alongside
 * the provider probe. */
function VllmControls({ onChanged }: { onChanged: () => void }) {
  const [model, setModel] = useState(DEFAULT_VLLM_MODEL);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const spawn = async () => {
    setError(null);
    setWorking('starting vLLM…');
    try {
      await spawnVllm(model);
      setWorking(null);
      onChanged();
    } catch (e) {
      setWorking(null);
      setError(String(e));
    }
  };

  const stop = async () => {
    setWorking('stopping…');
    try {
      await stopVllm();
    } finally {
      setWorking(null);
      onChanged();
    }
  };

  return (
    <div className="vllm-controls">
      <p className="home-hint">
        Not running. The backend can launch a vLLM server (needs Linux/WSL2 or Docker and usually a
        GPU — not native Windows).
      </p>
      <input value={model} onChange={(e) => setModel(e.target.value)} spellCheck={false} />
      {working ? (
        <span className="pull-progress">{working}</span>
      ) : (
        <>
          <button onClick={() => void spawn()}>Spawn vLLM</button>
          <button onClick={() => void stop()}>Stop</button>
          <button onClick={onChanged}>Re-check</button>
        </>
      )}
      {error && <p className="widget-error">{error}</p>}
    </div>
  );
}
