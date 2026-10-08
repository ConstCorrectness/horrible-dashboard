/**
 * The logit lens of the model running in this window, beside the structure of the
 * model being explored.
 *
 * The explorer's own lens is the llama.cpp trace grid, which reads a trace the
 * node captured. This is the other source: the WGSL engine's readout during an
 * ordinary generation (the playground or a `{webllm}` with the lens on), recorded
 * by `webml/lens-run.ts`. A layer × token grid: each cell is what the LM head
 * would have said from the residual stream after that layer, shaded by its
 * probability, in the accent where it is the token finally chosen. The footer row
 * says where each prediction settles.
 *
 * The run is a different model from the explored one more often than not (the
 * explorer follows the node's model). Rows link to blocks only when the layer
 * counts agree, and the header always names the run's model.
 */
import { settlesAt, useLensRun, type LensToken } from '../../webml';

import './window-lens.css';

/** Columns drawn: the reply's last this-many tokens. */
const COLUMNS = 32;

/** A token as a cell can show it: whitespace made visible, long ones cut. */
function show(token: string): string {
  const t = token.replace(/\n/g, '↵').replace(/ /g, '·');
  return t.length > 8 ? `${t.slice(0, 7)}…` : t || '∅';
}

/** `gguf:owner/repo/file.gguf` or a node path → the file's name. */
function modelName(model: string): string {
  return model.split(/[\\/:]/).pop() || model;
}

export function WindowLens({
  blocks,
  selectedLayer,
  onPickLayer,
}: {
  /** The explored model's block count; rows link to blocks only when it matches. */
  blocks: number | null;
  selectedLayer: number | null;
  /** Select a block in the diagram; without it rows are not links. */
  onPickLayer?: (layer: number) => void;
}) {
  const run = useLensRun();
  if (!run || run.tokens.length === 0) return null;

  const tokens: LensToken[] = run.tokens.slice(-COLUMNS);
  const linked = !!onPickLayer && blocks === run.layers;
  const settled = tokens.map(settlesAt);
  const layers = Array.from({ length: run.layers }, (_, l) => l);

  return (
    <section className="wl-root" aria-label="Logit lens of this window's model">
      <div className="wl-head">
        <b>Logit lens · this window</b>
        <span className="interp-dim" title={run.model}>
          {modelName(run.model)} · {run.layers} layers · {run.tokens.length} token
          {run.tokens.length === 1 ? '' : 's'}
          {run.tokens.length > COLUMNS ? ` (last ${COLUMNS} shown)` : ''}
          {run.live ? ' · generating' : ''}
        </span>
      </div>
      {blocks != null && !linked && (
        <div className="md-note">
          This window's model has {run.layers} layers and the one above has {blocks} blocks, so its
          rows are not linked to the diagram.
        </div>
      )}
      <div className="wl-scroll">
        <table className="wl-grid">
          <thead>
            <tr>
              <th scope="col">layer</th>
              {tokens.map((t, i) => (
                <th key={i} scope="col" title={`${JSON.stringify(t.token)} · p ${t.p.toFixed(3)}`}>
                  {show(t.token)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {layers.map((l) => (
              <tr key={l} className={l === selectedLayer && linked ? 'is-selected' : undefined}>
                <th scope="row">
                  {linked ? (
                    <button
                      type="button"
                      className="wl-layer"
                      title={`Show block ${l} in the diagram`}
                      onClick={() => onPickLayer?.(l)}
                    >
                      {l}
                    </button>
                  ) : (
                    l
                  )}
                </th>
                {tokens.map((t, i) => {
                  const layer = t.layers[l];
                  const best = layer?.top[0];
                  const chosen = best?.token === t.token;
                  return (
                    <td
                      key={i}
                      className={`${chosen ? 'is-chosen' : ''}${settled[i] !== null && l >= settled[i]! ? ' is-settled' : ''}`}
                      style={{ ['--p' as string]: (best?.p ?? 0).toFixed(3) }}
                      title={
                        layer && best
                          ? `layer ${l} · ${JSON.stringify(best.token)} p ${best.p.toFixed(3)} · H ${layer.entropy.toFixed(2)} bits · norm ${layer.norm.toFixed(1)}`
                          : undefined
                      }
                    >
                      {best ? show(best.token) : ''}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th
                scope="row"
                title="The first layer from which the chosen token leads at every layer"
              >
                settles
              </th>
              {settled.map((s, i) => (
                <td key={i}>{s ?? '—'}</td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
