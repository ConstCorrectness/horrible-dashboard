/**
 * Library: the interpretability artifacts this node has, can fetch, and can run.
 *
 * Three things every later tool (features, steering, lenses, circuits) stands on:
 *
 * - **The worker** — a process in its own CUDA venv holding one Hugging Face model.
 *   Installed and started from here; every op it answers shows its verdict: ran,
 *   failed (with the traceback), or not checked (and why).
 * - **Installed** — each artifact with what it states about itself and whether it
 *   fits the agent's GGUF and the worker's model: compatible, incompatible, or
 *   unknown. An SAE on the wrong model still produces numbers; this is where that
 *   is caught.
 * - **Get** — the curated catalog. Browsing reads sizes from the Hub; nothing is
 *   written until the size has been shown and confirmed.
 *
 * Plus a Neuronpedia lookup by feature id — read-only, public data, cached.
 */
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { RollingCounter } from '../../../viz/RollingCounter';
import { askInterp } from '../ask';
import { fmtBytes } from '../attention/model';
import { A } from '../attention/styles';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import { getFeature, type Artifact, type CatalogEntry, type Compat, type FeatureCard } from './api';
import { NeuronpediaCard } from './NeuronpediaCard';
import { LensInstall } from './LensInstall';
import {
  askDownload,
  browse,
  cancelDownload,
  confirmDownload,
  installGroup,
  op,
  refreshLibrary,
  removeArtifact,
  start,
  stop,
  useLibrary,
} from './state';

export function LibraryBody() {
  return (
    <SuiteFrame title="Library">
      <LibrarySection />
    </SuiteFrame>
  );
}

export function LibrarySection() {
  const st = useLibrary();
  useEffect(() => {
    void refreshLibrary();
  }, []);

  return (
    <div style={A.root}>
      {st.error && <div style={A.conflict}>{st.error}</div>}
      <div style={L.top}>
        <WorkerPanel />
        <Budget />
      </div>
      <Installed />
      <Catalog />
      <NeuronpediaLookup />
    </div>
  );
}

// ── the worker ───────────────────────────────────────────────────────────────

const GROUP_GLOSS: Record<string, string> = {
  base: 'torch (CUDA), transformers, accelerate, safetensors — enough to load and run a model',
  sae: 'sae-lens — SAE loading and encoding on the worker',
  circuits: 'circuit-tracer, nnsight — attribution graphs',
};

function WorkerPanel() {
  const st = useLibrary();
  const w = st.worker;
  const [repo, setRepo] = useState('Qwen/Qwen3-0.6B');
  const [prompt, setPrompt] = useState('The capital of France is');
  if (!w) return <section style={L.workerPanel}>Reading the worker…</section>;

  const env = w.env;
  const installing = env.state === 'installing';
  const loaded = w.status?.loaded ?? null;
  const vram = w.status?.vramAllocated;

  return (
    <section style={L.workerPanel} aria-label="Interpretability worker">
      <div style={A.panelHead}>
        <span style={S.heading}>Worker</span>
        <span style={L.stateTag(w.state)}>{w.state.replace('_', ' ')}</span>
        {w.pid && <span style={S.mono}>pid {w.pid}</span>}
      </div>

      {/* The environment: one venv, package groups added as tools need them. */}
      <div style={L.groups}>
        {env.available.map((g) => {
          const have = env.groups.includes(g);
          return (
            <div key={g} style={L.group(have)}>
              <span style={L.groupName}>{g}</span>
              <span style={L.groupGloss}>{GROUP_GLOSS[g] ?? ''}</span>
              {have ? (
                <span style={S.mono}>installed</span>
              ) : (
                <button
                  type="button"
                  disabled={installing || (g !== 'base' && !env.groups.includes('base'))}
                  onClick={() => void installGroup(g)}
                >
                  {installing && env.group === g ? 'Installing…' : 'Install'}
                </button>
              )}
            </div>
          );
        })}
      </div>
      {installing && <div style={L.logLine}>{env.line || 'resolving…'}</div>}
      {env.state === 'failed' && <div style={A.conflict}>{env.error}</div>}

      {w.state !== 'no_env' && (
        <>
          <div style={L.row}>
            {w.state === 'ready' ? (
              <button type="button" disabled={!!st.busy} onClick={() => void stop()}>
                Stop worker
              </button>
            ) : (
              <button type="button" disabled={!!st.busy} onClick={() => void start()}>
                {st.busy === 'start' ? 'Starting…' : 'Start worker'}
              </button>
            )}
            {w.ping && (
              <span style={S.mono}>
                torch {w.ping.torch} · {w.ping.cuda ? w.ping.device : 'CPU only'}
                {w.ping.vramBytes ? ` · ${fmtBytes(w.ping.vramBytes)} VRAM` : ''}
              </span>
            )}
          </div>
          {w.state === 'ready' && (
            <>
              <div style={L.row}>
                <input
                  type="text"
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  aria-label="Hugging Face model to load"
                  style={L.grow}
                  placeholder="org/model — from the Hugging Face cache"
                />
                <button
                  type="button"
                  disabled={!!st.busy || !repo.trim()}
                  title="Loads from the local Hugging Face cache only; a model that is not cached is declined, not downloaded"
                  onClick={() => void op('load', { repo: repo.trim() })}
                >
                  {st.busy === 'load' ? 'Loading…' : 'Load'}
                </button>
                {loaded && (
                  <button type="button" disabled={!!st.busy} onClick={() => void op('unload')}>
                    Unload
                  </button>
                )}
              </div>
              <div style={L.facts}>
                <Fact k="holding" v={loaded ? loaded.repo : 'nothing'} />
                {loaded && (
                  <>
                    <Fact k="shape" v={`${loaded.layers} layers · d_model ${loaded.dModel}`} />
                    <Fact k="dtype" v={`${loaded.dtype} on ${loaded.device}`} />
                  </>
                )}
                {typeof vram === 'number' && (
                  <Fact
                    k="vram"
                    v={
                      <RollingCounter
                        value={vram / 1024 ** 3}
                        format={(v) => `${v.toFixed(2)} GB`}
                      />
                    }
                  />
                )}
              </div>
              {loaded && (
                <div style={L.row}>
                  <input
                    type="text"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    aria-label="Prompt to continue"
                    style={L.grow}
                  />
                  <button
                    type="button"
                    disabled={!!st.busy}
                    onClick={() => void op('generate', { prompt, maxNewTokens: 24 })}
                  >
                    {st.busy === 'generate' ? 'Generating…' : 'Generate'}
                  </button>
                </div>
              )}
            </>
          )}
          {st.lastReply && <Verdict op={st.lastReply.op} reply={st.lastReply.reply} />}
          {w.state === 'crashed' && w.log.length > 0 && (
            <pre style={L.log}>{w.log.slice(-6).join('\n')}</pre>
          )}
        </>
      )}
    </section>
  );
}

function Verdict({
  op: name,
  reply,
}: {
  op: string;
  reply: NonNullable<ReturnType<typeof useLibrary>['lastReply']>['reply'];
}) {
  if (reply.state === 'ran') {
    const text = typeof reply.result.text === 'string' ? reply.result.text : null;
    return (
      <div style={L.verdict('ran')}>
        <span style={L.verdictTag}>{name} · ran</span>
        {text !== null && <span style={L.generated}>{text}</span>}
      </div>
    );
  }
  if (reply.state === 'not_checked') {
    return (
      <div style={L.verdict('not_checked')}>
        <span style={L.verdictTag}>{name} · not checked</span>
        <span>{reply.reason}</span>
      </div>
    );
  }
  return (
    <div style={L.verdict('failed')}>
      <span style={L.verdictTag}>{name} · failed</span>
      <pre style={L.log}>{reply.traceback}</pre>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div style={A.fact}>
      <span style={A.factKey}>{k}</span>
      <span style={A.factValue}>{v}</span>
    </div>
  );
}

// ── the budget ───────────────────────────────────────────────────────────────

function Budget() {
  const lib = useLibrary().library;
  if (!lib) return null;
  const share = lib.budgetBytes ? Math.min(1, lib.usedBytes / lib.budgetBytes) : 0;
  return (
    <section style={L.budget} aria-label="Library disk budget">
      <span style={S.heading}>Disk</span>
      <div style={L.big}>{fmtBytes(lib.usedBytes)}</div>
      <div style={S.mono}>
        of {lib.budgetBytes ? fmtBytes(lib.budgetBytes) : 'no limit'} · interp.libraryBudgetGb
      </div>
      <div style={L.track}>
        <span style={{ ...L.fill, width: `${Math.round(share * 100)}%` }} />
      </div>
      <div style={S.mono}>
        {lib.targets.length
          ? `checked against ${lib.targets.map((t) => t.label).join(' and ')}`
          : 'no model to check against yet'}
      </div>
    </section>
  );
}

// ── installed ────────────────────────────────────────────────────────────────

const COMPAT_COLOR: Record<string, string> = {
  compatible: 'var(--success)',
  incompatible: 'var(--danger)',
  unknown: 'var(--text-dim)',
};

function CompatBadge({ c }: { c: Compat }) {
  return (
    <span style={L.badge(COMPAT_COLOR[c.state])} title={c.reasons.join('\n') || c.state}>
      {c.state} · {c.source === 'gguf' ? 'agent GGUF' : 'worker'}
    </span>
  );
}

function Installed() {
  const lib = useLibrary().library;
  if (!lib) return null;
  return (
    <section style={{ ...A.ribbonWrap, paddingTop: 10 }} aria-label="Installed artifacts">
      <div style={A.panelHead}>
        <span style={S.heading}>Installed</span>
        <span style={S.mono}>{lib.artifacts.length}</span>
      </div>
      {lib.artifacts.length === 0 ? (
        <p style={A.emptyText}>
          Nothing yet. Pick an SAE, transcoder set or lens below — its size is shown before anything
          is written.
        </p>
      ) : (
        <ol style={L.list}>
          {lib.artifacts.map((a, i) => (
            <ArtifactRow key={a.id} a={a} index={i} />
          ))}
        </ol>
      )}
    </section>
  );
}

function ArtifactRow({ a, index }: { a: Artifact; index: number }) {
  const f = a.facts;
  const facts = [
    f.layer !== undefined ? `L${f.layer}` : null,
    f.dIn ? `d_in ${f.dIn}` : null,
    f.width ? `width ${f.width.toLocaleString()}` : null,
  ].filter(Boolean);
  return (
    <li style={{ ...L.artifact, ...S.stagger(index) }}>
      <div style={L.artifactHead}>
        <span style={L.kind}>{a.kind}</span>
        <span style={L.artifactName}>{a.choice || a.repo}</span>
        <span style={S.mono}>{fmtBytes(a.bytes)}</span>
      </div>
      <div style={L.artifactMeta}>
        <span style={S.mono}>
          {a.repo} · trained on {a.modelRepo ?? 'unstated'}
          {facts.length ? ` · ${facts.join(' · ')}` : ''}
        </span>
        {Object.entries(f.source).length > 0 && (
          <span style={S.mono} title="Where each fact was read">
            (
            {Object.entries(f.source)
              .map(([k, src]) => `${k}: ${src}`)
              .join(', ')}
            )
          </span>
        )}
      </div>
      <div style={L.row}>
        {a.compat.length ? (
          a.compat.map((c) => <CompatBadge key={c.source} c={c} />)
        ) : (
          <span style={L.badge(COMPAT_COLOR.unknown)}>unknown · no model loaded</span>
        )}
        <span style={L.spacer} />
        <button
          type="button"
          className="btn-mini"
          onClick={() => askInterp(`Library artifact ${a.id}`)}
        >
          Ask
        </button>
        <button type="button" className="btn-mini" onClick={() => void removeArtifact(a.id)}>
          Delete
        </button>
      </div>
      {a.kind === 'jlens' && <LensInstall artifactId={a.id} />}
    </li>
  );
}

// ── the catalog ──────────────────────────────────────────────────────────────

function Catalog() {
  const st = useLibrary();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section style={A.ribbonWrap} aria-label="Get artifacts">
      <div style={{ ...A.panelHead, paddingTop: 4 }}>
        <span style={S.heading}>Get</span>
        <span style={S.mono}>sizes read from the Hugging Face Hub</span>
      </div>
      <DownloadBar />
      <ol style={L.list}>
        {st.catalog.map((e, i) => (
          <li key={e.id} style={S.stagger(i)}>
            <button
              type="button"
              className="interp-col"
              style={L.entryRow(open === e.id)}
              aria-expanded={open === e.id}
              onClick={() => {
                const next = open === e.id ? null : e.id;
                setOpen(next);
                if (next && !st.choices[next]) void browse(next);
              }}
            >
              <span style={L.kind}>{e.kind}</span>
              <span style={L.entryTitle}>{e.title}</span>
              <span style={S.mono}>{e.repo}</span>
            </button>
            {open === e.id && <Choices entry={e} />}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Choices({ entry }: { entry: CatalogEntry }) {
  const st = useLibrary();
  const list = st.choices[entry.id];
  const [filter, setFilter] = useState('');
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (list?.choices ?? []).filter((c) => !q || c.id.toLowerCase().includes(q));
  }, [list, filter]);

  return (
    <div style={L.choices}>
      {entry.note && <p style={A.gloss}>{entry.note}</p>}
      {!list || list.loading ? (
        <p style={A.emptyText}>Listing {entry.repo}…</p>
      ) : list.error ? (
        <div style={A.conflict}>{list.error}</div>
      ) : (
        <>
          {list.choices.length > 8 && (
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="filter — e.g. layer_13 or 16k"
              aria-label="Filter choices"
              style={{ maxWidth: 320 }}
            />
          )}
          <div style={L.choiceList}>
            {shown.map((c) => (
              <div key={c.id || 'repo'} style={L.choice}>
                <span style={L.choiceName}>{c.id || `${entry.repo} (whole repo)`}</span>
                <span style={S.mono}>
                  {c.files.length} file{c.files.length === 1 ? '' : 's'} · {fmtBytes(c.bytes)}
                </span>
                <span style={S.mono}>{c.forModel}</span>
                {c.installed ? (
                  <span style={L.badge(COMPAT_COLOR.compatible)}>installed</span>
                ) : (
                  <button
                    type="button"
                    className="btn-mini"
                    onClick={() => askDownload(entry.id, c)}
                  >
                    Get
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/** The confirmation, then the progress, of the one download. */
function DownloadBar() {
  const st = useLibrary();
  const lib = st.library;
  if (st.pending) {
    const p = st.pending;
    const after = (lib?.usedBytes ?? 0) + p.bytes;
    const over = !!lib?.budgetBytes && after > lib.budgetBytes;
    return (
      <div style={L.confirm} role="alertdialog" aria-label="Confirm download">
        <div>
          <div style={S.heading}>Download {fmtBytes(p.bytes)}?</div>
          <div style={S.mono}>
            {p.choice || 'whole repo'} · {p.files} file{p.files === 1 ? '' : 's'} · library would be{' '}
            {fmtBytes(after)}
            {lib?.budgetBytes ? ` of ${fmtBytes(lib.budgetBytes)}` : ''}
          </div>
          {over && <div style={{ ...S.mono, color: 'var(--danger)' }}>Over the budget.</div>}
        </div>
        <span style={L.spacer} />
        <button type="button" disabled={over} onClick={() => void confirmDownload()}>
          Download
        </button>
        <button type="button" onClick={cancelDownload}>
          Cancel
        </button>
      </div>
    );
  }
  const d = st.download;
  if (!d) return null;
  const share = d.total ? d.completed / d.total : 0;
  return (
    <div style={L.confirm}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={S.heading}>
          {d.error ? 'Download failed' : d.done ? 'Downloaded' : 'Downloading'}{' '}
          <span style={S.mono}>{d.choice || d.entryId}</span>
        </div>
        {d.error ? (
          <div style={{ ...S.mono, color: 'var(--danger)' }}>{d.error}</div>
        ) : (
          <>
            <div style={L.track}>
              <span style={{ ...L.fill, width: `${Math.round(share * 100)}%` }} />
            </div>
            <div style={S.mono}>
              {fmtBytes(d.completed)} / {fmtBytes(d.total)}
              {d.file && !d.done ? ` · ${d.file.split('/').pop()}` : ''}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Neuronpedia ──────────────────────────────────────────────────────────────

function NeuronpediaLookup() {
  const [model, setModel] = useState('gemma-2-2b');
  const [source, setSource] = useState('20-gemmascope-res-16k');
  const [index, setIndex] = useState('1000');
  const [card, setCard] = useState<FeatureCard | null>(null);
  const [busy, setBusy] = useState(false);
  const lookUp = async () => {
    setBusy(true);
    try {
      setCard(await getFeature(model.trim(), source.trim(), index.trim()));
    } catch (err) {
      setCard({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section style={A.ribbonWrap} aria-label="Neuronpedia feature lookup">
      <div style={{ ...A.panelHead, paddingTop: 4 }}>
        <span style={S.heading}>Neuronpedia</span>
        <span style={S.mono}>
          a public GET by feature id · nothing of yours is sent · cached a week
        </span>
      </div>
      <div style={L.row}>
        <input
          type="text"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          aria-label="Neuronpedia model id"
          style={{ width: 150 }}
        />
        <input
          type="text"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          aria-label="Neuronpedia source (SAE set and layer)"
          style={L.grow}
        />
        <input
          type="text"
          value={index}
          onChange={(e) => setIndex(e.target.value)}
          aria-label="Feature index"
          style={{ width: 90 }}
        />
        <button type="button" disabled={busy} onClick={() => void lookUp()}>
          {busy ? 'Looking up…' : 'Look up'}
        </button>
      </div>
      {card && <NeuronpediaCard card={card} />}
    </section>
  );
}

// ── styles local to this section ─────────────────────────────────────────────

const panel: CSSProperties = {
  background: 'var(--bg-secondary)',
  borderTop: '2px solid var(--accent)',
  clipPath: 'polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)',
  padding: '10px 12px',
  minWidth: 0,
  display: 'grid',
  gap: 8,
  alignContent: 'start',
};

const STATE_COLOR: Record<string, string> = {
  ready: 'var(--success)',
  starting: 'var(--warn)',
  crashed: 'var(--danger)',
  stopped: 'var(--text-dim)',
  no_env: 'var(--text-dim)',
};

const L = {
  // Asymmetric: the worker is the wide column, the budget a narrow one.
  top: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 2.4fr) minmax(180px, 1fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  workerPanel: panel,
  budget: { ...panel, borderTopColor: 'var(--border)' } satisfies CSSProperties,
  big: { fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700 } satisfies CSSProperties,
  stateTag(state: string): CSSProperties {
    return {
      ...S.mono,
      textTransform: 'uppercase',
      letterSpacing: '0.12em',
      fontSize: 10,
      color: STATE_COLOR[state] ?? 'var(--text-secondary)',
      borderLeft: `2px solid ${STATE_COLOR[state] ?? 'var(--border)'}`,
      paddingLeft: 6,
    };
  },
  groups: { display: 'grid', gap: 4 } satisfies CSSProperties,
  group(have: boolean): CSSProperties {
    return {
      display: 'grid',
      gridTemplateColumns: '72px minmax(0, 1fr) auto',
      alignItems: 'center',
      gap: 10,
      minHeight: 30,
      borderLeft: `2px solid ${have ? 'var(--success)' : 'var(--border)'}`,
      paddingLeft: 8,
    };
  },
  groupName: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
  } satisfies CSSProperties,
  groupGloss: { color: 'var(--text-secondary)', fontSize: 12 } satisfies CSSProperties,
  logLine: {
    ...S.mono,
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  } satisfies CSSProperties,
  log: {
    ...S.mono,
    margin: 0,
    maxHeight: 180,
    overflow: 'auto',
    whiteSpace: 'pre-wrap',
    background: 'var(--bg-primary)',
    padding: 8,
  } satisfies CSSProperties,
  row: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' } satisfies CSSProperties,
  grow: { flex: 1, minWidth: 160 } satisfies CSSProperties,
  spacer: { flex: 1 } satisfies CSSProperties,
  facts: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
    gap: 4,
  } satisfies CSSProperties,
  verdict(state: 'ran' | 'failed' | 'not_checked'): CSSProperties {
    const color =
      state === 'ran' ? 'var(--success)' : state === 'failed' ? 'var(--danger)' : 'var(--warn)';
    return {
      display: 'grid',
      gap: 4,
      borderLeft: `2px solid ${color}`,
      padding: '4px 0 4px 8px',
      fontSize: 12,
      color: 'var(--text-primary)',
    };
  },
  verdictTag: {
    ...S.heading,
    fontSize: 9.5,
  } satisfies CSSProperties,
  generated: { fontFamily: 'var(--font-mono)', fontSize: 12 } satisfies CSSProperties,
  track: {
    height: 4,
    background: 'var(--bg-primary)',
    position: 'relative',
    margin: '4px 0',
  } satisfies CSSProperties,
  fill: {
    position: 'absolute',
    inset: '0 auto 0 0',
    background: 'var(--accent)',
    transition: 'width 200ms ease',
  } satisfies CSSProperties,
  list: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 4,
  } satisfies CSSProperties,
  artifact: {
    display: 'grid',
    gap: 4,
    padding: '6px 8px',
    borderLeft: '2px solid var(--border)',
    background: 'var(--bg-primary)',
  } satisfies CSSProperties,
  artifactHead: { display: 'flex', alignItems: 'baseline', gap: 10 } satisfies CSSProperties,
  artifactName: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
    flex: 1,
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
  artifactMeta: { display: 'flex', gap: 8, flexWrap: 'wrap' } satisfies CSSProperties,
  kind: {
    ...S.heading,
    fontSize: 9.5,
    minWidth: 72,
    color: 'var(--accent)',
  } satisfies CSSProperties,
  badge(color: string): CSSProperties {
    return {
      ...S.mono,
      fontSize: 10,
      textTransform: 'uppercase',
      letterSpacing: '0.1em',
      color,
      borderLeft: `2px solid ${color}`,
      paddingLeft: 6,
    };
  },
  entryRow(open: boolean): CSSProperties {
    return {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      width: '100%',
      height: 30,
      padding: '0 8px',
      border: 'none',
      borderLeft: `2px solid ${open ? 'var(--accent)' : 'transparent'}`,
      borderRadius: 0,
      background: open ? 'color-mix(in srgb, var(--accent) 8%, transparent)' : 'transparent',
      color: 'var(--text-primary)',
      cursor: 'pointer',
      textAlign: 'left',
    };
  },
  entryTitle: { flex: 1, minWidth: 0, fontSize: 12, fontWeight: 600 } satisfies CSSProperties,
  choices: { display: 'grid', gap: 6, padding: '6px 0 10px 18px' } satisfies CSSProperties,
  choiceList: {
    display: 'grid',
    gap: 2,
    maxHeight: 280,
    overflow: 'auto',
  } satisfies CSSProperties,
  choice: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 2fr) 130px minmax(0, 1fr) 70px',
    alignItems: 'center',
    gap: 10,
    minHeight: 26,
  } satisfies CSSProperties,
  choiceName: {
    fontFamily: 'var(--font-mono)',
    fontSize: 11.5,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
  confirm: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 10px',
    borderLeft: '2px solid var(--accent)',
    background: 'var(--bg-primary)',
    margin: '6px 0',
  } satisfies CSSProperties,
};
