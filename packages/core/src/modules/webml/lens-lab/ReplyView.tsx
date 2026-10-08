/**
 * The window's last lens run, all of it: every token of the reply against every
 * layer, and the stack under one token in detail.
 *
 * A cell is the probability that layer gives the token the model finally chose
 * (from its top-5 readout; blank means not in the top 5). So a column fills in as
 * the prediction forms, and the outlined cells are where the chosen token is that
 * layer's best. The tick is where it settles: the first layer from which it stays
 * best to the end.
 */
import { useEffect, useMemo, useState } from 'react';

import { settlesAt, useLensRun, type LensRun } from '../lens-run';
import { HeatGrid } from './HeatGrid';
import { LayerChart } from './LayerChart';
import { layerP, showToken } from './model';

/** The file's name from a `gguf:` id or a node path. */
export function modelName(model: string): string {
  return model.split(/[\\/:]/).pop() || model;
}

export function LensEmpty({ action }: { action?: React.ReactNode }) {
  return (
    <div className="ll-empty">
      <p>
        <b>No lens run yet.</b> Load a GGUF model in the WebML playground, turn on the logit lens
        (the <code>webml.lens</code> setting), and ask it something. Every token of the reply then
        records what each layer would have said, and it shows up here as it is written.
      </p>
      {action}
    </div>
  );
}

export function TokenDetail({ run, index }: { run: LensRun; index: number }) {
  const t = run.tokens[index];
  if (!t) return null;
  const settles = settlesAt(t);
  const most = Math.max(...t.layers.map((l) => l.norm), 1e-9);
  return (
    <div className="ll-detail">
      <div className="ll-detail-head">
        <span className="ll-big-token">{showToken(t.token, 24)}</span>
        <span className="ll-dim">
          token {index} · p {t.p.toFixed(3)} ·{' '}
          {settles === null
            ? 'never the best at the last layer (a sampled runner-up)'
            : settles === 0
              ? 'the best from the very first layer'
              : `settles at layer ${settles} of ${t.layers.length}`}
        </span>
      </div>
      <div className="ll-charts">
        <LayerChart
          title="Residual stream norm"
          xLabel="layer"
          series={[{ label: 'norm', values: t.layers.map((l) => l.norm), tone: 'accent' }]}
          format={(v) => v.toFixed(0)}
          marker={settles !== null ? { x: settles, label: 'settles' } : null}
        />
        <LayerChart
          title="Entropy of the readout (bits)"
          xLabel="layer"
          series={[{ label: 'entropy', values: t.layers.map((l) => l.entropy), tone: 'accent' }]}
          marker={settles !== null ? { x: settles, label: 'settles' } : null}
        />
      </div>
      <table className="ll-stack">
        <thead>
          <tr>
            <th scope="col">layer</th>
            <th scope="col">best token</th>
            <th scope="col">p</th>
            <th scope="col">p({showToken(t.token, 8)})</th>
            <th scope="col">norm</th>
          </tr>
        </thead>
        <tbody>
          {t.layers.map((l, i) => {
            const best = l.top[0];
            const chosen = best?.token === t.token;
            return (
              <tr
                key={i}
                className={`${chosen ? 'is-chosen' : ''}${i === settles ? ' is-settle' : ''}`}
              >
                <td>{i}</td>
                <td className="ll-tok">{best ? showToken(best.token, 14) : '—'}</td>
                <td>{best ? best.p.toFixed(2) : ''}</td>
                <td>
                  {inTopLabel(
                    layerP(l, t.token),
                    l.top.some((x) => x.token === t.token),
                  )}
                </td>
                <td>
                  <span className="ll-bar" title={l.norm.toFixed(1)}>
                    <span style={{ width: `${Math.max(2, (l.norm / most) * 100)}%` }} />
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function inTopLabel(p: number, inTop: boolean): string {
  return inTop ? p.toFixed(2) : '< top 5';
}

export function ReplyView() {
  const run = useLensRun();
  const [picked, setPicked] = useState<number | null>(null);
  // Follow a reply being written: the newest token is the interesting one until
  // the reader picks another.
  const last = run ? run.tokens.length - 1 : -1;
  useEffect(() => setPicked(null), [run?.id]);
  const index = picked ?? last;

  const value = useMemo(
    () => (row: number, col: number) => {
      const t = run?.tokens[col];
      return t ? layerP(t.layers[row], t.token) : 0;
    },
    [run],
  );
  const marked = useMemo(
    () => (row: number, col: number) => {
      const t = run?.tokens[col];
      return !!t && t.layers[row]?.top[0]?.token === t.token;
    },
    [run],
  );
  const tick = useMemo(
    () => (col: number) => {
      const t = run?.tokens[col];
      return t ? settlesAt(t) : null;
    },
    [run],
  );

  if (!run || run.tokens.length === 0) return <LensEmpty />;

  const settles = run.tokens.map(settlesAt);
  const settled = settles.filter((s): s is number => s !== null);
  return (
    <div className="ll-view">
      <div className="ll-stats">
        <Stat label="model" value={modelName(run.model)} title={run.model} />
        <Stat label="tokens" value={String(run.tokens.length)} />
        <Stat label="layers" value={String(run.layers)} />
        <Stat label="median settle layer" value={settled.length ? String(median(settled)) : '—'} />
        {run.live && <span className="ll-live">● writing</span>}
      </div>

      <div className="ll-strip" aria-label="The reply; pick a token">
        {run.tokens.map((t, i) => (
          <button
            key={i}
            type="button"
            className={`ll-strip-tok${i === index ? ' is-on' : ''}${settles[i] === null ? ' is-never' : ''}`}
            style={{ ['--settle' as string]: depth(settles[i], run.layers) }}
            title={`${JSON.stringify(t.token)} · ${settles[i] === null ? 'never settled' : `settles at layer ${settles[i]}`}`}
            onClick={() => setPicked(i)}
          >
            {showToken(t.token, 16)}
          </button>
        ))}
      </div>
      <p className="ll-hint">
        Underline depth is where each token settled: shallow for words the early layers already had,
        deep for ones decided late.
      </p>

      <HeatGrid
        label="Logit lens: the chosen token's probability at every layer, for every token"
        rows={run.layers}
        cols={run.tokens.length}
        value={value}
        marked={marked}
        tick={tick}
        selectedCol={index}
        onPickCol={setPicked}
        cell={{ w: run.tokens.length > 120 ? 6 : 12, h: run.layers > 40 ? 7 : 9 }}
        tip={(row, col) => {
          const t = run.tokens[col];
          const l = t.layers[row];
          return (
            <>
              <b>
                layer {row} · {showToken(t.token, 14)}
              </b>
              <div>
                best {l?.top[0] ? `${showToken(l.top[0].token, 14)} ${l.top[0].p.toFixed(2)}` : '—'}
              </div>
              <div>
                p(chosen){' '}
                {inTopLabel(layerP(l, t.token), !!l?.top.some((x) => x.token === t.token))} · H{' '}
                {l?.entropy.toFixed(2)} bits
              </div>
            </>
          );
        }}
      />
      <div className="ll-legend-row">
        <span className="ll-scale" aria-hidden="true" />
        <span>p(chosen token) at that layer, 0 → 1</span>
        <span className="ll-mark-key" aria-hidden="true" />{' '}
        <span>chosen token is the layer’s best</span>
        <span className="ll-tick-key" aria-hidden="true" /> <span>settles here</span>
      </div>

      <TokenDetail run={run} index={index} />
    </div>
  );
}

/** 0 (settled at the first layer) to 1 (at the last, or never). */
function depth(settle: number | null, layers: number): string {
  if (settle === null) return '1';
  return (settle / Math.max(1, layers - 1)).toFixed(3);
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

export function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="ll-stat" title={title}>
      <div className="ll-stat-value">{value}</div>
      <div className="ll-stat-label">{label}</div>
    </div>
  );
}
