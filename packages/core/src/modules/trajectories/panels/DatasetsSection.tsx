/**
 * Where runs are collected, what they cost, and how they get in and out.
 *
 * The dataset selector is not decoration: `GET /stats` and `GET /tools` both take a
 * `dataset`, so scoping is done **server-side** rather than by filtering a page that
 * was already truncated. A client-side narrowing here would silently only ever match
 * the rows that happened to be loaded.
 *
 * Export deliberately says what it *skipped*. `export.py` writes only graded successes
 * — training on whatever the agent happened to do distils its failure modes — and a
 * report of "42 examples" with no mention of the 300 ungraded runs it passed over
 * would read as a complete dataset.
 */
import { useCallback, useEffect, useState } from 'react';

import { DataList, DataRow, RollingNumber } from '../../../DataList';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { ControlBar } from '../../../ResourceCard';
import {
  createDataset,
  exportSft,
  importReplay,
  importRuns,
  listDatasets,
  listTools,
  getStats,
  updateDataset,
  IMPORT_FORMATS,
  type Dataset,
  type ImportFormat,
  type Stats,
  type ToolStat,
} from '../api';
import { DatabaseIcon, RecordIcon, RefreshIcon } from '../icons';
import { bodyScroll, card, Figure, heading, Loading, mono, ms, SectionShell } from './common';

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ ...card, minWidth: 120 }}>
      <div style={heading}>{label}</div>
      <div
        style={{
          fontSize: 'var(--fs-display)',
          fontFamily: 'var(--font-mono)',
          marginTop: 'var(--space-2)',
        }}
      >
        <RollingNumber value={value} />
      </div>
    </div>
  );
}

function ToolTable({ tools }: { tools: ToolStat[] }) {
  if (tools.length === 0) {
    return (
      <EmptyState title="No tool calls recorded yet">
        Runs with tool calls will list their tools here.
      </EmptyState>
    );
  }
  return (
    <div>
      {tools.map((tool) => (
        <div
          key={tool.name}
          style={{
            display: 'flex',
            gap: 'var(--space-5)',
            padding: 'var(--space-2) 0',
            borderBottom: '1px solid var(--border)',
            ...mono,
          }}
        >
          <span style={{ flex: 1, color: 'var(--text-primary)' }}>{tool.name}</span>
          <Figure width={80}>{tool.calls} calls</Figure>
          <Figure width={80} tone={tool.failures ? 'fail' : undefined}>
            {tool.failures} failed
          </Figure>
          <Figure width={80} tone={tool.gated ? 'warn' : undefined}>
            {tool.gated} gated
          </Figure>
          <Figure width={60}>{ms(tool.avgMs)}</Figure>
        </div>
      ))}
    </div>
  );
}

export function DatasetsSection() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [tools, setTools] = useState<ToolStat[]>([]);
  const [scope, setScope] = useState('');
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [importing, setImporting] = useState(false);
  const [format, setFormat] = useState<ImportFormat>('claude-code');
  const [payload, setPayload] = useState('');
  const [replayId, setReplayId] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [d, s, t] = await Promise.all([
        listDatasets(),
        getStats(scope || undefined),
        listTools({ dataset: scope || undefined }),
      ]);
      setDatasets(d);
      setStats(s);
      setTools(t);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [scope]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const id = trimmed
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 60);
    try {
      await createDataset({ id, name: trimmed });
      setName('');
      void refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const toggleCapture = async (dataset: Dataset) => {
    await updateDataset(dataset.id, { capture: !dataset.capture });
    void refresh();
  };

  const doExport = async () => {
    setNotice('Exporting…');
    try {
      const report = await exportSft({
        name: scope || 'trajectories',
        dataset: scope || null,
        // `human` only: an `agent-critic` label is a model grading a model, and a
        // training set built on those is a model learning its own opinions.
        label_source: 'human',
      });
      setNotice(
        `Wrote ${report.examples} example${report.examples === 1 ? '' : 's'} to ${report.path}` +
          (report.skippedCount
            ? ` — skipped ${report.skippedCount} of ${report.candidates} candidates (ungraded or not a success).`
            : '.'),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setNotice('');
    }
  };

  const doImport = async () => {
    const dataset = scope || datasets[0]?.id;
    if (!dataset) {
      setError('Create a dataset first — an import needs somewhere to land.');
      return;
    }
    setNotice('Importing…');
    try {
      const report = await importRuns({ dataset_id: dataset, format, content: payload });
      setNotice(
        `Imported ${report.created} new run${report.created === 1 ? '' : 's'} into ${dataset}` +
          (report.merged ? `, merged ${report.merged} that already existed.` : '.'),
      );
      setPayload('');
      void refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setNotice('');
    }
  };

  const doReplay = async () => {
    if (!replayId.trim()) return;
    setNotice('Fetching replay…');
    try {
      const report = await importReplay(replayId.trim(), scope || 'games');
      setNotice(`Imported ${report.created} run${report.created === 1 ? '' : 's'} — one per seat.`);
      setReplayId('');
      void refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setNotice('');
    }
  };

  const header = (
    <PaneHeader
      title="Datasets"
      meta={stats ? [`${stats.runs} runs in scope`] : ['measuring…']}
      actions={
        <>
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            aria-label="Scope stats to a dataset"
          >
            <option value="">all datasets</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            onClick={() => void doExport()}
            title="Write graded successes as SFT JSONL"
          >
            Export
          </Button>
          <Button intent="ghost" size="sm" onClick={() => setImporting((on) => !on)}>
            Import
          </Button>
          <Button size="sm" icon={<RefreshIcon />} onClick={() => void refresh()}>
            Refresh
          </Button>
        </>
      }
    />
  );

  return (
    <SectionShell header={header} error={error}>
      {notice ? (
        <div
          style={{
            padding: 'var(--space-3) var(--space-5)',
            fontSize: 'var(--fs-meta)',
            color: 'var(--text-dim)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          {notice}
        </div>
      ) : null}
      <div style={{ ...bodyScroll, padding: 'var(--space-5)' }}>
        {loading ? (
          <Loading what="datasets" />
        ) : (
          <>
            {stats ? (
              <div
                style={{
                  display: 'flex',
                  gap: 'var(--space-5)',
                  marginBottom: 'var(--space-6)',
                  flexWrap: 'wrap',
                }}
              >
                <StatTile label="Runs" value={stats.runs} />
                <StatTile label="Success" value={stats.outcomes.success ?? 0} />
                <StatTile label="Failure" value={stats.outcomes.failure ?? 0} />
                <StatTile label="Ungraded" value={stats.outcomes.ungraded ?? 0} />
              </div>
            ) : null}

            {importing ? (
              <div style={{ ...card, marginBottom: 'var(--space-6)' }}>
                <div style={heading}>Import runs</div>
                <p
                  style={{
                    fontSize: 'var(--fs-meta)',
                    color: 'var(--text-dim)',
                    margin: 'var(--space-2) 0 var(--space-4)',
                  }}
                >
                  Paste a transcript, or pull a games replay in as one run per seat. Runs land in{' '}
                  <strong>{scope || datasets[0]?.id || 'no dataset'}</strong>; an import that
                  matches an existing external id updates it rather than duplicating it.
                </p>
                <ControlBar>
                  <textarea
                    rows={5}
                    placeholder="transcript JSON"
                    value={payload}
                    onChange={(e) => setPayload(e.target.value)}
                    aria-label="Transcript to import"
                  />
                  <select
                    value={format}
                    onChange={(e) => setFormat(e.target.value as ImportFormat)}
                    aria-label="Transcript format"
                  >
                    {IMPORT_FORMATS.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                  <Button size="sm" disabled={!payload.trim()} onClick={() => void doImport()}>
                    Import
                  </Button>
                </ControlBar>
                <div style={{ marginTop: 'var(--space-4)' }}>
                  <ControlBar>
                    <input
                      placeholder="games replay id"
                      value={replayId}
                      onChange={(e) => setReplayId(e.target.value)}
                      aria-label="Games replay id"
                    />
                    <Button size="sm" disabled={!replayId.trim()} onClick={() => void doReplay()}>
                      Import replay
                    </Button>
                  </ControlBar>
                </div>
              </div>
            ) : null}

            <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>Collections</div>
            <div style={{ marginBottom: 'var(--space-4)' }}>
              <ControlBar>
                <input
                  placeholder="new dataset name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void add()}
                  aria-label="New dataset name"
                />
                <Button size="sm" icon={<DatabaseIcon />} onClick={() => void add()}>
                  Create
                </Button>
              </ControlBar>
            </div>
            {datasets.length === 0 ? (
              <EmptyState title="No datasets">
                Create one, then switch capture on — nothing is recorded until you do.
              </EmptyState>
            ) : (
              <DataList label="Datasets">
                {datasets.map((dataset, index) => (
                  <DataRow
                    key={dataset.id}
                    index={index}
                    kind={dataset.capture ? 'fail' : 'idle'}
                    hideMark
                    title={dataset.name}
                    badge={
                      dataset.capture ? (
                        <Chip kind="fail" dot>
                          recording
                        </Chip>
                      ) : undefined
                    }
                    meta={[dataset.id, `${dataset.run_count} runs`, dataset.source_kind]}
                    actions={
                      <Button
                        size="sm"
                        icon={<RecordIcon />}
                        onClick={() => void toggleCapture(dataset)}
                      >
                        {dataset.capture ? 'Stop capture' : 'Capture here'}
                      </Button>
                    }
                  />
                ))}
              </DataList>
            )}

            <div style={{ ...heading, margin: 'var(--space-6) 0 var(--space-3)' }}>
              Tool usage{scope ? ` — ${scope}` : ''}
            </div>
            <ToolTable tools={tools} />
          </>
        )}
      </div>
    </SectionShell>
  );
}
