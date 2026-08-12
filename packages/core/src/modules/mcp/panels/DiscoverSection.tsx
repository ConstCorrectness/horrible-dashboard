import { useCallback, useEffect, useState } from 'react';

import {
  discoverServers,
  probeServer,
  saveServer,
  toServerInput,
  type McpCatalogEntry,
  type McpInstallOption,
  type McpProbe,
} from '../api';

/**
 * Find a server, look inside it, then decide.
 *
 * The order is the point. A registry entry's description is marketing written by its
 * publisher; **Inspect** connects the real thing once, in a scratch session that is
 * never saved and never registers an agent tool, and shows its actual tools, its
 * `readOnlyHint` annotations and its own instructions. Adding a server used to mean
 * committing to it and finding out afterwards.
 *
 * Two things are deliberately loud. Inspecting a package option *runs third-party code
 * on this machine* — the same act as adding it, minus the persistence — so it says so.
 * And a secret an entry declares is collected into a separate field that never reaches
 * the config file, because `env` is persisted in the clear.
 */

function Badge({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return (
    <span
      style={{
        border: `1px solid ${tone ?? 'var(--border)'}`,
        borderRadius: 4,
        padding: '0 0.25rem',
        fontSize: '0.65rem',
        color: tone ?? 'var(--text-dim)',
      }}
    >
      {children}
    </span>
  );
}

function ProbeResult({ probe }: { probe: McpProbe }) {
  if (!probe.ok) {
    return (
      <div style={{ color: 'var(--danger, #f85149)', fontSize: '0.72rem' }}>
        {probe.error}
        {probe.messages.length > 0 && (
          <div style={{ color: 'var(--text-dim)', marginTop: '0.2rem' }}>
            {probe.messages.length} wire messages before it failed — the last was{' '}
            <code>{probe.messages[probe.messages.length - 1]?.method}</code>.
          </div>
        )}
      </div>
    );
  }
  return (
    <div style={{ fontSize: '0.72rem', marginTop: '0.3rem' }}>
      <div>
        <strong>
          {probe.serverName} {probe.serverVersion}
        </strong>{' '}
        — {probe.tools.length} tools, {probe.prompts.length} prompts, {probe.resources.length}{' '}
        resources
      </div>
      {probe.instructions && (
        <div style={{ color: 'var(--text-dim)', margin: '0.2rem 0' }}>
          {probe.instructions.slice(0, 300)}
          {probe.instructions.length > 300 ? '…' : ''}
        </div>
      )}
      <ul style={{ margin: '0.2rem 0 0', paddingLeft: '1rem' }}>
        {probe.tools.map((t) => (
          <li key={t.name}>
            <code>{t.name}</code>{' '}
            <span style={{ color: 'var(--text-dim)' }}>
              {t.readOnly ? '(read-only)' : '(gated)'} {t.description.slice(0, 120)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EntryCard({ entry, onAdded }: { entry: McpCatalogEntry; onAdded: () => void }) {
  const [choice, setChoice] = useState(0);
  const [id, setId] = useState(entry.suggestedId);
  const [extraArgs, setExtraArgs] = useState('');
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [probe, setProbe] = useState<McpProbe | null>(null);
  const [busy, setBusy] = useState<'' | 'probe' | 'add'>('');
  const [error, setError] = useState<string | null>(null);

  const option: McpInstallOption | undefined = entry.installs[choice];

  const input = useCallback(() => {
    if (!option) return null;
    const base = toServerInput(entry, option, { id });
    return {
      ...base,
      // Whitespace-split, the same as the manual form: this is what a user pastes
      // from a README, and the filesystem server's allowed directories arrive here.
      args: [...(base.args ?? []), ...(extraArgs.trim() ? extraArgs.trim().split(/\s+/) : [])],
      secretEnvValues: secrets,
    };
  }, [entry, option, id, extraArgs, secrets]);

  const run = async (kind: 'probe' | 'add') => {
    const payload = input();
    if (!payload) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === 'probe') {
        setProbe(await probeServer(payload));
      } else {
        await saveServer(payload);
        onAdded();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy('');
    }
  };

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: '0.5rem 0.65rem',
        marginBottom: '0.45rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
        <strong>{entry.title}</strong>
        {entry.source === 'curated' && <Badge tone="var(--ok, #3fb950)">curated</Badge>}
        {entry.version && <Badge>{entry.version}</Badge>}
        <code style={{ fontSize: '0.65rem', color: 'var(--text-dim)' }}>{entry.name}</code>
      </div>
      <div style={{ fontSize: '0.72rem', marginTop: '0.15rem' }}>{entry.description}</div>
      {entry.note && (
        <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: '0.15rem' }}>
          {entry.note}
        </div>
      )}

      {entry.installs.length === 0 && (
        <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: '0.3rem' }}>
          This entry describes no package or remote this node can run.
        </div>
      )}

      {option && (
        <div style={{ marginTop: '0.35rem', fontSize: '0.72rem' }}>
          <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {entry.installs.length > 1 && (
              <select value={choice} onChange={(e) => setChoice(Number(e.target.value))}>
                {entry.installs.map((o, i) => (
                  <option key={o.label} value={i}>
                    {o.label}
                  </option>
                ))}
              </select>
            )}
            <input
              value={id}
              onChange={(e) => setId(e.target.value)}
              style={{ width: 130 }}
              placeholder="id"
            />
            <code style={{ color: 'var(--text-dim)' }}>
              {option.kind === 'remote'
                ? `${option.transport} ${option.url}`
                : [option.command, ...option.args].join(' ')}
            </code>
          </div>

          {option.kind === 'package' && (
            <input
              value={extraArgs}
              onChange={(e) => setExtraArgs(e.target.value)}
              placeholder="extra arguments (e.g. a directory to allow)"
              style={{ width: '100%', marginTop: '0.25rem' }}
            />
          )}

          {option.env.map((v) => (
            <div key={v.name} style={{ marginTop: '0.25rem' }}>
              <label style={{ color: 'var(--text-dim)' }}>
                {v.name}
                {v.required ? ' *' : ''} {v.secret ? '(secret)' : ''}
              </label>
              {v.secret ? (
                <input
                  type="password"
                  value={secrets[v.name] ?? ''}
                  onChange={(e) => setSecrets({ ...secrets, [v.name]: e.target.value })}
                  placeholder={v.description || v.name}
                  style={{ width: '100%' }}
                />
              ) : (
                <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>
                  {v.description || 'set this in the server’s environment after adding'}
                </div>
              )}
            </div>
          ))}

          {option.unsupported ? (
            <div style={{ color: 'var(--warn, #d29922)', marginTop: '0.3rem' }}>
              Can&rsquo;t install from here: {option.unsupported}
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '0.35rem', marginTop: '0.35rem' }}>
              <button disabled={busy !== '' || !id} onClick={() => void run('probe')}>
                {busy === 'probe' ? 'Connecting…' : 'Inspect'}
              </button>
              <button disabled={busy !== '' || !id} onClick={() => void run('add')}>
                Add
              </button>
              {option.kind === 'package' && (
                <span style={{ color: 'var(--text-dim)', alignSelf: 'center' }}>
                  Inspect runs this package on your machine.
                </span>
              )}
            </div>
          )}
        </div>
      )}

      {error && <div style={{ color: 'var(--danger, #f85149)', fontSize: '0.72rem' }}>{error}</div>}
      {probe && <ProbeResult probe={probe} />}
    </div>
  );
}

export function DiscoverSection({ onAdded }: { onAdded: () => void }) {
  const [query, setQuery] = useState('');
  const [entries, setEntries] = useState<McpCatalogEntry[]>([]);
  const [online, setOnline] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const search = useCallback(async (q: string) => {
    setLoading(true);
    try {
      const res = await discoverServers(q);
      setEntries(res.entries);
      setOnline(res.registryOnline);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void search('');
  }, [search]);

  return (
    <div>
      <div style={{ display: 'flex', gap: '0.35rem', marginBottom: '0.5rem' }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void search(query);
          }}
          placeholder="Search the MCP registry"
          style={{ flex: 1 }}
        />
        <button onClick={() => void search(query)}>Search</button>
      </div>
      {/* A degraded list and an empty one look identical unless you say which. */}
      {!online && !loading && (
        <div style={{ color: 'var(--warn, #d29922)', fontSize: '0.72rem', marginBottom: '0.4rem' }}>
          The registry didn&rsquo;t answer — showing the shipped list only.
        </div>
      )}
      {loading && <div style={{ color: 'var(--text-dim)' }}>Searching…</div>}
      {error && <div style={{ color: 'var(--danger, #f85149)' }}>{error}</div>}
      {!loading && entries.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>Nothing matched.</div>
      )}
      {entries.map((e) => (
        <EntryCard key={`${e.source}:${e.name}`} entry={e} onAdded={onAdded} />
      ))}
    </div>
  );
}
