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
      {providers.map((p) =>
        p.key_connector ? (
          <ConnectorKeyRow key={p.kind} provider={p} />
        ) : (
          <KeyRow key={p.kind} provider={p} onChanged={refresh} />
        ),
      )}
    </div>
  );
}

/**
 * A provider whose key belongs to a connector (NVIDIA NIM — the same key reaches
 * NGC's catalog, so a second copy here would be a second thing to rotate and leak).
 * Shown rather than hidden: before this, a keyless NIM was not reported at all, so
 * nothing anywhere said NVIDIA was a provider this node can use. The row states
 * where the credential lives instead of offering a field the backend would refuse.
 */
function ConnectorKeyRow({ provider }: { provider: DetectedProvider }) {
  return (
    <div className="setting-row">
      <div className="setting-label">
        <label>{provider.label}</label>
        <p className="setting-desc">
          {provider.has_api_key ? (
            <>
              Connected.{' '}
              {provider.models.length > 0 && `${provider.models.length} models offered. `}
            </>
          ) : (
            <>Not connected — this provider is not selectable yet. </>
          )}
          Its key is held by the <code>{provider.key_connector}</code> connector, on the home page,
          because the same key also reaches NVIDIA&rsquo;s model catalog.{' '}
          {provider.api_key_url && (
            <a href={provider.api_key_url} target="_blank" rel="noreferrer">
              Get a key
            </a>
          )}
        </p>
      </div>
      <div className="setting-control">
        <span className={`api-key-dot${provider.has_api_key ? ' on' : ''}`} />
      </div>
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
