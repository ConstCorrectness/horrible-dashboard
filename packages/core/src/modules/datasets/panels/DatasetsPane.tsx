import { useCallback, useEffect, useMemo, useState } from 'react';

import { Button, EmptyState, PaneHeader } from '../../../Primitives';
import { useStaggeredEntrance } from '../../../viz';
import {
  deleteDataset,
  listDatasets,
  listSources,
  listSplits,
  peek,
  registerDataset,
  searchDatasets,
  tokenStats,
  updateDataset,
  type Dataset,
  type DatasetRef,
  type DatasetSource,
  type Peek,
  type Split,
  type TokenStats,
} from '../api';
import { FormatVerdict } from './FormatVerdict';
import { TokenHistogram } from './TokenHistogram';

/**
 * The datasets browser: find training material, look at it, and register it.
 *
 * Registering is the point. A recipe used to hold a dataset *name* — a string
 * nobody had looked at, in a shape nobody had checked. A registered dataset
 * carries the source, the split, the detected shape and the column map, which is
 * the difference between "this run used Capybara" and a rerun that provably eats
 * the same rows the same way.
 *
 * The pane is three sections because they are three different questions:
 * **Sources** (what is out there), **Library** (what this node has committed to),
 * and **Inspect** (is this one actually right). Inspect is the one that earns the
 * pane: real rows, the format verdict with its evidence, and the fraction of
 * examples a sequence length would silently truncate.
 */

const dim = { color: 'var(--text-dim)' } as const;
const mono = { fontFamily: 'var(--font-mono, monospace)' } as const;

const card: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-lg)',
  padding: '0.55rem 0.65rem',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.4rem',
};

const heading: React.CSSProperties = {
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  fontSize: '0.68rem',
  color: 'var(--text-secondary)',
};

interface Target {
  source: string;
  ref: string;
  config: string;
  split: string;
  /** Set when this target came from the library rather than a search hit. */
  datasetId?: string;
  title?: string;
}

export function DatasetsPane() {
  const [sources, setSources] = useState<DatasetSource[]>([]);
  const [source, setSource] = useState('hub');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<DatasetRef[]>([]);
  const [library, setLibrary] = useState<Dataset[]>([]);
  const [target, setTarget] = useState<Target | null>(null);
  const [splits, setSplits] = useState<Split[]>([]);
  const [sample, setSample] = useState<Peek | null>(null);
  const [stats, setStats] = useState<TokenStats | null>(null);
  const [maxLength, setMaxLength] = useState(1024);
  const [model, setModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const refreshLibrary = useCallback(() => {
    listDatasets()
      .then(setLibrary)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    listSources()
      .then(setSources)
      .catch((e: Error) => setError(e.message));
    refreshLibrary();
  }, [refreshLibrary]);

  const runSearch = useCallback(() => {
    setBusy(true);
    setError('');
    searchDatasets(query, source, 25)
      .then(setHits)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }, [query, source]);

  // A local source lists its files with no query, so an empty search is useful
  // there and useless on the Hub. Running it on source change rather than making
  // the user press a button for an answer that needs no input.
  useEffect(() => {
    const local = sources.find((s) => s.id === source)?.local;
    if (local) runSearch();
    else setHits([]);
  }, [source, sources, runSearch]);

  const inspect = useCallback((next: Target) => {
    setTarget(next);
    setSample(null);
    setStats(null);
    setError('');
    setBusy(true);
    listSplits(next.ref, next.source)
      .then(setSplits)
      .catch(() => setSplits([]));
    peek({ source: next.source, ref: next.ref, config: next.config, split: next.split, limit: 5 })
      .then(setSample)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }, []);

  const measure = useCallback(() => {
    if (!target) return;
    setBusy(true);
    tokenStats({
      dataset_id: target.datasetId,
      source: target.source,
      ref: target.ref,
      config: target.config,
      split: target.split,
      model,
      max_length: maxLength,
      limit: 200,
    })
      .then(setStats)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }, [target, model, maxLength]);

  const register = useCallback(() => {
    if (!target || !sample) return;
    setBusy(true);
    registerDataset({
      name: target.title || target.ref,
      source: target.source,
      ref: target.ref,
      config: sample.config || target.config,
      split: sample.split || target.split,
      format: sample.detection.format,
      column_map: sample.detection.columns,
    })
      .then((saved) => {
        setTarget({ ...target, datasetId: saved.id });
        refreshLibrary();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }, [target, sample, refreshLibrary]);

  const overrideFormat = useCallback(
    (format: string) => {
      if (!target?.datasetId) return;
      updateDataset(target.datasetId, { format })
        .then(() => refreshLibrary())
        .catch((e: Error) => setError(e.message));
    },
    [target, refreshLibrary],
  );

  const entrance = useStaggeredEntrance();
  const registered = useMemo(
    () => (target?.datasetId ? library.find((d) => d.id === target.datasetId) : undefined),
    [library, target],
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <PaneHeader
        title="Datasets"
        meta={[
          <span key="n" style={{ ...dim, ...mono }}>
            {library.length} registered
          </span>,
        ]}
      />

      {error && (
        <div style={{ padding: '0.4rem 0.7rem', color: 'var(--danger)', fontSize: '0.78rem' }}>
          {error}
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(240px, 1fr) minmax(320px, 1.4fr)',
          gap: '0.7rem',
          padding: '0.6rem 0.7rem',
          overflow: 'auto',
          minHeight: 0,
          flex: 1,
        }}
      >
        {/* --- left: sources + library ------------------------------------ */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.7rem', minWidth: 0 }}>
          <section style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            <div style={heading}>Sources</div>
            <div style={{ display: 'flex', gap: '0.35rem' }}>
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                style={{ padding: '0 0.6rem', minWidth: 120 }}
              >
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={query}
                placeholder="search…"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') runSearch();
                }}
                style={{ padding: '0 0.6rem', flex: 1, minWidth: 0 }}
              />
              <Button onClick={runSearch} disabled={busy}>
                Find
              </Button>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
              {hits.map((hit, i) => (
                <button
                  key={`${hit.source}:${hit.id}`}
                  className="hd-row"
                  onClick={() =>
                    inspect({
                      source: hit.source,
                      ref: hit.id,
                      config: '',
                      split: 'train',
                      title: hit.title,
                    })
                  }
                  {...entrance(i, {
                    ...card,
                    textAlign: 'left',
                    cursor: 'pointer',
                    background: 'transparent',
                  })}
                >
                  <span style={{ ...mono, fontSize: '0.78rem', wordBreak: 'break-all' }}>
                    {hit.id}
                  </span>
                  {hit.description && (
                    <span style={{ ...dim, fontSize: '0.72rem' }}>{hit.description}</span>
                  )}
                </button>
              ))}
              {!hits.length && !busy && (
                <span style={{ ...dim, fontSize: '0.75rem' }}>
                  {sources.find((s) => s.id === source)?.local
                    ? 'Nothing in this directory yet.'
                    : 'Search the Hub, or switch to Exports to train on what your evals caught.'}
                </span>
              )}
            </div>
          </section>

          <section style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
            <div style={heading}>Library</div>
            {library.length === 0 ? (
              <EmptyState title="No datasets registered">
                Inspect one on the right and register it — a registered dataset carries its shape,
                so a recipe stops guessing at a text column.
              </EmptyState>
            ) : (
              library.map((d) => (
                <div key={d.id} style={card}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.4rem' }}>
                    <button
                      className="hd-row"
                      onClick={() =>
                        inspect({
                          source: d.source,
                          ref: d.ref,
                          config: d.config,
                          split: d.split,
                          datasetId: d.id,
                          title: d.name,
                        })
                      }
                      style={{
                        background: 'transparent',
                        border: 0,
                        padding: 0,
                        cursor: 'pointer',
                        color: 'inherit',
                        fontSize: '0.8rem',
                        textAlign: 'left',
                        flex: 1,
                        minWidth: 0,
                      }}
                    >
                      {d.name}
                    </button>
                    <Button
                      intent="ghost"
                      onClick={() => {
                        deleteDataset(d.id)
                          .then(refreshLibrary)
                          .catch(() => undefined);
                      }}
                    >
                      Remove
                    </Button>
                  </div>
                  <div style={{ ...dim, ...mono, fontSize: '0.68rem' }}>
                    {d.source} · {d.split} · {d.format} · {d.fingerprint}
                  </div>
                </div>
              ))
            )}
          </section>
        </div>

        {/* --- right: inspect --------------------------------------------- */}
        <section style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', minWidth: 0 }}>
          <div style={heading}>Inspect</div>
          {!target ? (
            <EmptyState title="Nothing selected">
              Pick a dataset on the left to see its real columns and rows, what shape it is in, and
              how many examples a sequence length would truncate.
            </EmptyState>
          ) : (
            <>
              <div style={card}>
                <div style={{ ...mono, fontSize: '0.8rem', wordBreak: 'break-all' }}>
                  {target.ref}
                </div>
                <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                  <select
                    value={`${target.config}|${target.split}`}
                    onChange={(e) => {
                      const [config, split] = e.target.value.split('|');
                      inspect({ ...target, config, split });
                    }}
                    style={{ padding: '0 0.6rem' }}
                  >
                    {(splits.length
                      ? splits
                      : [{ config: target.config, split: target.split }]
                    ).map((s) => (
                      <option key={`${s.config}|${s.split}`} value={`${s.config}|${s.split}`}>
                        {s.config ? `${s.config} / ${s.split}` : s.split}
                      </option>
                    ))}
                  </select>
                  <Button
                    intent={target.datasetId ? 'ghost' : 'primary'}
                    onClick={register}
                    disabled={busy || !sample}
                  >
                    {target.datasetId ? 'Re-register' : 'Register'}
                  </Button>
                </div>
                {registered && (
                  <div style={{ ...dim, ...mono, fontSize: '0.68rem' }}>
                    registered · {registered.fingerprint}
                  </div>
                )}
              </div>

              {sample && (
                <div style={card}>
                  <FormatVerdict detection={sample.detection} />
                  {target.datasetId && (
                    <label
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.4rem',
                        fontSize: '0.72rem',
                        ...dim,
                      }}
                    >
                      Override
                      <select
                        value={registered?.format ?? sample.detection.format}
                        onChange={(e) => overrideFormat(e.target.value)}
                        style={{ padding: '0 0.6rem' }}
                      >
                        {['chatml', 'sharegpt', 'alpaca', 'preference', 'raw_text', 'unknown'].map(
                          (f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                  )}
                </div>
              )}

              <div style={card}>
                <div
                  style={{
                    display: 'flex',
                    gap: '0.35rem',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                  }}
                >
                  <input
                    type="text"
                    value={model}
                    placeholder="base model (for exact token counts)"
                    onChange={(e) => setModel(e.target.value)}
                    style={{ padding: '0 0.6rem', flex: 1, minWidth: 140 }}
                  />
                  <input
                    type="number"
                    value={maxLength}
                    onChange={(e) => setMaxLength(Number(e.target.value) || 1024)}
                    style={{ padding: '0 0.6rem', width: 90 }}
                  />
                  <Button onClick={measure} disabled={busy}>
                    Measure
                  </Button>
                </div>
                {stats ? (
                  <TokenHistogram stats={stats} maxLength={maxLength} />
                ) : (
                  <span style={{ ...dim, fontSize: '0.75rem' }}>
                    Measure to see how many examples this sequence length would truncate. Truncation
                    during training is silent.
                  </span>
                )}
              </div>

              {sample && sample.rows.length > 0 && (
                <div style={{ ...card, overflow: 'hidden' }}>
                  <div style={heading}>Rows</div>
                  <div style={{ overflowX: 'auto' }}>
                    <table
                      style={{
                        borderCollapse: 'collapse',
                        fontSize: '0.72rem',
                        ...mono,
                        minWidth: '100%',
                      }}
                    >
                      <thead>
                        <tr>
                          {sample.columns.map((c) => (
                            <th
                              key={c}
                              style={{
                                textAlign: 'left',
                                padding: '0.25rem 0.5rem',
                                borderBottom: '1px solid var(--border)',
                                color: 'var(--text-secondary)',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {sample.rows.map((row, i) => (
                          <tr key={i}>
                            {sample.columns.map((c) => (
                              <td
                                key={c}
                                style={{
                                  padding: '0.25rem 0.5rem',
                                  borderBottom: '1px solid var(--border)',
                                  maxWidth: 260,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  ...dim,
                                }}
                                title={JSON.stringify(row[c])}
                              >
                                {typeof row[c] === 'string'
                                  ? (row[c] as string)
                                  : JSON.stringify(row[c])}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
