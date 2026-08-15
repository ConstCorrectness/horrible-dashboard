import { useCallback, useEffect, useRef, useState } from 'react';

import { usePaneSection } from '../../layout/use-sections';
import {
  deleteModel,
  downloadModel,
  formatBytes,
  formatParams,
  getInstallVariants,
  getLlamaModels,
  getLlamaStatus,
  getRepoFiles,
  installServer,
  startServer,
  stopServer,
  type LlamaStatus,
  type ModelEntry,
  type ModelsResponse,
  type Progress,
  type RepoFile,
  type VariantAvailability,
} from './api';
import { TracesSection } from './TracesSection';

/**
 * The llama.cpp pane: the binary, the weights, and the running server.
 *
 * Three sections rather than three panes because they are one workflow with a
 * strict order — you cannot serve a model without a build, and you cannot pick a
 * model you haven't got. Splitting them would mean three panes of which two are
 * usually an instruction to go open another one.
 *
 * Everything reads from one `/llamacpp/status` poll, so the pane cannot show a
 * server as running while the model list still offers to delete what it loaded.
 */

function pct(p: Progress): number | null {
  if (!p.total || !p.completed) return null;
  return Math.min(100, Math.round((p.completed / p.total) * 100));
}

function ProgressBar({ progress }: { progress: Progress }) {
  const value = pct(progress);
  return (
    <div className="llama-progress">
      <div className="llama-progress-track">
        <div
          className={`llama-progress-fill${value === null ? ' llama-progress-idle' : ''}`}
          style={value === null ? undefined : { width: `${value}%` }}
        />
      </div>
      <span className="llama-progress-label">
        {progress.status ?? 'working'}
        {value !== null ? ` · ${value}%` : ''}
        {progress.total
          ? ` · ${formatBytes(progress.completed ?? 0)} / ${formatBytes(progress.total)}`
          : ''}
      </span>
    </div>
  );
}

function ServerSection({
  status,
  models,
  refresh,
}: {
  status: LlamaStatus | null;
  models: ModelEntry[];
  refresh: () => void;
}) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState('');
  const [variant, setVariant] = useState('cpu');
  const [selected, setSelected] = useState('');
  const [contextSize, setContextSize] = useState(4096);
  const [gpuLayers, setGpuLayers] = useState(0);
  const [busy, setBusy] = useState(false);
  const [availability, setAvailability] = useState<VariantAvailability | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getInstallVariants().then(
      (info) => {
        if (!cancelled) setAvailability(info);
      },
      () => {
        // A failed lookup leaves every option enabled — an unknown answer must
        // not be shown as "unavailable", which would misrepresent the release.
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const modelPath = selected || models[0]?.path || '';

  const install = async () => {
    setBusy(true);
    setError('');
    setProgress({ status: 'starting' });
    try {
      await installServer('latest', variant, (p) => {
        if (p.error) setError(String(p.error));
        else setProgress(p);
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setProgress(null);
      refresh();
    }
  };

  const start = async () => {
    if (!modelPath) return;
    setBusy(true);
    setError('');
    try {
      const next = await startServer({ modelPath, contextSize, gpuLayers });
      if (!next.ready && next.error) setError(next.error);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await stopServer();
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const install0 = status?.install ?? null;

  return (
    <div className="llama-section">
      <div className="llama-card">
        <h3>Build</h3>
        {install0 ? (
          <p className="llama-meta">
            <code>{install0.tag}</code> · {install0.variant} · {formatBytes(install0.sizeBytes)}{' '}
            {install0.verified ? (
              <span className="llama-tag llama-ok" title={`sha256 ${install0.sha256}`}>
                verified
              </span>
            ) : (
              <span
                className="llama-tag"
                title={`GitHub published no digest for this asset. Recorded sha256 ${install0.sha256}`}
              >
                unverified
              </span>
            )}
          </p>
        ) : (
          <p className="llama-meta">
            No <code>llama-server</code> installed. The build is downloaded from the upstream
            llama.cpp releases and unpacked into your data directory — nothing is bundled.
          </p>
        )}
        <div className="llama-row">
          <select value={variant} onChange={(e) => setVariant(e.target.value)} disabled={busy}>
            {(
              [
                ['cpu', 'CPU (works everywhere)'],
                ['cuda', 'CUDA (NVIDIA)'],
                ['vulkan', 'Vulkan'],
                ['hip', 'HIP / ROCm (AMD)'],
                ['sycl', 'SYCL (Intel)'],
              ] as const
            ).map(([value, label]) => {
              const available = availability?.variants[value];
              return (
                <option key={value} value={value} disabled={available === false}>
                  {label}
                  {available === false ? ` — no ${availability?.tag} build for this platform` : ''}
                </option>
              );
            })}
          </select>
          <button onClick={() => void install()} disabled={busy}>
            {install0 ? 'Install latest' : 'Install'}
          </button>
        </div>
        {availability?.error && <p className="llama-note">{availability.error}</p>}
        {progress && <ProgressBar progress={progress} />}
      </div>

      <div className="llama-card">
        <h3>Server</h3>
        {status?.running ? (
          <>
            <p className="llama-meta">
              <span className={`llama-dot${status.ready ? ' llama-dot-on' : ''}`} />
              {status.ready ? 'Ready' : 'Loading the model…'} · <code>{status.model}</code> ·{' '}
              {status.endpoint} · pid {status.pid}
            </p>
            {!status.isAgentProvider && (
              <p className="llama-note">
                Your agent is not pointed at this server. Set the provider to <b>llama.cpp</b> in
                Settings → Agent orchestrator (or per agent) to use it.
              </p>
            )}
            <button onClick={() => void stop()} disabled={busy}>
              Stop
            </button>
          </>
        ) : (
          <>
            <div className="llama-row">
              <select
                value={modelPath}
                onChange={(e) => setSelected(e.target.value)}
                disabled={busy || !models.length}
              >
                {!models.length && <option value="">No GGUF found</option>}
                {models.map((m) => (
                  <option key={m.path} value={m.path}>
                    {m.name} · {formatBytes(m.sizeBytes)} · {m.origin}
                  </option>
                ))}
              </select>
            </div>
            <div className="llama-row">
              <label>
                Context
                <input
                  type="number"
                  min={512}
                  step={512}
                  value={contextSize}
                  onChange={(e) => setContextSize(e.target.valueAsNumber || 4096)}
                />
              </label>
              <label title="Layers offloaded to the GPU. 0 = pure CPU; a CPU-only build ignores anything higher.">
                GPU layers
                <input
                  type="number"
                  min={0}
                  value={gpuLayers}
                  onChange={(e) => setGpuLayers(e.target.valueAsNumber || 0)}
                />
              </label>
              <button
                onClick={() => void start()}
                disabled={busy || !modelPath || !status?.installed}
              >
                Start
              </button>
            </div>
            {!status?.installed && <p className="llama-note">Install a build first.</p>}
          </>
        )}
        {error && <p className="llama-error">{error}</p>}
        {status?.error && !error && <p className="llama-error">{status.error}</p>}
      </div>

      {!!status?.logs.length && (
        <div className="llama-card llama-logs">
          <h3>Log</h3>
          <pre>{status.logs.slice(-40).join('\n')}</pre>
        </div>
      )}
    </div>
  );
}

function ModelsSection({ data, refresh }: { data: ModelsResponse | null; refresh: () => void }) {
  const [repo, setRepo] = useState('');
  const [files, setFiles] = useState<RepoFile[]>([]);
  const [lookupError, setLookupError] = useState('');
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const lookup = async (target: string) => {
    setBusy(true);
    setLookupError('');
    setFiles([]);
    try {
      const res = await getRepoFiles(target);
      if (res.error) setLookupError(res.error);
      setFiles(res.files.filter((f) => !f.isProjector));
    } catch (err) {
      setLookupError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const download = async (file: string) => {
    setBusy(true);
    setError('');
    setProgress({ status: 'starting' });
    try {
      await downloadModel(repo, file, (p) => {
        if (p.error) setError(String(p.error));
        else setProgress(p);
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setProgress(null);
      refresh();
    }
  };

  const remove = async (path: string) => {
    setError('');
    try {
      await deleteModel(path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      refresh();
    }
  };

  const used = data?.usedBytes ?? 0;
  const budget = data?.budgetBytes ?? 0;

  return (
    <div className="llama-section">
      <div className="llama-card">
        <h3>Download a GGUF</h3>
        <div className="llama-row">
          <input
            value={repo}
            placeholder="Hugging Face repo, e.g. bartowski/Llama-3.2-3B-Instruct-GGUF"
            onChange={(e) => setRepo(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && repo.trim()) void lookup(repo.trim());
            }}
          />
          <button onClick={() => void lookup(repo.trim())} disabled={busy || !repo.trim()}>
            List files
          </button>
        </div>
        {!files.length && !lookupError && (
          <div className="llama-suggested">
            {(data?.suggested ?? []).map((s) => (
              <button
                key={s.repo}
                className="llama-suggest"
                onClick={() => {
                  setRepo(s.repo);
                  void lookup(s.repo);
                }}
                title={s.note}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
        {lookupError && <p className="llama-error">{lookupError}</p>}
        {!!files.length && (
          <ul className="llama-files">
            {files.map((f) => (
              <li key={f.path}>
                <code>{f.path}</code>
                <span>{formatBytes(f.sizeBytes)}</span>
                <button onClick={() => void download(f.path)} disabled={busy}>
                  Download
                </button>
              </li>
            ))}
          </ul>
        )}
        {progress && <ProgressBar progress={progress} />}
        {error && <p className="llama-error">{error}</p>}
      </div>

      <div className="llama-card">
        <h3>
          On this machine{' '}
          <span className="llama-meta">
            {formatBytes(used)}
            {budget ? ` of ${formatBytes(budget)} budget` : ''}
          </span>
        </h3>
        <ul className="llama-models">
          {(data?.models ?? []).map((m) => (
            <li key={m.path}>
              <div className="llama-model-head">
                <span className="llama-model-name">{m.name}</span>
                <span className={`llama-tag llama-origin-${m.origin}`}>{m.origin}</span>
              </div>
              <div className="llama-meta">
                {m.architecture || 'unknown arch'} · {formatParams(m.parameters)} params ·{' '}
                {m.quantization || '—'} · {formatBytes(m.sizeBytes)}
                {m.contextLength ? ` · ${m.contextLength.toLocaleString()} ctx` : ''}
              </div>
              <div className="llama-model-path" title={m.path}>
                {m.path}
              </div>
              {m.error && <div className="llama-error">{m.error}</div>}
              {m.deletable && (
                <button className="llama-danger" onClick={() => void remove(m.path)}>
                  Delete
                </button>
              )}
            </li>
          ))}
          {!data?.models.length && (
            <li className="llama-meta">
              No GGUF files found. Download one above, or point <code>llamacpp.modelDirs</code> at a
              folder you already have.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}

export function LlamaCppPane() {
  const { section } = usePaneSection();
  const [status, setStatus] = useState<LlamaStatus | null>(null);
  const [models, setModels] = useState<ModelsResponse | null>(null);
  // A status poll that resolves after the pane unmounted (or after a newer one)
  // must not write state — the classic "stopped server shows as running" flicker.
  const alive = useRef(true);

  const refresh = useCallback(() => {
    void getLlamaStatus()
      .then((s) => alive.current && setStatus(s))
      .catch(() => undefined);
    void getLlamaModels()
      .then((m) => alive.current && setModels(m))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    // Loading a large GGUF takes tens of seconds and the log tail is the only
    // sign of progress, so poll while the pane is open.
    const timer = window.setInterval(refresh, 3000);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);

  return (
    <div className="llama-pane">
      {section === 'traces' ? (
        <TracesSection models={models?.models ?? []} />
      ) : section === 'models' ? (
        <ModelsSection data={models} refresh={refresh} />
      ) : (
        <ServerSection status={status} models={models?.models ?? []} refresh={refresh} />
      )}
    </div>
  );
}
