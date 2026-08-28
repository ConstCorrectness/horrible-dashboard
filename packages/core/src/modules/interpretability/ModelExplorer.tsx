import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { SplitPane } from '../../SplitPane';
import { useModelLocus } from '../../model-locus';
import { ModelDesigner } from './designer/ModelDesigner';
import { buildInspectGraph, inspectGraphKey } from './inspect/graph';
import { HeadGrouping } from './inspect/HeadGrouping';
import { InspectCanvas } from './inspect/InspectCanvas';

import {
  interpretabilityStore,
  type ModelArchitecture,
  type ModelTensors,
  type TensorEntry,
} from './store';

/**
 * The loaded model, as a thing you can click through rather than a picture of one.
 *
 * The old pane drew a schematic from `/api/show`'s scalar metadata: correct, and
 * inert. This one is backed by the model's own GGUF tensor directory, so selecting
 * a stage shows the tensors that actually implement it — real names, real shapes,
 * the mixed quantization a K-quant build genuinely uses, and where the bytes sit.
 *
 * Two sources, and the difference matters enough to be shown rather than merged:
 * `architecture` is the normalized *description* (also available for models we can
 * only reach through a Hugging Face repo), while `tensors` is the *inventory*, which
 * exists only when we opened the weights file. A model can have the first without
 * the second; the pane stays useful, minus the tensor tables.
 */
function useArchitecture(): ModelArchitecture | null {
  return useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
}

function useTensors(): ModelTensors | null {
  return useSyncExternalStore(interpretabilityStore.subscribe, interpretabilityStore.getTensors);
}

function fmtCount(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

function fmtBytes(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

const ATTENTION_LABEL: Record<string, string> = {
  mha: 'Multi-head attention',
  gqa: 'Grouped-query attention',
  mqa: 'Multi-query attention',
  unknown: 'Attention',
};

/** What the tree currently has selected. `layer: null` aggregates across blocks. */
interface Selection {
  stage: string;
  layer: number | null;
}

/** A `<dl>` that omits rows whose value is unknown — a gap is never drawn as "—". */
function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  const present = rows.filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (present.length === 0) return null;
  return (
    <dl className="md-facts">
      {present.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TensorTable({ tensors }: { tensors: TensorEntry[] }) {
  const [expanded, setExpanded] = useState(false);
  // A 70B MoE lists thousands of tensors; rendering them all into a detail panel
  // that is usually skimmed costs more than it tells you.
  const LIMIT = 60;
  const shown = expanded ? tensors : tensors.slice(0, LIMIT);
  if (tensors.length === 0) return null;

  return (
    <div className="mx-tensors">
      <table>
        <thead>
          <tr>
            <th>Tensor</th>
            <th>Shape</th>
            <th>Type</th>
            <th>Size</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((t) => (
            <tr key={t.name}>
              <td>
                <code>{t.name}</code>
              </td>
              <td className="mx-num">{t.shape.join(' × ')}</td>
              <td>
                <span className="mx-dtype">{t.dtype}</span>
              </td>
              <td className="mx-num">
                {t.byteSize == null ? (
                  <span
                    className="interp-warn-chip"
                    title={`Unrecognized quantization (${t.dtype}) — no block size known, so no size is reported rather than a guessed one.`}
                  >
                    unknown
                  </span>
                ) : (
                  fmtBytes(t.byteSize)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {tensors.length > LIMIT && (
        <button className="mx-more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show fewer' : `Show all ${tensors.length} tensors`}
        </button>
      )}
    </div>
  );
}

/**
 * Bytes a full KV cache occupies at a given context length.
 *
 * Rendered as a *range*, not a number, because llama.cpp's cache element type is a
 * runtime choice (F16 by default, Q8_0 or Q4_0 when configured) that the model file
 * does not record. Reporting the F16 figure alone would state as fact something the
 * user can change from the command line.
 */
function kvCacheBytes(arch: ModelArchitecture, ctx: number): number | null {
  const kv = arch.attention?.kvHeads;
  const dim = arch.attention?.headDim;
  if (!arch.layers || !kv || !dim) return null;
  return 2 * arch.layers * kv * dim * ctx * 2; // K and V, 2 bytes/element (F16)
}

/**
 * Inspect mode: the loaded model's weights, unchanged. See the wrapper below for
 * why it is now one of two modes rather than the whole pane.
 */
function ModelInspector() {
  const arch = useArchitecture();
  const tensors = useTensors();
  const [selection, setSelection] = useState<Selection>({ stage: 'model', layer: null });
  const [layerOpen, setLayerOpen] = useState(false);

  // The inventory is per-model and large, so it is pulled on mount rather than
  // riding the per-turn refresh. Re-runs when the model changes.
  const modelName = arch?.model ?? '';
  useEffect(() => {
    void interpretabilityStore.ensureTensors();
  }, [modelName]);

  const inventory = tensors?.source === 'gguf' ? tensors : null;

  /**
   * Follow the model locus: clicking layer 15 in the lens grid reveals
   * `blk.15`'s tensors here. The two panes know nothing about each other — see
   * `packages/core/src/model-locus.ts`.
   *
   * `!= null` and not a truthiness check: layer 0 is the first decoder block and
   * `-1` is the embedding, so both are real values that a falsy test drops.
   */
  const locus = useModelLocus();
  const locusLayer = locus.layer;
  /**
   * Bumped only when the selection arrived from OUTSIDE, so the canvas can bring
   * that node into view. A counter and not the layer itself: clicking layer 15 in
   * the lens twice must re-centre, and a value-keyed effect would treat the second
   * click as a no-op.
   */
  const [focusNonce, setFocusNonce] = useState(0);
  useEffect(() => {
    if (locusLayer == null) return;
    if (locusLayer < 0) {
      setSelection({ stage: 'embedding', layer: null });
      setFocusNonce((n) => n + 1);
      return;
    }
    setSelection({ stage: 'block', layer: locusLayer });
    // Opening the block is the point: a selected block whose tensor list is
    // still collapsed looks like nothing happened.
    setLayerOpen(true);
    setFocusNonce((n) => n + 1);
  }, [locusLayer]);

  /** Tensors grouped by (component, layer), computed once per inventory. */
  const index = useMemo(() => {
    const byStage = new Map<string, TensorEntry[]>();
    const byStageLayer = new Map<string, TensorEntry[]>();
    for (const t of inventory?.tensors ?? []) {
      const stage = byStage.get(t.component) ?? [];
      stage.push(t);
      byStage.set(t.component, stage);
      if (t.layer !== null) {
        const key = `${t.component}:${t.layer}`;
        const cell = byStageLayer.get(key) ?? [];
        cell.push(t);
        byStageLayer.set(key, cell);
      }
    }
    return { byStage, byStageLayer };
  }, [inventory]);

  const selected = useMemo((): TensorEntry[] => {
    if (!inventory) return [];
    if (selection.stage === 'model') return inventory.tensors;
    if (selection.stage === 'block') {
      return inventory.tensors.filter((t) => t.layer === selection.layer);
    }
    return selection.layer === null
      ? (index.byStage.get(selection.stage) ?? [])
      : (index.byStageLayer.get(`${selection.stage}:${selection.layer}`) ?? []);
  }, [inventory, index, selection]);

  /**
   * Tensor roles whose shape is not the same in every block.
   *
   * The stack above draws one decoder block "× N", and every scalar source it can
   * draw from — GGUF metadata, a repo's config.json — reports a single head count
   * for the whole model. That is often a lie: Gemma 4 alternates its attention
   * shape by layer, so `blk.0.attn_q` is 3840×4096 while `blk.17.attn_q` is
   * 3840×8192. The inventory is the only source that can see it, and a "× 48" with
   * one head count beside it actively misleads if it isn't said.
   */
  const nonUniform = useMemo(() => {
    if (!inventory) return [];
    const shapesByRole = new Map<string, Set<string>>();
    for (const t of inventory.tensors) {
      if (t.layer === null) continue;
      const role = t.name.replace(/(?:^|\.)blk\.\d+\./, '');
      const shapes = shapesByRole.get(role) ?? new Set<string>();
      shapes.add(t.shape.join('×'));
      shapesByRole.set(role, shapes);
    }
    return [...shapesByRole.entries()]
      .filter(([, shapes]) => shapes.size > 1)
      .map(([role, shapes]) => ({ role, shapes: [...shapes] }));
  }, [inventory]);

  /**
   * The drawing.
   *
   * Memoized on a STRUCTURAL key, never on object identity: the interpretability
   * store refetches on a schedule and an identical refetch produces a new object
   * every time, so identity-keyed memos would rebuild every node on every poll —
   * and React Flow, handed new node objects, drops the viewport mid-pan.
   */
  const graphKey = inspectGraphKey(arch, inventory, selection, layerOpen);
  const graph = useMemo(
    () =>
      arch
        ? buildInspectGraph(arch, inventory, selection, layerOpen)
        : { nodes: [], edges: [], focusId: null },
    // `graphKey` is the ONLY dependency, by design. `useMemo` runs during render,
    // so the values read here are always current — the deliberate choice is not to
    // recompute when they change identity without changing what is drawn. If a
    // newly-drawn field is ever added above, it has to be added to the key too.
    [graphKey]
  );

  const selectedBytes = useMemo(
    () => selected.reduce((sum, t) => sum + (t.byteSize ?? 0), 0),
    [selected],
  );
  const selectedParams = useMemo(
    () => selected.reduce((sum, t) => sum + t.elements, 0),
    [selected],
  );

  if (!arch || arch.source === 'none') {
    return (
      <div className="interp-empty">
        <p>No model architecture available.</p>
        <p className="interp-dim">
          {arch?.error ??
            'Load a model and open a turn — the explorer describes whichever model the captured turns ran on.'}
        </p>
      </div>
    );
  }

  const attn = arch.attention;

  const sourceChip =
    arch.source === 'ollama' ? (
      <span
        className="mx-src mx-src-strong"
        title={`Read from the loaded weights via ${arch.sourceDetail}`}
      >
        weights
      </span>
    ) : (
      <span
        className="mx-src"
        title={`Read from the repo ${arch.sourceDetail} — describes the architecture, not necessarily your exact weights`}
      >
        repo
      </span>
    );

  return (
    <div className="mx-root">
      <div className="mx-head">
        <span className="interp-model">{arch.model}</span>
        {arch.family && <span className="interp-dim">{arch.family}</span>}
        {sourceChip}
        {inventory && (
          <span className="mx-src mx-src-strong" title={inventory.path}>
            gguf v{inventory.ggufVersion}
          </span>
        )}
      </div>

      <div className="mx-body">
        <SplitPane
          id="mx.inspect"
          initial={260}
          min={190}
          minOther={320}
          narrowBelow={720}
          label="Diagram width"
        >
        {/* ── The architecture ────────────────────────────────────────
            Was a vertical stack of buttons in a 260px CSS grid column: it could
            not be resized, could not be zoomed, and drew the residual as a text
            row that connected nothing. `Selection` is unchanged and still the
            source of truth — the canvas is a projection of it, which is why the
            model-locus effect above needed no edit. */}
        <InspectCanvas
          graph={graph}
          selection={selection}
          onSelect={(next) => {
            setSelection(next);
            // Clicking the stack means "show me inside this block". The old UI had
            // a separate disclosure triangle for that; the canvas has the node
            // itself, and a click that selected the stack without opening it would
            // leave the internals unreachable.
            if (next.stage === 'block') setLayerOpen((open) => !open || selection.stage !== 'block');
          }}
          onPickLayer={(layer) => {
            setSelection({ stage: 'block', layer });
            setLayerOpen(true);
          }}
          focusNonce={focusNonce}
        />

        {/* ── Detail for the selection ────────────────────────────────── */}
        <div className="mx-detail">
          <div className="mx-detail-head">
            <b>
              {selection.stage === 'model'
                ? 'Whole model'
                : selection.stage === 'block'
                  ? `Block ${selection.layer}`
                  : selection.stage}
            </b>
            {selection.layer !== null && selection.stage !== 'model' && (
              <span className="interp-dim">block {selection.layer}</span>
            )}
          </div>

          {selection.stage === 'model' && (
            <Facts
              rows={[
                ['Layers', arch.layers],
                ['Hidden size', arch.hiddenSize],
                ['Vocab', fmtCount(arch.vocabSize)],
                ['Context length', arch.contextLength?.toLocaleString()],
                ['Norm', arch.normType],
                [
                  // Named with the actual window rather than "@ ctx": at 262k the
                  // figure runs to tens of gigabytes, and a number that size with
                  // no denominator beside it reads as a bug.
                  `KV cache @ ${arch.contextLength?.toLocaleString() ?? '—'}`,
                  arch.contextLength && kvCacheBytes(arch, arch.contextLength)
                    ? `${fmtBytes(kvCacheBytes(arch, arch.contextLength))} at F16`
                    : null,
                ],
                ['File size', inventory ? fmtBytes(inventory.fileSize) : null],
              ]}
            />
          )}

          {selection.stage === 'attention' && attn && (
            <>
              <Facts
                rows={[
                  ['Kind', ATTENTION_LABEL[attn.kind] ?? attn.kind],
                  ['Query heads', attn.heads],
                  ['KV heads', attn.kvHeads],
                  [
                    'Head dim',
                    attn.headDim != null
                      ? `${attn.headDim}${attn.headDimDerived ? ' (derived)' : ''}`
                      : null,
                  ],
                  ['Group ratio', attn.groupRatio ? `${attn.groupRatio}:1` : null],
                  ['Sliding window', attn.slidingWindow],
                  ['RoPE θ', attn.ropeTheta?.toLocaleString()],
                ]}
              />
              <HeadGrouping attention={attn} />
            </>
          )}

          {selection.stage === 'ffn' && arch.ffn && (
            <Facts
              rows={[
                ['Intermediate', arch.ffn.intermediateSize],
                ['Expansion', arch.ffn.expansionRatio ? `${arch.ffn.expansionRatio}×` : null],
                ['Activation', arch.ffn.activation],
                [
                  'Gating',
                  arch.ffn.gated === null
                    ? 'unknown'
                    : arch.ffn.gated
                      ? 'gated (two up-projections)'
                      : 'dense (one up-projection)',
                ],
              ]}
            />
          )}

          {selection.stage === 'moe' && arch.moe && (
            <Facts
              rows={[
                ['Experts', arch.moe.experts],
                ['Active per token', arch.moe.expertsPerToken],
                ['Shared experts', arch.moe.sharedExperts],
                ['Expert intermediate', arch.moe.expertIntermediateSize],
                [
                  'Active fraction',
                  arch.moe.activeFraction != null
                    ? `${(arch.moe.activeFraction * 100).toFixed(1)}%`
                    : null,
                ],
              ]}
            />
          )}

          {inventory ? (
            <>
              <div className="mx-detail-sum">
                <span>
                  <b>{selected.length}</b> tensors
                </span>
                <span>
                  <b>{fmtCount(selectedParams)}</b> params
                </span>
                <span>
                  <b>{fmtBytes(selectedBytes)}</b>
                  {inventory.totalBytes > 0 && (
                    <span className="interp-dim">
                      {' '}
                      · {((selectedBytes / inventory.totalBytes) * 100).toFixed(1)}% of file
                    </span>
                  )}
                </span>
              </div>
              {selection.stage === 'model' && (
                <div className="mx-quants">
                  {Object.entries(inventory.quantTypes).map(([type, count]) => (
                    <span key={type} className="mx-dtype" title={`${count} tensors`}>
                      {type} <b>{count}</b>
                    </span>
                  ))}
                  {!inventory.bytesComplete && (
                    <span
                      className="interp-warn-chip"
                      title="At least one tensor uses a quantization we have no block size for, so the total is a floor rather than the true size."
                    >
                      size is a floor
                    </span>
                  )}
                </div>
              )}
              <TensorTable tensors={selected} />
            </>
          ) : (
            <div className="mx-noinv interp-dim">
              {tensors?.error ??
                'No GGUF inventory — the structure above comes from metadata alone.'}
            </div>
          )}

          {(arch.notes.length > 0 || (selection.stage === 'model' && nonUniform.length > 0)) && (
            <div className="mx-notes">
              {selection.stage === 'model' && nonUniform.length > 0 && (
                <div className="md-note">
                  <b>Blocks are not identical.</b> {nonUniform.length} tensor role
                  {nonUniform.length === 1 ? '' : 's'} change shape between layers, so the single
                  head count above describes some blocks and not others:{' '}
                  {nonUniform
                    .slice(0, 4)
                    .map((v) => `${v.role} (${v.shapes.join(', ')})`)
                    .join('; ')}
                  {nonUniform.length > 4 ? '; …' : ''}
                </div>
              )}
              {arch.notes.map((note) => (
                <div key={note} className="md-note">
                  {note}
                </div>
              ))}
            </div>
          )}
        </div>
        </SplitPane>
      </div>
    </div>
  );
}

/**
 * The pane's two modes: the model that exists, and the one you are drawing.
 *
 * They share a pane rather than getting one each because of the bridge between
 * them. Inspect answers "what is this model"; Design answers "what if it were
 * different" — and the obvious way to start a design is from the thing you are
 * currently reading. Splitting them into two panes would put a workspace switch
 * between a question and its follow-up.
 */
export function ModelExplorer() {
  const [mode, setMode] = useState<'inspect' | 'design'>('inspect');
  return (
    <div className="mx-pane">
      <div className="mx-modes" role="tablist" aria-label="Model explorer mode">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'inspect'}
          className={`mx-mode${mode === 'inspect' ? ' mx-mode-on' : ''}`}
          onClick={() => setMode('inspect')}
        >
          Inspect
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'design'}
          className={`mx-mode${mode === 'design' ? ' mx-mode-on' : ''}`}
          onClick={() => setMode('design')}
        >
          Design
        </button>
      </div>
      <div className="mx-mode-body">{mode === 'inspect' ? <ModelInspector /> : <ModelDesigner />}</div>
    </div>
  );
}
