/**
 * Settings-page section for **hosted model provider API keys** (OpenAI, Anthropic,
 * Gemini, OpenRouter).
 *
 * A key is deliberately *not* a setting. `GET /api/settings` hands the whole bag to
 * the browser and to every plugin, so a credential there is a credential given away;
 * keys live Fernet-encrypted in `secrets.db` and are written through
 * `PUT /agent/providers/<kind>/key`, which returns only whether one is now held.
 * That is why the field below is write-only — it never shows what is stored, because
 * nothing can read it back.
 *
 * Once a key is in place the provider becomes *reachable* in `/agent/status`, its
 * model catalog fills the model dropdowns in
 * [OrchestratorSettings](./OrchestratorSettings.tsx) and onboarding, and it can be
 * picked per-agent like any local provider.
 */
import { useCallback, useEffect, useState } from 'react';

import { deleteProviderKey, getAgentStatus, saveProviderKey, type DetectedProvider } from './api';

export function ApiKeysSettings() {
  const [providers, setProviders] = useState<DetectedProvider[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    void getAgentStatus()
      .then((s) => setProviders((s.providers ?? []).filter((p) => p.hosted)))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  useEffect(refresh, [refresh]);

  if (error) return <p className="widget-error">Could not read provider status: {error}</p>;
  if (providers.length === 0) return null;

  return (
    <div className="api-keys-settings">
      <p className="setting-desc">
        Keys for hosted model providers. Each is stored encrypted on this machine and{' '}
        <b>never sent to the browser</b> — the field below writes, it does not read, so a saved key
        shows as saved rather than as its own value. A provider with a key appears in the provider
        and model dropdowns above.
      </p>
      {providers.map((p) => (
        <KeyRow key={p.kind} provider={p} onChanged={refresh} />
      ))}
    </div>
  );
}

function KeyRow({ provider, onChanged }: { provider: DetectedProvider; onChanged: () => void }) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveProviderKey(provider.kind, value);
      setValue('');
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await deleteProviderKey(provider.kind);
      // A key exported in the backend's environment survives this, and saying
      // otherwise would leave the user hunting for a key we cannot delete.
      if (res.has_api_key) {
        setError(
          `Removed the stored key, but ${provider.label} still has one from the backend's environment.`,
        );
      }
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="setting-row">
      <div className="setting-label">
        <label>{provider.label}</label>
        <p className="setting-desc">
          {provider.has_api_key ? (
            <>
              Key saved.{' '}
              {provider.models.length > 0 && `${provider.models.length} models offered. `}
              Enter a new one to replace it.{' '}
            </>
          ) : (
            <>No key — this provider is not selectable yet. </>
          )}
          {provider.api_key_url && (
            <a href={provider.api_key_url} target="_blank" rel="noreferrer">
              Get a key
            </a>
          )}
        </p>
      </div>
      <div className="setting-control">
        <span className={`api-key-dot${provider.has_api_key ? ' on' : ''}`} />
        <input
          type="password"
          value={value}
          spellCheck={false}
          autoComplete="off"
          placeholder={provider.has_api_key ? 'Replace saved key' : 'Paste API key'}
          onChange={(e) => setValue(e.target.value)}
        />
        <button disabled={busy || value.trim() === ''} onClick={() => void save()}>
          Save
        </button>
        {provider.has_api_key && (
          <button className="setting-reset" disabled={busy} onClick={() => void remove()}>
            Remove
          </button>
        )}
      </div>
      {error && <p className="widget-error">{error}</p>}
    </div>
  );
}
