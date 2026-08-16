import { useCallback, useEffect, useRef, useState } from 'react';

import { usePaneSection } from '../../layout/use-sections';
import { getHardware, refreshHardware, type Hardware } from '../hardware/api';
import {
  deleteModel,
  downloadModel,
  formatBytes,
  formatParams,
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
} from './api';
import { OffloadPreview } from './OffloadPreview';
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

/** The probe's number as a person reads it — 999 is the sentinel for "all of them". */
function tuned(value: number | undefined): string {
  if (value === undefined) return 'auto';
  return value === 999 ? 'all' : String(value);
}

/**
 * What the machine is, above the controls that depend on it.
 *
 * The probe's findings used to live only in Settings, three clicks from the form
 * whose defaults they decide. Rendering the reading here is what makes a CPU build
 * on a machine with a card explicable rather than mysterious.
 *
 * The rule inherited from the hardware module: **"unknown" is never drawn as
 * "none"** — an absent `nvidia-smi` and an absent GPU are different facts.
 */
function MachineLine({
  hardware,
  onReprobe,
}: {
  hardware: Hardware | null;
  onReprobe: () => void;
}) {
  if (!hardware) return null;
  const { profile } = hardware;
  const primary = profile.primary;
  return (
    <p className="llama-machine">
      {primary ? (
        <>
          <span className="llama-dot llama-dot-on" />
          <b>{primary.name}</b>
          {primary.vramMb !== null && (
            <>
              {' · '}
              {(primary.vramMb / 1024).toFixed(primary.vramMb >= 10_240 ? 0 : 1)} GB
              {primary.unified ? ' unified' : ' VRAM'}
            </>
          )}
        </>
      ) : profile.certain ? (
        <>
          <span className="llama-dot" />
          No accelerator — this machine runs on its CPU.
        </>
      ) : (
        <>
          <span className="llama-dot" />
          <b>Accelerator unknown</b> — the probe could not ask, which is not the same as none.
        </>
      )}
      {profile.overridden && <span className="llama-tag">your override</span>}
      <button className="llama-linkbtn" onClick={onReprobe}>
        Re-probe
      </button>
    </p>
  );
}

function ServerSection({
  status,
  models,
  hardware,
  reprobe,
  refresh,
}: {
  status: LlamaStatus | null;
  models: ModelEntry[];
  hardware: Hardware | null;
  reprobe: () => void;
  refresh: () => void;
}) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState('');
  // `auto` and null, never 'cpu' and 0: the backend resolves those through the
  // hardware probe (`routes.py` — `variant in ("", "auto")`, `gpuLayers is None`),
  // and a form that always sends a concrete value silently bypasses the probe
  // entirely. An explicit 0 remains meaningful — it means pure CPU on purpose.
  const [variant, setVariant] = useState('auto');
  const [selected, setSelected] = useState('');
  const [contextSize, setContextSize] = useState(4096);
  const [gpuLayers, setGpuLayers] = useState<number | null>(null);
  const [threads, setThreads] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const modelPath = selected || models[0]?.path || '';
  const defaults = hardware?.defaults;
  const reasons = defaults?.reasons ?? {};
  const model = models.find((m) => m.path === modelPath);
  const accelerator = hardware?.profile.primary ?? null;
  // What would actually be sent: the typed value, or the probe's answer when the
  // field is on auto. 999 is the probe's "all of them" sentinel, and the preview
  // needs a real count, so it clamps against the file's own layer count.
  const effectiveLayers = gpuLayers ?? defaults?.gpuLayers ?? 0;
  // A context above what the weights were trained for is accepted by llama-server
  // and degrades quietly, so say it here where the number is typed.
  const overContext = !!model?.contextLength && contextSize > model.contextLength;

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
      const next = await startServer({ modelPath, contextSize, gpuLayers, threads });
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
        <MachineLine hardware={hardware} onReprobe={reprobe} />
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
          <select
            value={variant}
            onChange={(e) => setVariant(e.target.value)}
            disabled={busy}
            title={reasons.llamaVariant}
          >
            <option value="auto">
              {defaults ? `Auto — ${defaults.llamaVariant}` : 'Auto (recommended)'}
            </option>
            <option value="cpu">CPU (works everywhere)</option>
            <option value="cuda">CUDA (NVIDIA)</option>
            <option value="vulkan">Vulkan</option>
            <option value="hip">HIP / ROCm (AMD)</option>
            <option value="sycl">SYCL (Intel)</option>
          </select>
          <button onClick={() => void install()} disabled={busy}>
            {install0 ? 'Install latest' : 'Install'}
          </button>
        </div>
        {/* The reason is the whole difference between a settings change and a bug
            report, so it is shown rather than tucked into a tooltip. */}
        {variant === 'auto' && reasons.llamaVariant && (
          <p className="llama-why">{reasons.llamaVariant}</p>
        )}
        {variant !== 'auto' && (
          <p className="llama-why">
            Your choice, not the probe’s. A build whose runtime this machine cannot load fails at
            spawn.
          </p>
        )}
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
            {/* An empty box means "ask the probe" and sends null; a typed number is
                an instruction, including 0. That distinction is the whole point of
                the nullable request field — see StartOptions. */}
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
              <label title="Layers offloaded to the GPU. Empty asks the hardware probe; 0 is pure CPU on purpose. A CPU-only build ignores anything higher.">
                GPU layers
                <input
                  type="number"
                  min={0}
                  placeholder={tuned(defaults?.gpuLayers)}
                  value={gpuLayers ?? ''}
                  onChange={(e) =>
                    setGpuLayers(
                      Number.isNaN(e.target.valueAsNumber) ? null : e.target.valueAsNumber,
                    )
                  }
                />
              </label>
              <label title="Empty asks the hardware probe, which leaves one core for the rest of the app.">
                Threads
                <input
                  type="number"
                  min={1}
                  placeholder={tuned(defaults?.threads)}
                  value={threads ?? ''}
                  onChange={(e) =>
                    setThreads(Number.isNaN(e.target.valueAsNumber) ? null : e.target.valueAsNumber)
                  }
                />
              </label>
              <button
                onClick={() => void start()}
                disabled={busy || !modelPath || !status?.installed}
              >
                Start
              </button>
            </div>
            {gpuLayers === null && reasons.gpuLayers && (
              <p className="llama-why">
                {tuned(defaults?.gpuLayers)} layers on the GPU — {reasons.gpuLayers}
              </p>
            )}
            {gpuLayers === 0 && (
              <p className="llama-why">
                Pure CPU, because you asked for it — clear the box for the probe’s answer.
              </p>
            )}
            {/* The same number as the box above, as a picture of the stack it is
                dividing. Driven by the effective count so `auto` is shown rather
                than left blank, and dragging it makes the choice explicit. */}
            <OffloadPreview
              modelPath={modelPath}
              contextSize={contextSize}
              layers={effectiveLayers}
              vramMb={accelerator?.vramMb ?? null}
              unified={accelerator?.unified ?? false}
              isAuto={gpuLayers === null}
              onChange={setGpuLayers}
              onAuto={() => setGpuLayers(null)}
            />
            {overContext && (
              <p className="llama-note">
                {model?.name} was trained for {model?.contextLength?.toLocaleString()} tokens.
                llama-server accepts a larger window and the quality falls off past the trained one.
              </p>
            )}
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
  const [hardware, setHardware] = useState<Hardware | null>(null);
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

  // Deliberately not on the 3s poll: the probe is cached process-wide and only
  // changes when someone asks for it to (a settings override, the Re-probe button),
  // so re-fetching it every tick would be spawning nothing and reading the same
  // answer forever.
  const reprobe = useCallback(() => {
    void refreshHardware()
      .then((h) => alive.current && setHardware(h))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    void getHardware()
      .then((h) => alive.current && setHardware(h))
      .catch(() => undefined);
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
        <ServerSection
          status={status}
          models={models?.models ?? []}
          hardware={hardware}
          reprobe={reprobe}
          refresh={refresh}
        />
      )}
    </div>
  );
}
