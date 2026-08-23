import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { setModelLocus, useModelLocus } from '../../../model-locus';
import {
  forkTrace,
  getLensGrid,
  getLensTrack,
  listLenses,
  listTraces,
  saveFinding,
  type LensGrid,
  type LensSpec,
  type LensTrack,
  type Progress,
  type TraceSummary,
  type VocabEntry,
} from '../api';
import {
  cellBackground,
  diffGrids,
  displayToken,
  editableTokens,
  layerLabel,
  rankBackground,
  trackRank,
  type CellDiff,
} from './grid-model';
import { TokenPicker } from './TokenPicker';
import {
  addVocabPin,
  clearVocabPins,
  getVocabPins,
  MAX_VOCAB_PINS,
  removeVocabPin,
  type VocabPin,
} from './vocab-pins';

/**
 * The lens: a trace read as words rather than as numbers.
 *
 * The Traces section is a debugger — pin a node, watch its numbers. This is the
 * other question, the one Neuronpedia's Jacobian Lens asks: at every layer and
 * every position, what is the model *disposed to say*? The grid is that answer,
 * one word per cell, and the bottom row is what the model really did emit.
 *
 * Two things make it a tool rather than a picture, and both are here:
 *
 * - **Pin a word and see it everywhere.** A grid shows what won; a track shows
 *   where the word you care about was while it was losing.
 * - **Swap a token and look again.** The grid alone cannot separate "the model
 *   knows this" from "the prompt said it". Replacing one token and diffing the
 *   two grids can.
 */

function VerifyBanner({ grid }: { grid: LensGrid }) {
  const tone = grid.verified === 'true' ? 'ok' : grid.verified === 'false' ? 'bad' : 'unknown';
  const title =
    grid.verified === 'true'
      ? 'Checked against the model'
      : grid.verified === 'false'
        ? 'Unverified — the numbers below disagree with the model'
        : 'Not checked';
  const detail = grid.verifyDetail as { maxAbsDiff?: number; normMaxAbsDiff?: number };
  return (
    <div className={`llama-verify llama-verify-${tone}`}>
      <strong>{title}</strong>
      <p className="llama-meta">{grid.verifyNote}</p>
      {typeof detail.maxAbsDiff === 'number' ? (
        <p className="llama-meta">
          largest disagreement {detail.maxAbsDiff}
          {typeof detail.normMaxAbsDiff === 'number'
            ? ` · final norm ${detail.normMaxAbsDiff}`
            : ''}
        </p>
      ) : null}
    </div>
  );
}

function TokenStrip({
  grid,
  swapping,
  onSwap,
}: {
  grid: LensGrid;
  swapping: number | null;
  onSwap: (position: number) => void;
}) {
  const tokens = editableTokens(grid.tokens);
  if (!tokens.length) return null;
  return (
    <div className="llama-tokens llama-lens-strip">
      {tokens.map((token) => (
        <button
          key={token.index}
          className={`llama-token llama-token-swap${swapping === token.index ? ' llama-token-swapping' : ''}`}
          title={`#${token.index} · id ${token.id} · click to replace and re-run`}
          onClick={() => onSwap(token.index)}
        >
          {displayToken(token.text, 16)}
        </button>
      ))}
    </div>
  );
}

function Grid({
  grid,
  track,
  diff,
  onSelect,
  selected,
}: {
  grid: LensGrid;
  track: LensTrack | null;
  diff: CellDiff[][] | null;
  onSelect: (layer: number, position: number) => void;
  selected: { layer: number; position: number } | null;
}) {
  const vocab = grid.unembedding.nVocab || 1;
  // Ascending, so the bottom row is the top block — the one whose readout is the
  // model's actual output. Reading a lens upside down inverts every story it tells.
  return (
    <div className="llama-lens-scroll">
      <table className="llama-lens-grid">
        <thead>
          <tr>
            <th className="llama-lens-corner" />
            {grid.positions.map((position) => {
              const token = grid.tokens[position];
              return (
                <th key={position} title={token ? `#${position} · id ${token.id}` : undefined}>
                  {token ? displayToken(token.text, 8) : position}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {grid.layers.map((layer, row) => (
            <tr key={layer}>
              <th scope="row">{layerLabel(layer)}</th>
              {grid.positions.map((position, col) => {
                const cell = grid.cells[row]?.[col];
                const rank = track ? trackRank(track, layer, position) : null;
                const change = diff?.[row]?.[col];
                const isSelected = selected?.layer === layer && selected.position === position;
                const background =
                  rank !== null ? rankBackground(rank, vocab) : cellBackground(cell);
                const classes = [
                  'llama-lens-cell',
                  isSelected ? 'llama-lens-cell-on' : '',
                  change?.changed ? 'llama-lens-cell-changed' : '',
                ]
                  .filter(Boolean)
                  .join(' ');
                return (
                  <td key={position} className={classes} style={{ background }}>
                    <button
                      className="llama-lens-cellbtn"
                      onClick={() => onSelect(layer, position)}
                      title={
                        change?.changed
                          ? `was ${change.was} → now ${change.now}`
                          : cell?.texts.slice(0, 5).join(' · ')
                      }
                    >
                      <span className="llama-lens-word">
                        {displayToken(cell?.texts[0] ?? '', 9)}
                      </span>
                      {rank !== null ? <sup className="llama-lens-rank">{rank}</sup> : null}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CellDetail({
  grid,
  at,
  onPin,
}: {
  grid: LensGrid;
  at: { layer: number; position: number };
  onPin: (id: number, text: string) => void;
}) {
  const row = grid.layers.indexOf(at.layer);
  const col = grid.positions.indexOf(at.position);
  const cell = grid.cells[row]?.[col];
  if (!cell) return null;
  return (
    <div className="llama-card">
      <h3>
        {layerLabel(at.layer)} · position {at.position}
      </h3>
      <ol className="llama-lens-candidates">
        {cell.ids.map((id, index) => (
          <li key={id}>
            <button
              className="llama-lens-candidate"
              onClick={() => onPin(id, cell.texts[index] ?? '')}
            >
              <span className="llama-lens-word">{displayToken(cell.texts[index] ?? '', 20)}</span>
              <span className="llama-meta">
                {cell.logits[index]?.toFixed(2)} · #{id}
              </span>
            </button>
          </li>
        ))}
      </ol>
      <p className="llama-meta">
        Click a candidate to pin it and see where it is across the whole grid. Shading is the top
        candidate&rsquo;s share <em>among these</em>, not the model&rsquo;s probability.
      </p>
    </div>
  );
}

export function LensSection() {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [traceId, setTraceId] = useState('');
  const [lenses, setLenses] = useState<LensSpec[]>([]);
  const [lensId, setLensId] = useState('identity');
  const [k, setK] = useState(5);
  const [grid, setGrid] = useState<LensGrid | null>(null);
  const [parentGrid, setParentGrid] = useState<LensGrid | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<{ layer: number; position: number } | null>(null);
  const [pins, setPins] = useState<VocabPin[]>([]);
  const [tracks, setTracks] = useState<Record<number, LensTrack>>({});
  const [activeTrack, setActiveTrack] = useState<number | null>(null);
  const [swapAt, setSwapAt] = useState<number | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [findingNote, setFindingNote] = useState('');
  const [findingState, setFindingState] = useState<{ kind: 'ok' | 'error'; text: string } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const trace = useMemo(() => traces.find((t) => t.traceId === traceId) ?? null, [traces, traceId]);
  const model = trace?.modelName ?? '';

  const loadTraces = useCallback(() => {
    void listTraces()
      .then((res) => {
        if (!alive.current) return;
        setTraces(res.traces);
        setTraceId((current) =>
          current && res.traces.some((t) => t.traceId === current)
            ? current
            : (res.traces[0]?.traceId ?? ''),
        );
      })
      .catch(() => undefined);
  }, []);

  useEffect(loadTraces, [loadTraces]);

  useEffect(() => {
    setPins(model ? getVocabPins(model) : []);
    setTracks({});
    setActiveTrack(null);
  }, [model]);

  useEffect(() => {
    if (!traceId) return;
    void listLenses(traceId)
      .then((res) => {
        if (!alive.current) return;
        setLenses(res.lenses);
        setLensId((current) => (res.lenses.some((l) => l.id === current) ? current : 'identity'));
        if (!res.available) setError(res.reason);
      })
      .catch(() => undefined);
  }, [traceId]);

  const loadGrid = useCallback(() => {
    if (!traceId) {
      setGrid(null);
      return;
    }
    setBusy(true);
    setError('');
    void getLensGrid(traceId, { lens: lensId, k })
      .then((res) => {
        if (!alive.current) return;
        setGrid(res);
        setSelected(null);
      })
      .catch((err: unknown) => {
        if (!alive.current) return;
        setGrid(null);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => alive.current && setBusy(false));
  }, [traceId, lensId, k]);

  useEffect(loadGrid, [loadGrid]);

  // Follow a locus set from outside this pane — `dash.lens.focus(...)` or the
  // agent's `lens.focus`, so an explanation can point at the cell it is talking
  // about. Our own clicks are skipped by `source`: they already set the
  // selection, and re-applying it would fight the user on every click.
  const locus = useModelLocus();
  useEffect(() => {
    if (locus.source === 'lens') return;
    if (locus.traceId && locus.traceId !== traceId) {
      // Switch first and land the cell on the next pass: the grid reloads
      // asynchronously and clears the selection when it arrives, so selecting
      // now would be undone by the response.
      setTraceId(locus.traceId);
      return;
    }
    if (locus.layer === undefined || locus.position === undefined) return;
    setSelected({ layer: locus.layer, position: locus.position });
  }, [locus, traceId]);

  // The parent of a fork, so the diff has something to compare against. Loaded
  // only when the trace declares one — every other trace has no parent, and
  // asking for one would 404 on the common case.
  useEffect(() => {
    const parent = trace?.derivedFrom;
    if (!parent) {
      setParentGrid(null);
      return;
    }
    void getLensGrid(parent, { lens: lensId, k })
      .then((res) => alive.current && setParentGrid(res))
      .catch(() => alive.current && setParentGrid(null));
  }, [trace?.derivedFrom, lensId, k]);

  const diff = useMemo(
    () => (grid && parentGrid ? diffGrids(parentGrid, grid) : null),
    [grid, parentGrid],
  );

  /**
   * File this reading into the library, so it outlives the trace — traces are
   * pruned the moment the budget wants their bytes.
   *
   * A refusal comes back as a 200 with `error` set rather than a rejected
   * promise: "this grid is unverified" is an answer about the reading, and it
   * belongs beside the verify banner that already says so.
   */
  const save = useCallback(() => {
    if (!traceId) return;
    setSaving(true);
    setFindingState(null);
    void saveFinding(traceId, { note: findingNote, lens: lensId, k })
      .then((res) => {
        if (!alive.current) return;
        if (res.error) {
          setFindingState({ kind: 'error', text: res.error });
          return;
        }
        setFindingState({ kind: 'ok', text: `Saved to the ${res.library} library: ${res.title}` });
        setFindingNote('');
      })
      .catch((err: unknown) => {
        if (!alive.current) return;
        setFindingState({ kind: 'error', text: err instanceof Error ? err.message : String(err) });
      })
      .finally(() => alive.current && setSaving(false));
  }, [traceId, findingNote, lensId, k]);

  const pin = useCallback(
    (id: number, text: string) => {
      if (!model) return;
      setPins(addVocabPin(model, { id, text }));
      setActiveTrack(id);
      setModelLocus({ tokenId: id }, 'lens');
      if (!traceId) return;
      void getLensTrack(traceId, id, lensId)
        .then((res) => alive.current && setTracks((prev) => ({ ...prev, [id]: res })))
        .catch(() => undefined);
    },
    [model, traceId, lensId],
  );

  const unpin = useCallback(
    (id: number) => {
      if (!model) return;
      setPins(removeVocabPin(model, id));
      setActiveTrack((current) => (current === id ? null : current));
    },
    [model],
  );

  const swap = useCallback(
    (position: number, entry: VocabEntry) => {
      if (!traceId) return;
      setSwapAt(null);
      setBusy(true);
      setError('');
      let created = '';
      void forkTrace(traceId, [{ position, toId: entry.id }], (p) => {
        if (!alive.current) return;
        setProgress(p);
        if (p.error) setError(String(p.error));
        if (typeof p.traceId === 'string') created = p.traceId;
      })
        .then(() => {
          if (!alive.current) return;
          setProgress(null);
          loadTraces();
          // Land on the fork: the point of a swap is the grid it produced, and
          // leaving the parent selected would make a successful run look inert.
          if (created) setTraceId(created);
        })
        .catch((err: unknown) => {
          if (!alive.current) return;
          setError(err instanceof Error ? err.message : String(err));
        })
        .finally(() => alive.current && setBusy(false));
    },
    [traceId, loadTraces],
  );

  const track = activeTrack === null ? null : (tracks[activeTrack] ?? null);

  if (!traces.length) {
    return (
      <div className="llama-lens">
        <p className="llama-meta">
          No traces yet. Run one in the <strong>Traces</strong> section — the &ldquo;Lens
          only&rdquo; capture set is the cheap one, a few megabytes rather than a gigabyte, because
          the lens reads the residual stream and nothing else.
        </p>
      </div>
    );
  }

  return (
    <div className="llama-lens">
      <div className="llama-row">
        <label>
          Trace
          <select value={traceId} onChange={(e) => setTraceId(e.target.value)}>
            {traces.map((t) => (
              <option key={t.traceId} value={t.traceId}>
                {t.modelName} · {new Date((t.createdAt || 0) * 1000).toLocaleString()}
                {t.derivedFrom ? ' · fork' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>
          Lens
          <select value={lensId} onChange={(e) => setLensId(e.target.value)}>
            {lenses.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Top-k
          <input
            type="number"
            min={1}
            max={20}
            value={k}
            onChange={(e) => setK(Math.max(1, Math.min(20, Number(e.target.value) || 5)))}
          />
        </label>
        <button onClick={loadGrid} disabled={busy}>
          {busy ? 'Reading…' : 'Refresh'}
        </button>
      </div>

      {lenses.length === 1 ? <p className="llama-meta">{lenses[0]?.provenance}</p> : null}

      {progress ? (
        <p className="llama-meta">
          {String(progress.status ?? 'running')}
          {typeof progress.pass === 'number' ? ` · pass ${progress.pass}` : ''}
        </p>
      ) : null}
      {error ? <p className="llama-error">{error}</p> : null}

      {grid ? (
        <>
          <VerifyBanner grid={grid} />

          {trace?.derivedFrom ? (
            <p className="llama-meta">
              Forked from <code>{trace.derivedFrom}</code>
              {trace.edits?.length
                ? ` · replaced position ${trace.edits.map((e) => e.position).join(', ')}`
                : ''}
              {diff ? ' · changed cells are outlined' : ' · parent unavailable, no diff'}
            </p>
          ) : null}

          <TokenStrip grid={grid} swapping={swapAt} onSwap={setSwapAt} />
          {swapAt !== null && trace ? (
            <TokenPicker
              modelPath={trace.modelPath}
              label={`Replace position ${swapAt} and re-run`}
              onPick={(entry) => swap(swapAt, entry)}
              onCancel={() => setSwapAt(null)}
            />
          ) : null}

          {pins.length ? (
            <div className="llama-row llama-lens-pins">
              <span className="llama-meta">Tracking</span>
              {pins.map((p) => (
                <span key={p.id} className="llama-row">
                  <button
                    className={`llama-chip${activeTrack === p.id ? ' llama-chip-on' : ''}`}
                    onClick={() => {
                      const next = activeTrack === p.id ? null : p.id;
                      setActiveTrack(next);
                      if (next !== null && !tracks[next] && traceId)
                        void getLensTrack(traceId, next, lensId)
                          .then(
                            (res) =>
                              alive.current && setTracks((prev) => ({ ...prev, [next]: res })),
                          )
                          .catch(() => undefined);
                    }}
                  >
                    {displayToken(p.text, 14)}
                  </button>
                  <button className="llama-linkbtn llama-inline" onClick={() => unpin(p.id)}>
                    ×
                  </button>
                </span>
              ))}
              <button
                className="llama-linkbtn"
                onClick={() => {
                  setPins(clearVocabPins(model));
                  setActiveTrack(null);
                }}
              >
                clear
              </button>
            </div>
          ) : null}
          {pins.length >= MAX_VOCAB_PINS ? (
            <p className="llama-meta">
              {MAX_VOCAB_PINS} tracked tokens is the cap — each one is a full pass over the output
              head.
            </p>
          ) : null}

          <Grid
            grid={grid}
            track={track}
            diff={diff}
            selected={selected}
            onSelect={(layer, position) => {
              setSelected({ layer, position });
              // Publishing the click is what makes the model explorer reveal
              // this block's tensors. The grid does not know the explorer
              // exists — see `model-locus.ts`.
              setModelLocus({ modelSha: trace?.modelSha, traceId, layer, position }, 'lens');
            }}
          />

          {selected ? <CellDetail grid={grid} at={selected} onPin={pin} /> : null}

          <p className="llama-meta">
            {grid.unembedding.tensor}
            {grid.unembedding.tied ? ' (tied to the embedding table)' : ''} ·{' '}
            {grid.unembedding.quant} · {grid.unembedding.nVocab.toLocaleString()} tokens ·{' '}
            {grid.unembedding.architecture}
            {grid.unembedding.logitSoftcap
              ? ` · logits softcapped at ${grid.unembedding.logitSoftcap}`
              : ''}
          </p>

          <div className="llama-row llama-lens-save">
            <input
              type="text"
              value={findingNote}
              placeholder="What did you find? Saved with the reading…"
              onChange={(e) => setFindingNote(e.target.value)}
            />
            <button onClick={save} disabled={saving || grid.verified !== 'true'}>
              {saving ? 'Saving…' : 'Save to library'}
            </button>
          </div>
          {grid.verified !== 'true' ? (
            <p className="llama-meta">
              Only a verified reading can be filed — a caveat in a note does not survive the
              first search that quotes its numbers.
            </p>
          ) : null}
          {findingState ? (
            <p className={findingState.kind === 'error' ? 'llama-error' : 'llama-meta'}>
              {findingState.text}
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
