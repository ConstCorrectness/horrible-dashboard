/**
 * "What did my fine-tune change?" — two GGUF models read the same text, and the
 * lens says where, position by position and layer by layer, they part ways.
 *
 * The base writes the reply (greedy); then both are made to read it with the lens
 * on (see compare-run.ts). Reading the *same tokens* is what makes the comparison
 * mean anything: two free generations diverge at the first different token and
 * every position after that compares two different sentences.
 *
 * The picture is a diverging grid: per layer and position, the fine-tune's
 * probability for the token minus the base's — accent where the fine-tune is
 * surer, `--danger` where it is less sure, blank where they agree. Beside it, the
 * settle layer of each token under each model, and the cost in log-probability.
 */
import { useEffect, useMemo, useRef, useState } from 'react';

import { isHosted } from '../../../hosted';
import { apiGet } from '../../../api';
import { listGgufs, nodeGgufModelId, SUPPORTED_ARCHS } from '@horrible/webml';

import { useEngineState } from '../engine';
import { runComparison, type ComparePhase, type CompareResult } from './compare-run';
import { HeatGrid } from './HeatGrid';
import { LayerChart } from './LayerChart';
import { compareRuns, layerP, showToken } from './model';
import { modelName, Stat } from './ReplyView';

interface ModelOption {
  id: string;
  label: string;
  /** What a training run on this node started from, when it was trained here. */
  base?: string | null;
}

interface NodeModel {
  path: string;
  name: string;
  architecture: string;
  isAdapter: boolean;
  baseModel: string | null;
}

/** GGUFs the window can run: this node's (not hosted) and the ones kept in OPFS. */
async function runnableModels(): Promise<ModelOption[]> {
  const out: ModelOption[] = [];
  if (!isHosted()) {
    try {
      const res = await apiGet<{ models: NodeModel[] }>('/llamacpp/models');
      for (const m of res.models) {
        if (m.isAdapter || !SUPPORTED_ARCHS.includes(m.architecture)) continue;
        out.push({
          id: nodeGgufModelId(m.path),
          label: `${m.name} (this node)`,
          base: m.baseModel,
        });
      }
    } catch {
      // No node catalog (llama.cpp module off): the OPFS list still stands.
    }
  }
  try {
    for (const g of await listGgufs()) {
      if (g.complete) out.push({ id: g.id, label: `${modelName(g.id)} (this browser)` });
    }
  } catch {
    // OPFS unavailable (a private window).
  }
  return out;
}

const DEFAULT_PROMPT = 'In one sentence, what is the capital of France known for?';

export function CompareView() {
  const state = useEngineState();
  const [options, setOptions] = useState<ModelOption[] | null>(null);
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [maxTokens, setMaxTokens] = useState(32);
  const [phase, setPhase] = useState<ComparePhase | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState(0);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    let live = true;
    void runnableModels().then((list) => {
      if (!live) return;
      setOptions(list);
      // A model trained here names its base: offer that pair first.
      const tuned = list.find((m) => m.base);
      if (tuned) setB((cur) => cur || tuned.id);
    });
    return () => {
      live = false;
    };
  }, []);

  const comparison = useMemo(() => (result ? compareRuns(result.a, result.b) : null), [result]);

  const run = async () => {
    if (!a || !b || !prompt.trim()) return;
    setError(null);
    setResult(null);
    setPicked(0);
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      setResult(
        await runComparison({
          a,
          b,
          prompt: prompt.trim(),
          maxTokens,
          signal: ctrl.signal,
          onPhase: setPhase,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      abort.current = null;
      setPhase(null);
    }
  };

  const busy = phase !== null;
  const loaded = state.kind === 'ready' ? state.model : null;
  const tunedBase = options?.find((m) => m.id === b)?.base;

  return (
    <div className="ll-view">
      <div className="ll-form">
        <label>
          <span>Base (A)</span>
          <ModelPicker value={a} onChange={setA} options={options} loaded={loaded} />
        </label>
        <label>
          <span>Fine-tune (B)</span>
          <ModelPicker value={b} onChange={setB} options={options} loaded={loaded} />
        </label>
        {tunedBase && (
          <p className="ll-hint">
            B was trained on this node from <code>{tunedBase}</code>; pick that model’s GGUF as A.
          </p>
        )}
        <label className="ll-form-wide">
          <span>Prompt</span>
          <textarea value={prompt} rows={2} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <label>
          <span>Reply tokens</span>
          <input
            type="number"
            min={4}
            max={256}
            value={maxTokens}
            onChange={(e) => setMaxTokens(Math.max(4, Math.min(256, Number(e.target.value) || 32)))}
          />
        </label>
        <div className="ll-actions">
          {busy ? (
            <button type="button" onClick={() => abort.current?.abort()}>
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="is-primary"
              disabled={!a || !b}
              onClick={() => void run()}
            >
              Compare
            </button>
          )}
          <span className="ll-dim">
            {phase?.kind === 'loading' && `Loading ${modelName(phase.model)}…`}
            {phase?.kind === 'writing' && `${modelName(phase.model)} is writing the reply…`}
            {phase?.kind === 'reading' &&
              `${modelName(phase.model)} is reading it: ${phase.done} / ${phase.total} tokens`}
            {!busy && 'Loads A, then B, into this window (the playground’s model is replaced).'}
          </span>
        </div>
      </div>

      {error && <div className="ll-error">{error}</div>}

      {result && comparison && (
        <>
          {!comparison.aligned && (
            <div className="ll-warn">
              The two models tokenized the reply differently, so positions are compared by index and
              stop being the same token after the first difference. A base and its fine-tune share a
              tokenizer; these two do not.
            </div>
          )}
          <div className="ll-reply">
            <span className="ll-dim">Reply (written by A):</span> {result.text}
          </div>
          <div className="ll-stats">
            <Stat
              label="A perplexity"
              value={perplexity(comparison.meanLogpA)}
              title="exp(−mean log-probability) of the reply under A; lower is surer"
            />
            <Stat
              label="B perplexity"
              value={perplexity(comparison.meanLogpB)}
              title="exp(−mean log-probability) of the reply under B"
            />
            <Stat
              label="B would say otherwise"
              value={`${comparison.disagreements} / ${comparison.positions.length}`}
              title="Positions where B's own top choice differs from A's"
            />
            <Stat
              label="mean settle A → B"
              value={`${comparison.meanSettleA?.toFixed(1) ?? '—'} → ${comparison.meanSettleB?.toFixed(1) ?? '—'}`}
              title="Average layer at which each model settles on the reply's tokens"
            />
          </div>

          <div className="ll-charts">
            <LayerChart
              title="Where each token settles"
              xLabel="position"
              format={(v) => v.toFixed(0)}
              series={[
                { label: 'A', values: comparison.positions.map((p) => p.settleA), tone: 'accent' },
                { label: 'B', values: comparison.positions.map((p) => p.settleB), tone: 'danger' },
              ]}
            />
            <LayerChart
              title="p(token) at the output"
              xLabel="position"
              series={[
                { label: 'A', values: comparison.positions.map((p) => p.pA), tone: 'accent' },
                { label: 'B', values: comparison.positions.map((p) => p.pB), tone: 'danger' },
              ]}
            />
          </div>

          <HeatGrid
            label="B minus A: the token's probability at every layer and position"
            rows={comparison.layers}
            cols={comparison.positions.length}
            value={(row, col) =>
              Math.max(-1, Math.min(1, comparison.positions[col].layerDelta[row] ?? 0))
            }
            selectedCol={picked}
            onPickCol={setPicked}
            cell={{
              w: comparison.positions.length > 80 ? 8 : 14,
              h: comparison.layers > 40 ? 7 : 9,
            }}
            tip={(row, col) => {
              const pos = comparison.positions[col];
              const la = result.a.steps[col]?.layers?.[row];
              const lb = result.b.steps[col]?.layers?.[row];
              return (
                <>
                  <b>
                    layer {row} · {showToken(pos.token, 14)}
                  </b>
                  <div>
                    A {layerP(la, pos.token).toFixed(2)} · B {layerP(lb, pos.token).toFixed(2)}
                  </div>
                  <div>Δ {(pos.layerDelta[row] ?? 0).toFixed(2)} (top-5 readouts)</div>
                </>
              );
            }}
          />
          <div className="ll-legend-row">
            <span className="ll-swatch ll-swatch--danger" aria-hidden="true" />
            <span>B less sure</span>
            <span className="ll-swatch ll-swatch--neutral" aria-hidden="true" />
            <span>same</span>
            <span className="ll-swatch ll-swatch--accent" aria-hidden="true" />
            <span>B surer</span>
            <span className="ll-dim">— of the reply's token, at that layer</span>
          </div>

          <PositionDetail result={result} index={picked} />
        </>
      )}
    </div>
  );
}

/** exp(−mean log p), compact when it is enormous (an unrelated model reading the text). */
function perplexity(meanLogp: number): string {
  const v = Math.exp(-meanLogp);
  return v >= 10000 ? v.toExponential(1) : v.toFixed(2);
}

function ModelPicker({
  value,
  onChange,
  options,
  loaded,
}: {
  value: string;
  onChange: (id: string) => void;
  options: ModelOption[] | null;
  loaded: string | null;
}) {
  const list = useMemo(() => {
    const all = [...(options ?? [])];
    if (loaded && loaded.startsWith('gguf') && !all.some((o) => o.id === loaded))
      all.unshift({ id: loaded, label: `${modelName(loaded)} (loaded)` });
    return all;
  }, [options, loaded]);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{options === null ? 'Looking for models…' : 'Pick a GGUF'}</option>
      {list.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/** One position, both stacks side by side. */
function PositionDetail({ result, index }: { result: CompareResult; index: number }) {
  const sa = result.a.steps[index];
  const sb = result.b.steps[index];
  if (!sa || !sb) return null;
  const layers = Math.max(sa.layers?.length ?? 0, sb.layers?.length ?? 0);
  return (
    <div className="ll-detail">
      <div className="ll-detail-head">
        <span className="ll-big-token">{showToken(sa.token, 24)}</span>
        <span className="ll-dim">
          position {index} · p A {sa.p.toFixed(3)} · p B {sb.p.toFixed(3)}
        </span>
      </div>
      <table className="ll-stack">
        <thead>
          <tr>
            <th scope="col">layer</th>
            <th scope="col">A says</th>
            <th scope="col">B says</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: layers }, (_, l) => {
            const ta = sa.layers?.[l]?.top[0];
            const tb = sb.layers?.[l]?.top[0];
            return (
              <tr key={l} className={ta?.token !== tb?.token ? 'is-differ' : ''}>
                <td>{l}</td>
                <td className={`ll-tok${ta?.token === sa.token ? ' is-chosen' : ''}`}>
                  {ta ? `${showToken(ta.token, 14)} ${ta.p.toFixed(2)}` : '—'}
                </td>
                <td className={`ll-tok${tb?.token === sb.token ? ' is-chosen' : ''}`}>
                  {tb ? `${showToken(tb.token, 14)} ${tb.p.toFixed(2)}` : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
