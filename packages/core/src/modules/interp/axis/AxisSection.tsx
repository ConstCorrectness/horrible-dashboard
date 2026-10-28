/**
 * Axis: the Assistant Axis, built on a model you run, and what it shows.
 *
 * Three panels, top to bottom. **The axis** — built here on any local GGUF from
 * role-play answers vs plain-assistant answers (`interp/axis.py`), with the check
 * that makes it worth reading: per layer, how well it separates held-out
 * assistant tokens from held-out role tokens, and where each role sits along it.
 * **Monitor** — a conversation, a captured agent turn or a trace, projected token
 * by token, against the held-out bands: is this still the assistant talking?
 * **Cap** — the same prompt generated twice, once with the projection floored at
 * the assistant's 25th percentile over a band of layers (two passes per token,
 * since a llama.cpp control vector only adds), with every floored token marked.
 */
import { useEffect, useMemo, useState, type CSSProperties } from 'react';

import { RollingCounter } from '../../../viz/RollingCounter';
import { getLlamaModels, type ModelEntry } from '../../llamacpp/api';
import { interpretabilityStore } from '../../interpretability/store';
import { askInterp } from '../ask';
import { A } from '../attention/styles';
import { refreshFeatureTraces, useFeatures } from '../features/state';
import { shownToken } from '../library/NeuronpediaCard';
import { S } from '../styles';
import { SuiteFrame } from '../SuiteFrame';
import type { AxisSummary, Band, CapRun, MonitorResult, TraceSeries } from './api';
import {
  axisStore,
  build,
  current,
  refreshAxes,
  runCap,
  runMonitor,
  selectAxis,
  setBuild,
  setCap,
  setMonitor,
  useAxis,
  type MonitorSource,
} from './state';

export function AxisBody() {
  return (
    <SuiteFrame title="Axis">
      <AxisSection />
    </SuiteFrame>
  );
}

const CHECK_COLOR: Record<string, string> = {
  separates: 'var(--success)',
  weak: 'var(--warn)',
  not_checked: 'var(--text-dim)',
};

export function AxisSection() {
  const st = useAxis();
  const [building, setBuilding] = useState(false);
  useEffect(() => {
    void refreshAxes();
  }, []);
  const ax = current(st);
  const showBuild = building || !st.axes.length || Boolean(st.build.running);
  return (
    <div style={A.root}>
      <section style={{ ...A.ribbonWrap, paddingTop: 10 }} aria-label="Axis">
        <div style={T.row}>
          <label style={T.field}>
            <span style={S.heading}>Axis</span>
            <select
              value={st.id}
              onChange={(e) => selectAxis(e.target.value)}
              aria-label="Axis to read"
              style={{ minWidth: 260 }}
            >
              {!st.axes.length && <option value="">none built yet</option>}
              {st.axes.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.modelName} · L{a.defaultLayer} · {a.roles.length} roles
                </option>
              ))}
            </select>
          </label>
          {ax && <CheckChip ax={ax} />}
          <span style={{ flex: 1 }} />
          <button type="button" onClick={() => setBuilding((b) => !b)}>
            {showBuild && st.axes.length ? 'Hide build' : 'New axis'}
          </button>
        </div>
        {showBuild && <BuildPanel />}
      </section>
      {st.error && <div style={A.conflict}>{st.error}</div>}
      {ax ? (
        <>
          <AxisCard ax={ax} />
          <MonitorPanel ax={ax} />
          <CapPanel ax={ax} />
        </>
      ) : (
        !st.build.running && (
          <p style={A.emptyText}>
            An axis is built on the model you will read with it. The model answers a few questions
            as itself and in each of the roles, greedily; the axis is the difference between the
            assistant&apos;s answers and the roles&apos;, and it is checked on questions left out of
            the fit. A 1B model takes about five minutes on the CPU.
          </p>
        )
      )}
    </div>
  );
}

function CheckChip({ ax }: { ax: AxisSummary }) {
  const color = CHECK_COLOR[ax.check.state] ?? 'var(--text-dim)';
  return (
    <div style={T.chip(color)} title={ax.check.reason}>
      <span style={{ ...S.heading, color }}>{ax.check.state.replace('_', ' ')}</span>
      <span style={S.mono}>
        token AUC <RollingCounter value={ax.check.tokenAuc ?? 0} format={(v) => v.toFixed(2)} /> ·
        answer AUC {ax.check.auc?.toFixed(2) ?? '—'}
      </span>
    </div>
  );
}

// ── building ─────────────────────────────────────────────────────────────────

function BuildPanel() {
  const st = useAxis();
  const b = st.build;
  const [models, setModels] = useState<ModelEntry[]>([]);
  useEffect(() => {
    getLlamaModels()
      .then((r) => {
        const usable = r.models.filter((m) => !m.error && !/mmproj/i.test(m.name));
        setModels(usable);
        if (!axisStore.get().build.modelPath && usable[0]) setBuild({ modelPath: usable[0].path });
      })
      .catch(() => setModels([]));
  }, []);
  const roles = st.defaults?.roles.length ?? 30;
  const roleCount = b.roleCount || roles;
  const questions = st.defaults?.questions.length ?? 8;
  const steps = (roleCount + (st.defaults?.assistantPrompts.length ?? 3)) * questions * b.maxTokens;
  const run = b.running;
  return (
    <div style={T.build}>
      <div style={T.row}>
        <label style={{ ...T.field, flex: 1, minWidth: 240 }}>
          <span style={S.heading}>Model</span>
          <select
            value={b.modelPath}
            onChange={(e) => setBuild({ modelPath: e.target.value })}
            aria-label="GGUF to build the axis on"
          >
            {models.map((m) => (
              <option key={m.path} value={m.path}>
                {m.name} · {m.quantization || m.architecture}
              </option>
            ))}
          </select>
        </label>
        <label style={T.field}>
          <span style={S.heading}>Roles</span>
          <input
            type="number"
            min={3}
            max={300}
            value={roleCount}
            onChange={(e) => setBuild({ roleCount: Math.max(3, Number(e.target.value) || 3) })}
            aria-label="Roles to use"
            style={{ width: 72 }}
          />
        </label>
        <label style={T.field}>
          <span style={S.heading}>Answer tokens</span>
          <input
            type="number"
            min={4}
            max={128}
            value={b.maxTokens}
            onChange={(e) => setBuild({ maxTokens: Math.max(4, Number(e.target.value) || 4) })}
            aria-label="Tokens per answer"
            style={{ width: 72 }}
          />
        </label>
        <button type="button" disabled={!b.modelPath || !!run} onClick={() => void build()}>
          {run ? 'Building…' : 'Build axis'}
        </button>
      </div>
      <textarea
        value={b.rolesText}
        onChange={(e) => setBuild({ rolesText: e.target.value })}
        placeholder={`Optional: your own roles, one per line ("name: description"). Empty uses the built-in ${roles} — from a librarian to a sentient storm.`}
        aria-label="Custom roles"
        rows={2}
        style={T.textarea}
      />
      <div style={S.mono}>
        {run
          ? `${run.condition} · ${run.done}/${run.total} answers · ${run.elapsed.toFixed(0)} s`
          : `${questions} questions (last ${st.defaults?.heldOut ?? 3} held out) × ${roleCount} roles + the plain assistant ≈ ${steps.toLocaleString()} generated tokens`}
      </div>
      {run && run.total > 0 && (
        <div style={T.progress}>
          <div style={{ ...T.progressFill, width: `${(100 * run.done) / run.total}%` }} />
        </div>
      )}
    </div>
  );
}

// ── the axis itself ──────────────────────────────────────────────────────────

function AxisCard({ ax }: { ax: AxisSummary }) {
  const layer = ax.layers[ax.defaultLayer];
  const band = new Set(ax.cap.layers);
  const W = 520;
  const H = 96;
  const bw = W / ax.layers.length;
  return (
    <section style={T.cardGrid} aria-label="The axis">
      <div style={A.ribbonWrap}>
        <div style={A.panelHead}>
          <span style={S.heading}>Separation by layer</span>
          <span style={S.mono}>held-out tokens · bar = AUC, tick = |cos| with role PC1</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H + 14}`} style={{ width: '100%', display: 'block' }} role="img">
          <title>Held-out token AUC per layer</title>
          <line
            x1={0}
            x2={W}
            y1={H * 0.5}
            y2={H * 0.5}
            stroke="var(--border)"
            strokeDasharray="3 3"
          />
          {ax.layers.map((l, i) => {
            const v = l.tokenAuc ?? l.auc ?? 0;
            const h = Math.max(1, v * H);
            const on = i === ax.defaultLayer;
            return (
              <g key={i}>
                <rect
                  x={i * bw + 1}
                  y={H - h}
                  width={bw - 2}
                  height={h}
                  fill={on || band.has(i) ? 'var(--accent)' : 'var(--text-dim)'}
                  opacity={on ? 1 : band.has(i) ? 0.6 : 0.35}
                >
                  <title>
                    L{i}: token AUC {l.tokenAuc ?? '—'}, d′ {l.dPrime ?? '—'}, answer AUC{' '}
                    {l.auc ?? '—'}, PC1 cos {l.pc1Cos ?? '—'}
                  </title>
                </rect>
                {l.pc1Cos != null && (
                  <line
                    x1={i * bw + 2}
                    x2={(i + 1) * bw - 2}
                    y1={H - l.pc1Cos * H}
                    y2={H - l.pc1Cos * H}
                    stroke="var(--text)"
                    strokeWidth={1.5}
                  />
                )}
                {(i % 4 === 0 || on) && (
                  <text x={i * bw + bw / 2} y={H + 11} textAnchor="middle" style={T.svgText}>
                    {i}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        <div style={S.mono}>
          L{ax.defaultLayer} is the default (best token separation) · cap band L{ax.cap.layers[0]}–L
          {ax.cap.layers[ax.cap.layers.length - 1]} · d′ {layer?.dPrime ?? '—'} ·{' '}
          {ax.samples.assistant} assistant / {ax.samples.role} role answers,{' '}
          {ax.samples.heldOutTokens} held-out tokens
        </div>
      </div>
      <RoleMap ax={ax} />
    </section>
  );
}

/** Each role's mean answer on the axis at the default layer, with the assistant's. */
function RoleMap({ ax }: { ax: AxisSummary }) {
  const L = ax.defaultLayer;
  const rows = useMemo(
    () =>
      [
        { name: 'assistant', value: ax.assistantProjection[L], assistant: true },
        ...Object.entries(ax.roleProjections).map(([name, v]) => ({
          name,
          value: v[L],
          assistant: false,
        })),
      ].sort((a, b) => b.value - a.value),
    [ax, L],
  );
  const lo = Math.min(...rows.map((r) => r.value));
  const hi = Math.max(...rows.map((r) => r.value));
  const span = hi - lo || 1;
  return (
    <div style={A.ribbonWrap}>
      <div style={A.panelHead}>
        <span style={S.heading}>Roles along the axis</span>
        <span style={S.mono}>L{L} · mean answer · hover for its first answer</span>
      </div>
      <ol style={T.roles}>
        {rows.map((r, i) => (
          <li
            key={r.name}
            style={{ ...T.roleRow, ...S.stagger(i) }}
            title={ax.examples[r.name] ?? ''}
          >
            <span style={{ ...T.roleName, color: r.assistant ? 'var(--accent)' : undefined }}>
              {r.name}
            </span>
            <span style={T.roleTrack}>
              <span
                style={{
                  ...T.roleDot,
                  left: `${((r.value - lo) / span) * 100}%`,
                  background: r.assistant ? 'var(--accent)' : 'var(--text-secondary)',
                }}
              />
            </span>
            <span style={S.mono}>{r.value.toFixed(0)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// ── monitor ──────────────────────────────────────────────────────────────────

const SOURCES: { id: MonitorSource; label: string }[] = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'turn', label: 'Agent turn' },
  { id: 'trace', label: 'Trace' },
];

function MonitorPanel({ ax }: { ax: AxisSummary }) {
  const st = useAxis();
  const m = st.monitor;
  const features = useFeatures();
  const turns = useMemo(() => interpretabilityStore.getSnapshot().slice(-20).reverse(), []);
  useEffect(() => {
    if (m.source === 'trace' && !features.traces.length) void refreshFeatureTraces();
  }, [m.source, features.traces.length]);
  const traces = features.traces.filter((t) => !t.modelName || t.modelName === ax.modelName);
  return (
    <section style={A.ribbonWrap} aria-label="Monitor">
      <div style={A.panelHead}>
        <span style={S.heading}>Monitor</span>
        <span style={S.mono}>each token&apos;s projection at L{ax.defaultLayer}</span>
      </div>
      <div style={T.row}>
        <div role="tablist" style={T.tabs}>
          {SOURCES.map((s) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              className="btn-mini"
              aria-selected={m.source === s.id}
              style={T.tab(m.source === s.id)}
              onClick={() => setMonitor({ source: s.id })}
            >
              {s.label}
            </button>
          ))}
        </div>
        {m.source === 'turn' && (
          <select
            value={m.turnId}
            onChange={(e) => setMonitor({ turnId: e.target.value })}
            aria-label="Captured agent turn"
            style={{ flex: 1, minWidth: 220 }}
          >
            <option value="">
              {turns.length ? 'pick a captured turn' : 'no captured turns yet'}
            </option>
            {turns.map((t) => (
              <option key={t.turnId} value={t.turnId}>
                {t.agentName || t.agentId} · {t.model} · {t.rounds.length} rounds ·{' '}
                {new Date(t.startedAt * 1000).toLocaleTimeString()}
              </option>
            ))}
          </select>
        )}
        {m.source === 'trace' && (
          <select
            value={m.traceId}
            onChange={(e) => setMonitor({ traceId: e.target.value })}
            aria-label="Trace of this model"
            style={{ flex: 1, minWidth: 220 }}
          >
            <option value="">
              {traces.length ? 'pick a trace' : `no traces of ${ax.modelName}`}
            </option>
            {traces.map((t) => (
              <option key={t.traceId} value={t.traceId}>
                {t.traceId} · {t.prompt.slice(0, 60)}
              </option>
            ))}
          </select>
        )}
        <button type="button" disabled={!!m.running} onClick={() => void runMonitor()}>
          {m.running ? 'Reading…' : 'Read'}
        </button>
      </div>
      {m.source === 'conversation' && (
        <textarea
          value={m.conversation}
          onChange={(e) => setMonitor({ conversation: e.target.value })}
          placeholder={'system: …\nuser: …\nassistant: …\nuser: …'}
          aria-label="Conversation to read"
          rows={5}
          style={T.textarea}
        />
      )}
      {m.source === 'trace' && (
        <div style={S.mono}>
          A trace is raw text with no chat template — not the assistant turn the bands were measured
          on, so its absolute level means less than its shape.
        </div>
      )}
      {m.running && <div style={S.mono}>{m.running}</div>}
      {m.result && <Reading r={m.result} />}
    </section>
  );
}

function Reading({ r }: { r: TraceSeries | MonitorResult }) {
  const boundaries = 'messages' in r ? r.messages : [];
  const sum = 'assistantSummary' in r && r.assistantSummary ? r.assistantSummary : r.summary;
  const own = 'assistantSummary' in r && r.assistantSummary;
  const drift = 'drift' in r ? r.drift : null;
  const medians = new Map((drift?.perMessage ?? []).map((p) => [p.start, p.median]));
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={T.stats}>
        <Stat label={own ? 'Assistant mean' : 'Mean'} value={sum.mean} />
        <Stat label="Below floor" value={(sum.belowFloor ?? 0) * 100} unit="%" />
        {drift?.changeInGaps !== undefined ? (
          <Stat label="Replies moved" value={drift.changeInGaps * 100} unit="% of gap" signed />
        ) : (
          <Stat label="In role range" value={(sum.inRoleRange ?? 0) * 100} unit="%" />
        )}
        <Stat label="Trend / 100 tok" value={sum.slopePer100} signed />
      </div>
      <DriftChart
        values={r.values}
        tokens={r.tokens}
        bands={r.bands}
        boundaries={boundaries.map((b) => ({
          ...b,
          role: medians.has(b.start) ? `${b.role} ${medians.get(b.start)!.toFixed(0)}` : b.role,
        }))}
        generatedFrom={'generatedFrom' in r ? r.generatedFrom : undefined}
      />
      {drift && (
        <div style={S.mono}>
          The bands come from the model&apos;s own answers; text it did not write reads lower.
          Within one conversation that offset is shared, so compare messages with each other: each
          label is its median, and the replies moved {drift.change ?? '—'} (gap {drift.gap ?? '—'}).
        </div>
      )}
      <div style={A.actions}>
        <button
          type="button"
          onClick={() =>
            askInterp(
              `Axis section: a ${'traceId' in r ? `trace (${r.traceId})` : (r as MonitorResult).source.kind} read along the assistant axis at L${r.layer}. Does it drift out of the assistant persona, and where?`,
            )
          }
        >
          Ask about this drift
        </button>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  unit = '',
  signed,
}: {
  label: string;
  value: number | undefined;
  unit?: string;
  signed?: boolean;
}) {
  return (
    <div style={T.stat}>
      <span style={S.heading}>{label}</span>
      <span style={T.statValue}>
        {value === undefined ? (
          '—'
        ) : (
          <RollingCounter
            value={value}
            format={(v) => `${signed && v > 0 ? '+' : ''}${v.toFixed(0)}${unit}`}
          />
        )}
      </span>
    </div>
  );
}

/**
 * A projection series as a line, over the held-out bands: the assistant's
 * interquartile range (accent) and the roles' (danger), the floor dashed.
 */
export function DriftChart({
  values,
  tokens,
  bands,
  boundaries = [],
  generatedFrom,
  second,
  marks,
  height = 150,
}: {
  values: (number | null)[];
  tokens: string[];
  bands: { assistant?: Band; role?: Band };
  boundaries?: { role: string; start: number; end: number }[];
  generatedFrom?: number;
  /** A second series drawn behind (the default run under a capped one). */
  second?: (number | null)[];
  /** Positions to tick (capped tokens). */
  marks?: number[];
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const H = height;
  // Skip the first position: BOS's residual is many times the norm and flattens the rest.
  const shown = [...values.slice(1), ...(second ?? []).slice(1)].filter(
    (v): v is number => v !== null,
  );
  const a = bands.assistant;
  const ro = bands.role;
  const lo = Math.min(...shown, ro?.p25 ?? Infinity, a?.p25 ?? Infinity);
  const hi = Math.max(...shown, a?.p75 ?? -Infinity);
  const pad = (hi - lo) * 0.08 || 1;
  const y = (v: number) => H - ((v - (lo - pad)) / (hi - lo + 2 * pad)) * H;
  const n = Math.max(values.length, second?.length ?? 0);
  const x = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * W);
  const path = (vs: (number | null)[]) =>
    vs
      .map((v, i) => (i === 0 || v === null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
      .filter(Boolean)
      .join(' ');
  if (!shown.length) return <div style={S.mono}>no values to draw</div>;
  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', display: 'block', background: 'var(--bg)' }}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          setHover(Math.round(((e.clientX - box.left) / box.width) * (n - 1)));
        }}
        onMouseLeave={() => setHover(null)}
        role="img"
      >
        <title>Projection on the assistant axis, token by token</title>
        {ro && (
          <rect
            x={0}
            width={W}
            y={y(ro.p75)}
            height={Math.max(0, y(ro.p25) - y(ro.p75))}
            fill="var(--danger)"
            opacity={0.1}
          />
        )}
        {a && (
          <rect
            x={0}
            width={W}
            y={y(a.p75)}
            height={Math.max(0, y(a.p25) - y(a.p75))}
            fill="var(--accent)"
            opacity={0.12}
          />
        )}
        {a && (
          <line
            x1={0}
            x2={W}
            y1={y(a.p25)}
            y2={y(a.p25)}
            stroke="var(--accent)"
            strokeDasharray="4 3"
          />
        )}
        {boundaries.map((b, i) => (
          <g key={i}>
            <line x1={x(b.start)} x2={x(b.start)} y1={0} y2={H} stroke="var(--border)" />
            <text x={x(b.start) + 3} y={10} style={T.svgText}>
              {b.role}
            </text>
          </g>
        ))}
        {generatedFrom !== undefined && generatedFrom > 0 && (
          <line
            x1={x(generatedFrom)}
            x2={x(generatedFrom)}
            y1={0}
            y2={H}
            stroke="var(--accent)"
            strokeDasharray="2 2"
          />
        )}
        {(marks ?? []).map((i) => (
          <line key={i} x1={x(i)} x2={x(i)} y1={H - 4} y2={H} stroke="var(--accent)" />
        ))}
        {second && (
          <polyline points={path(second)} fill="none" stroke="var(--text-dim)" strokeWidth={1.2} />
        )}
        <polyline points={path(values)} fill="none" stroke="var(--text)" strokeWidth={1.4} />
        {hover !== null && hover > 0 && hover < n && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={0}
            y2={H}
            stroke="var(--text-secondary)"
            opacity={0.5}
          />
        )}
      </svg>
      <div style={S.mono}>
        {hover !== null && hover > 0 && hover < n
          ? `#${hover} ${JSON.stringify(shownToken(tokens[hover] ?? ''))} → ${values[hover] ?? '—'}${second ? ` (default ${second[hover] ?? '—'})` : ''}`
          : `band: assistant p25–p75 ${a ? `${a.p25.toFixed(0)}–${a.p75.toFixed(0)}` : '—'} · roles ${ro ? `${ro.p25.toFixed(0)}–${ro.p75.toFixed(0)}` : '—'} · dashed = floor`}
      </div>
    </div>
  );
}

// ── cap ──────────────────────────────────────────────────────────────────────

function CapPanel({ ax }: { ax: AxisSummary }) {
  const st = useAxis();
  const c = st.cap;
  const r = c.result;
  return (
    <section style={A.ribbonWrap} aria-label="Cap">
      <div style={A.panelHead}>
        <span style={S.heading}>Cap</span>
        <span style={S.mono}>
          floor at the assistant&apos;s p{ax.cap.percentile} over L{ax.cap.layers.join(', L')} ·
          answer tokens only
        </span>
      </div>
      <input
        type="text"
        value={c.system}
        onChange={(e) => setCap({ system: e.target.value })}
        placeholder="System prompt (a role to pull the model into)"
        aria-label="System prompt"
      />
      <div style={T.row}>
        <input
          type="text"
          value={c.prompt}
          onChange={(e) => setCap({ prompt: e.target.value })}
          placeholder="User message"
          aria-label="User message"
          style={{ flex: 1, minWidth: 200 }}
        />
        <input
          type="number"
          min={1}
          max={256}
          value={c.maxTokens}
          onChange={(e) => setCap({ maxTokens: Math.max(1, Number(e.target.value) || 1) })}
          aria-label="Tokens to generate"
          style={{ width: 72 }}
        />
        <button
          type="button"
          disabled={!!c.running || !c.prompt.trim()}
          onClick={() => void runCap()}
        >
          {c.running ? 'Running…' : 'Generate both'}
        </button>
      </div>
      {c.running && <div style={S.mono}>{c.running}</div>}
      {r && (
        <>
          <div style={T.columns}>
            <CapColumn title="Default" run={r.default} />
            <CapColumn title="Capped" run={r.capped} accent />
          </div>
          <DriftChart
            values={r.capped.tokens.map((t) => t.projection[String(ax.defaultLayer)] ?? null)}
            second={r.default.tokens.map((t) => t.projection[String(ax.defaultLayer)] ?? null)}
            tokens={r.capped.tokens.map((t) => t.token)}
            bands={{
              assistant: ax.layers[ax.defaultLayer].assistant,
              role: ax.layers[ax.defaultLayer].role,
            }}
            generatedFrom={r.capped.promptTokens}
            marks={r.capped.tokens.flatMap((t, i) => (t.pushed ? [i] : []))}
            height={110}
          />
          <div style={S.mono}>
            L{ax.defaultLayer}: capped run (bright) over default (dim) · ticks mark floored tokens ·
            each capped token is decoded again and its projection read back from that pass
          </div>
        </>
      )}
    </section>
  );
}

function CapColumn({ title, run, accent }: { title: string; run: CapRun; accent?: boolean }) {
  const answer = run.tokens.filter((t) => t.generated);
  return (
    <section
      style={{ ...A.card, borderTop: `2px solid ${accent ? 'var(--accent)' : 'var(--border)'}` }}
      aria-label={title}
    >
      <div style={A.panelHead}>
        <span style={S.heading}>{title}</span>
        {accent && (
          <span style={S.mono}>
            {run.cappedTokens}/{answer.length} tokens floored
          </span>
        )}
      </div>
      <p style={T.text}>
        {answer.map((t, i) => (
          <span
            key={i}
            style={t.pushed ? T.pushed : undefined}
            title={
              t.pushed
                ? Object.entries(t.pushed)
                    .map(([l, v]) => `L${l} +${v.toFixed(0)}`)
                    .join(' · ')
                : undefined
            }
          >
            {shownToken(t.token)}
          </span>
        ))}
      </p>
    </section>
  );
}

// ── styles local to this section ─────────────────────────────────────────────

const T = {
  row: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: 12,
    flexWrap: 'wrap',
  } satisfies CSSProperties,
  field: { display: 'grid', gap: 4 } satisfies CSSProperties,
  chip(color: string): CSSProperties {
    return {
      ...A.card,
      display: 'grid',
      gap: 2,
      borderTop: `2px solid ${color}`,
      padding: '6px 10px',
    };
  },
  build: { display: 'grid', gap: 8, marginTop: 10 } satisfies CSSProperties,
  textarea: {
    width: '100%',
    boxSizing: 'border-box',
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    resize: 'vertical',
  } satisfies CSSProperties,
  progress: { height: 3, background: 'var(--bg)', overflow: 'hidden' } satisfies CSSProperties,
  progressFill: {
    height: '100%',
    background: 'var(--accent)',
    transition: 'width 300ms ease',
  } satisfies CSSProperties,
  cardGrid: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1.5fr) minmax(240px, 1fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  svgText: {
    fontFamily: 'var(--font-mono)',
    fontSize: 9,
    fill: 'var(--text-secondary)',
  } satisfies CSSProperties,
  roles: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'grid',
    gap: 2,
    maxHeight: 240,
    overflowY: 'auto',
  } satisfies CSSProperties,
  roleRow: {
    display: 'grid',
    gridTemplateColumns: '120px minmax(0, 1fr) 56px',
    gap: 8,
    alignItems: 'center',
  } satisfies CSSProperties,
  roleName: {
    fontFamily: 'var(--font-mono)',
    fontSize: 11.5,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
  roleTrack: {
    position: 'relative',
    height: 8,
    borderBottom: '1px solid var(--border)',
  } satisfies CSSProperties,
  roleDot: {
    position: 'absolute',
    top: 1,
    width: 7,
    height: 7,
    marginLeft: -3.5,
    clipPath: 'polygon(50% 0, 100% 50%, 50% 100%, 0 50%)',
  } satisfies CSSProperties,
  tabs: { display: 'flex', gap: 2 } satisfies CSSProperties,
  tab(on: boolean): CSSProperties {
    return {
      ...S.heading,
      fontSize: 10,
      border: 'none',
      borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}`,
      background: 'transparent',
      color: on ? 'var(--text)' : 'var(--text-secondary)',
      cursor: 'pointer',
      padding: '0 8px',
      height: 26,
    };
  },
  stats: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
    gap: 10,
  } satisfies CSSProperties,
  stat: { ...A.card, display: 'grid', gap: 2 } satisfies CSSProperties,
  statValue: {
    fontFamily: 'var(--font-mono)',
    fontSize: 18,
    fontWeight: 700,
  } satisfies CSSProperties,
  columns: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
    gap: 14,
    alignItems: 'start',
  } satisfies CSSProperties,
  text: {
    margin: 0,
    fontFamily: 'var(--font-mono)',
    fontSize: 12.5,
    lineHeight: 1.6,
    whiteSpace: 'pre-wrap',
    minHeight: 60,
  } satisfies CSSProperties,
  pushed: {
    background: 'color-mix(in srgb, var(--accent) 16%, transparent)',
  } satisfies CSSProperties,
};
