import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { usePaneSection } from '../../layout/use-sections';
import { ProgressBar } from '../../viz/ProgressBar';
import { RollingCounter } from '../../viz/RollingCounter';
import { Meter } from '../../viz/Meter';
import { MachineBand } from './MachineBand';
import { QuantScatter } from './QuantScatter';
import { getHardware, refreshHardware, type Hardware } from '../hardware/api';
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
  removeInstall,
  startServer,
  stopServer,
  type LlamaStatus,
  type ModelEntry,
  type ModelsResponse,
  type Progress,
  type RepoFile,
  type VariantAvailability,
} from './api';
import { LensSection } from './lens/LensSection';
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

/**
 * The shared progress bar, fed llama.cpp's NDJSON frames.
 *
 * `rate` is on for both call sites because both are downloads measured in bytes —
 * an unpacked build is ~200 MB and a GGUF can be forty times that, and a bar with
 * only a percentage gives no way to decide whether to wait for it.
 */
function Progressing({ progress }: { progress: Progress }) {
  return (
    <ProgressBar
      completed={progress.completed}
      total={progress.total}
      status={progress.status}
      detail={
        progress.total
          ? `${formatBytes(progress.completed ?? 0)} / ${formatBytes(progress.total)}`
          : undefined
      }
      rate
      formatRate={formatBytes}
    />
  );
}

/** The probe's number as a person reads it — 999 is the sentinel for "all of them". */
function tuned(value: number | undefined): string {
  if (value === undefined) return 'auto';
  return value === 999 ? 'all' : String(value);
}

/**
 * Uptime, at the resolution a person actually reads.
 *
 * Coarsens as it grows: seconds matter while you are waiting for a model to load,
 * and nobody reads the seconds off a server that has been up for two days.
 */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${total % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/**
 * Spawned → loading → ready, as three states rather than one sentence.
 *
 * `running` and `ready` are separate fields for a real reason: `llama-server`
 * binds its port and then spends tens of seconds mapping a large GGUF, and every
 * request in that window fails. Collapsing them made a normal load look broken and
 * gave no way to tell it apart from a server that is genuinely stuck.
 */
function ReadyStrip({ status }: { status: LlamaStatus }) {
  const failed = !!status.error;
  const stage = failed ? 'error' : status.ready ? 'ready' : 'loading';
  const steps: { id: string; label: string }[] = [
    { id: 'spawned', label: 'Process up' },
    { id: 'loading', label: 'Mapping weights' },
    { id: 'ready', label: 'Answering' },
  ];
  const reached = stage === 'ready' ? 3 : 2;

  return (
    <div className="llama-ready" data-stage={stage}>
      <ol>
        {steps.map((step, index) => (
          <li
            key={step.id}
            className={
              failed && index === reached - 1
                ? 'llama-ready-bad'
                : index < reached
                  ? 'llama-ready-on'
                  : ''
            }
          >
            <span className="llama-dot" />
            {step.label}
          </li>
        ))}
      </ol>
      {stage === 'loading' && !failed && (
        <span className="llama-meta">
          The port is open and requests will fail until this finishes.
        </span>
      )}
    </div>
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

  const removeBuild = async (tag: string, variant: string) => {
    setBusy(true);
    setError('');
    try {
      await removeInstall(tag, variant);
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
      {/* The machine is its own card above the build, not a line inside it: the
          build is chosen FOR the machine, so the reading has to come first. */}
      <div className="llama-card">
        <MachineBand hardware={hardware} onReprobe={reprobe} />
      </div>

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
          <select
            value={variant}
            onChange={(e) => setVariant(e.target.value)}
            disabled={busy}
            title={reasons.llamaVariant}
          >
            {/* `auto` is never greyed out: it names no asset of its own — the probe
                resolves it to one at install time — so availability cannot answer for it. */}
            <option value="auto">
              {defaults ? `Auto — ${defaults.llamaVariant}` : 'Auto (recommended)'}
            </option>
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
        {availability?.error && <p className="llama-note">{availability.error}</p>}
        {progress && <Progressing progress={progress} />}
        {/* Every unpacked build, not only the active one. `status.installs` has
            always been fetched and never listed, so a machine that collected three
            builds across variant changes showed one and offered no way to reclaim
            the other two — `POST /install/remove` had no caller at all. */}
        {status && status.installs.length > 1 && (
          <ul className="llama-installs">
            {status.installs.map((entry) => {
              const active = entry.tag === install0?.tag && entry.variant === install0?.variant;
              return (
                <li key={`${entry.tag}:${entry.variant}`} className={active ? 'llama-on' : ''}>
                  <code>{entry.tag}</code>
                  <span className="llama-tag">{entry.variant}</span>
                  <span className="llama-meta">{formatBytes(entry.sizeBytes)}</span>
                  {active && <span className="llama-tag llama-ok">active</span>}
                  {/* The backend refuses to remove a build whose server is up, so
                      the button says so rather than offering the user a 409. */}
                  <button
                    className="llama-linkbtn"
                    disabled={busy || status.running}
                    title={
                      status.running
                        ? 'Stop the running server before removing a build'
                        : `Delete ${entry.path}`
                    }
                    onClick={() => void removeBuild(entry.tag, entry.variant)}
                  >
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="llama-card">
        <h3>Server</h3>
        {status?.running ? (
          <>
            {/* `running` and `ready` are deliberately distinct in the API — loading
                a large GGUF takes tens of seconds during which the process is up and
                every request fails — and this used to collapse them into one
                sentence, so "started but not answering yet" read as a fault. */}
            <ReadyStrip status={status} />
            <p className="llama-meta">
              <code>{status.model}</code> · {status.endpoint} · pid {status.pid} · up{' '}
              <RollingCounter value={status.uptimeSeconds} format={formatDuration} />
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
          <h3>
            Log{' '}
            {/* Say that this is a tail. A silent slice makes a truncated log look
                like the whole one, which is how a startup error scrolls off the top
                and takes the explanation with it. */}
            <span className="llama-meta">
              last {Math.min(40, status.logs.length)} of {status.logs.length} lines
            </span>
          </h3>
          <pre>{status.logs.slice(-40).join('\n')}</pre>
        </div>
      )}
    </div>
  );
}

/** What each origin means — the bare word does not say where the file lives. */
const ORIGIN_LABEL: Record<string, string> = {
  managed: 'Downloaded here',
  ollama: 'The Ollama store',
  lmstudio: 'The LM Studio store',
  extra: 'Your extra directories',
};

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
  /** Which row the pointer is on — shared by the meter and the list, both ways. */
  const [hover, setHover] = useState<string | null>(null);

  const groups = useMemo(() => {
    const by = new Map<string, ModelEntry[]>();
    for (const m of data?.models ?? []) {
      const list = by.get(m.origin) ?? [];
      list.push(m);
      by.set(m.origin, list);
    }
    // `managed` first: it is the only group whose files this app owns, and so the
    // only one where a Delete button appears at all.
    return [...by.entries()].sort(([a], [b]) =>
      a === 'managed' ? -1 : b === 'managed' ? 1 : a.localeCompare(b),
    );
  }, [data]);

  // Relative to the longest context in the catalogue, not to an absolute maximum:
  // the comparison worth drawing is between the files you actually have.
  const longestContext = Math.max(1, ...(data?.models ?? []).map((m) => m.contextLength ?? 0));

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
        {progress && <Progressing progress={progress} />}
        {error && <p className="llama-error">{error}</p>}
      </div>

      <div className="llama-card">
        <h3>
          On this machine{' '}
          <span className="llama-meta">
            {data?.models.length ?? 0} files · {formatBytes(used)}
            {budget ? ` of ${formatBytes(budget)}` : ''}
          </span>
        </h3>

        {/* The budget as a proportion rather than a sentence. One segment per
            model, so the bar also answers "what is taking the room" — hovering a
            segment lights its row, and the row lights its segment. */}
        {!!data?.models.length && (
          <Meter
            label={`${formatBytes(used)} of ${formatBytes(budget)} used`}
            total={used}
            threshold={budget || null}
            thresholdLabel={`${formatBytes(budget)} budget (llamacpp.diskBudgetGb)`}
            segments={data.models.map((m) => ({
              value: m.sizeBytes,
              tone: m.origin === 'managed' ? ('primary' as const) : ('muted' as const),
              label: `${m.name} — ${formatBytes(m.sizeBytes)} (${m.origin})`,
              active: hover === m.path,
              onHover: (entering: boolean) => setHover(entering ? m.path : null),
            }))}
          >
            <p className="llama-meta">
              Filled segments are files this app manages. The rest are read where Ollama and LM
              Studio already keep them — serveable, never touched, and not deletable from here.
            </p>
          </Meter>
        )}

        {/* Bytes per parameter, which is what a quantization name means. */}
        <QuantScatter models={data?.models ?? []} />

        {/* Grouped by origin, each group's root named. "Why is this here" and "why
            can I not delete it" are the same question, and `origin` is the whole
            answer — it was a bare tag on a row before, sorted next to nothing. */}
        {groups.map(([origin, entries]) => (
          <section key={origin} className="llama-origin-group">
            <div className="llama-band-head">
              <h4>{ORIGIN_LABEL[origin] ?? origin}</h4>
              <span className="llama-meta">
                {entries.length} · {formatBytes(entries.reduce((n, m) => n + m.sizeBytes, 0))}
              </span>
              {origin === 'managed' && !!data?.root && (
                <code className="llama-model-path" title={data.root}>
                  {data.root}
                </code>
              )}
              {origin === 'extra' && !!data?.extraDirs.length && (
                <code className="llama-model-path" title={data.extraDirs.join(', ')}>
                  {data.extraDirs.join(' · ')}
                </code>
              )}
            </div>
            <ul className="llama-models">
              {entries.map((m) => (
                <li
                  key={m.path}
                  className={hover === m.path ? 'llama-on' : ''}
                  onMouseEnter={() => setHover(m.path)}
                  onMouseLeave={() => setHover(null)}
                >
                  <div className="llama-model-head">
                    <span className="llama-model-name">{m.name.split('/').pop() ?? m.name}</span>
                    <span className="llama-tag">{m.quantization || '—'}</span>
                    <span className="llama-meta">{formatBytes(m.sizeBytes)}</span>
                  </div>
                  <div className="llama-meta">
                    {m.architecture || 'unknown arch'} · {formatParams(m.parameters)} params
                  </div>
                  {/* The trained context, against the longest in the catalogue. The
                      Server tab warns you AFTER you type a number past it; this is
                      the same fact before you type anything. */}
                  {m.contextLength ? (
                    <div
                      className="llama-ctxrail"
                      title={`Trained for ${m.contextLength.toLocaleString()} tokens`}
                    >
                      <span style={{ width: `${(m.contextLength / longestContext) * 100}%` }} />
                      <em>{m.contextLength.toLocaleString()} ctx</em>
                    </div>
                  ) : (
                    <div className="llama-meta">context length not recorded in this file</div>
                  )}
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
            </ul>
          </section>
        ))}

        {!data?.models.length && (
          <p className="llama-meta">
            No GGUF files found. Download one above, or point <code>llamacpp.modelDirs</code> at a
            folder you already have.
          </p>
        )}
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
      {section === 'lens' ? (
        <LensSection />
      ) : section === 'traces' ? (
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
