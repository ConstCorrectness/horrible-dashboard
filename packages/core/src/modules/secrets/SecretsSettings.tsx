import { useEffect, useState, type FormEvent } from 'react';
import { apiGet, apiPut, apiPost, apiDelete } from '../../api';

interface ProvidersResponse {
  providers: string[];
}

export function SecretsSettings() {
  const [providers, setProviders] = useState<string[]>([]);
  const [newProvider, setNewProvider] = useState('');
  const [newKey, setNewKey] = useState('');
  const [status, setStatus] = useState<string | null>(null);

  // Google Integration State
  const [googleStatus, setGoogleStatus] = useState<{ configured: boolean; authenticated: boolean } | null>(null);
  const [syncStatus, setSyncStatus] = useState<string | null>(null);

  const load = async (): Promise<void> => {
    try {
      const res = await apiGet<ProvidersResponse>('/secrets');
      setProviders(res.providers || []);
      
      const gRes = await apiGet<{ configured: boolean; authenticated: boolean }>('/integrations/google/status');
      setGoogleStatus(gRes);
    } catch {
      // Backend down or error
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    if (!newProvider.trim() || !newKey.trim()) return;

    try {
      await apiPut('/secrets', {
        provider_name: newProvider.trim(),
        secret_value: newKey.trim(),
      });
      setNewProvider('');
      setNewKey('');
      setStatus('Key saved successfully.');
      setTimeout(() => setStatus(null), 3000);
      void load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDelete = async (provider: string) => {
    try {
      await apiDelete(`/secrets/${provider}`);
      void load();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  const handleGoogleSyncPost = async () => {
    try {
      setSyncStatus('Starting sync...');
      await apiPost('/integrations/google/sync', {});
      setSyncStatus('Sync queued successfully in background!');
      setTimeout(() => setSyncStatus(null), 3000);
    } catch (err) {
      setSyncStatus(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="secrets-settings">
      <div className="settings-section">
        <h3>API Keys & Providers</h3>
        <p className="settings-hint">
          Manage your API keys for external models (e.g., openai, gemini, anthropic).
          These are encrypted at rest using a local master key.
        </p>

        {status && <div className="settings-status">{status}</div>}

        <ul className="secrets-list">
          {providers.map((p) => (
            <li key={p}>
              <span className="secret-provider">{p}</span>
              <span className="secret-hidden">••••••••••••••••</span>
              <button onClick={() => void handleDelete(p)} className="secret-delete">
                Delete
              </button>
            </li>
          ))}
          {providers.length === 0 && <li className="secrets-empty">No keys configured yet.</li>}
        </ul>

        <form className="secrets-form" onSubmit={handleAdd}>
          <input
            type="text"
            placeholder="Provider name (e.g., openai)"
            value={newProvider}
            onChange={(e) => setNewProvider(e.target.value)}
          />
          <input
            type="password"
            placeholder="API Key"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
          />
          <button type="submit">Add / Update</button>
        </form>
      </div>

      <div className="settings-section" style={{ marginTop: '2rem' }}>
        <h3>Google Drive Integration</h3>
        <p className="settings-hint">
          Connect your Google Drive to automatically sync and index your documents for the RAG agent.
        </p>

        {!googleStatus?.configured ? (
          <p className="settings-hint" style={{ color: 'orange' }}>
            To enable Google Drive, you must first add <kbd>google_client_id</kbd> and <kbd>google_client_secret</kbd> in the API Keys section above.
          </p>
        ) : !googleStatus?.authenticated ? (
          <div>
            <p className="settings-hint">Configuration found. You must authorize the app.</p>
            <a href="/api/integrations/google/auth" className="secret-delete" style={{ display: 'inline-block', padding: '0.5rem 1rem', textDecoration: 'none', background: '#3b82f6', color: 'white', borderRadius: '4px' }}>
              Connect Google Drive
            </a>
          </div>
        ) : (
          <div>
            <p className="settings-hint" style={{ color: 'green' }}>✓ Google Drive is connected.</p>
            {syncStatus && <div className="settings-status">{syncStatus}</div>}
            <button onClick={handleGoogleSyncPost} style={{ padding: '0.5rem 1rem', background: '#10b981', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
              Sync Now
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
