/**
 * The runs index and the run being read.
 *
 * `SplitPane` rather than a fixed 340px column: the old fixed width did not survive a
 * 320px dock, where the detail column simply had nowhere left to be.
 *
 * Two search modes, and **which one answered is on screen**. `POST /search` degrades
 * on its own — to substring when no embedder answers, to recent when the query is
 * empty — and a silent fall back to "recent" looks exactly like a semantic search that
 * found nothing relevant. Saying `substring` instead is the difference between "your
 * index is not built" and "your query was bad".
 */
import { useCallback, useEffect, useState } from 'react';

import { DataList, DataRow } from '../../../DataList';
import { IconSearch } from '../../../glyphs';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { ControlBar } from '../../../ResourceCard';
import { SplitPane } from '../../../SplitPane';
import {
  deleteRun,
  getRun,
  listRuns,
  reindex,
  searchRuns,
  type SearchMethod,
  type TrajectoryDetail,
  type TrajectoryRun,
} from '../api';
import { RefreshIcon } from '../icons';
import { ago, bodyScroll, Loading, outcomeKind, SectionShell } from './common';
import { RunDetail } from './RunDetail';

/** What the backend says about how it answered, in words a reader can act on. */
const METHOD_NOTE: Record<SearchMethod, { kind: 'ok' | 'warn' | 'info'; text: string }> = {
  semantic: { kind: 'ok', text: 'semantic' },
  substring: { kind: 'warn', text: 'substring — no embedder answered' },
  recent: { kind: 'info', text: 'most recent — the query was empty' },
};

export function RunsSection({
  onInspectHarness,
}: {
  onInspectHarness: (fingerprint: string) => void;
}) {
  const [runs, setRuns] = useState<TrajectoryRun[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TrajectoryDetail | null>(null);
  const [outcome, setOutcome] = useState('');
  const [query, setQuery] = useState('');
  const [semantic, setSemantic] = useState(false);
  const [method, setMethod] = useState<SearchMethod | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      if (semantic) {
        // `outcome: null` rather than the backend's default of successes-only. That
        // default is right for retrieval feeding examples to a model — a failure
        // retrieved as a worked example teaches the failure — but this is a browser,
        // and silently hiding every failed run from a search is the opposite of what
        // somebody reading trajectories is looking for.
        const data = await searchRuns({ query, outcome: outcome || null, limit: 50 });
        setRuns(data.runs);
        setTotal(data.runs.length);
        setMethod(data.method);
      } else {
        const data = await listRuns({ outcome, q: query, limit: 100 });
        setRuns(data.runs);
        setTotal(data.total);
        setMethod(null);
      }
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [outcome, query, semantic]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const load = useCallback(async (id: string) => {
    setSelectedId(id);
    try {
      setDetail(await getRun(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const remove = async (id: string) => {
    await deleteRun(id);
    if (selectedId === id) {
      setSelectedId(null);
      setDetail(null);
    }
    void refresh();
  };

  const rebuild = async () => {
    setNotice('Reindexing…');
    try {
      const report = await reindex();
      const n = report.indexed ?? 0;
      setNotice(`Indexed ${n} run${n === 1 ? '' : 's'}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setNotice('');
    }
  };

  const header = (
    <PaneHeader
      title="Runs"
      meta={[
        `${total} run${total === 1 ? '' : 's'}`,
        ...(method
          ? [
              <Chip key="m" kind={METHOD_NOTE[method].kind}>
                {METHOD_NOTE[method].text}
              </Chip>,
            ]
          : []),
      ]}
      actions={
        <Button size="sm" icon={<RefreshIcon />} onClick={() => void refresh()}>
          Refresh
        </Button>
      }
    />
  );

  return (
    <SectionShell header={header} error={error}>
      <div style={{ padding: 'var(--space-3) var(--space-5)' }}>
        <ControlBar>
          <input
            type="search"
            placeholder={semantic ? 'describe the run you are looking for' : 'search goals'}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search runs"
          />
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            aria-label="Filter by outcome"
          >
            <option value="">any outcome</option>
            <option value="failure">failure</option>
            <option value="success">success</option>
            <option value="partial">partial</option>
          </select>
          <Button
            intent={semantic ? 'primary' : 'ghost'}
            size="sm"
            icon={<IconSearch />}
            title="Match by meaning rather than by substring"
            onClick={() => setSemantic((on) => !on)}
          >
            Semantic
          </Button>
          {semantic ? (
            <Button
              intent="ghost"
              size="sm"
              title="Build the vector index semantic search reads"
              onClick={() => void rebuild()}
            >
              Reindex
            </Button>
          ) : null}
        </ControlBar>
        {notice ? (
          <div
            style={{
              marginTop: 'var(--space-2)',
              fontSize: 'var(--fs-meta)',
              color: 'var(--text-dim)',
            }}
          >
            {notice}
          </div>
        ) : null}
      </div>
      <div style={bodyScroll}>
        <SplitPane
          id="trajectories.runs"
          initial={320}
          min={240}
          minOther={360}
          narrowBelow={720}
          label="Run list width"
        >
          <div style={{ height: '100%', overflow: 'auto' }}>
            {loading ? (
              <Loading what="runs" />
            ) : runs.length === 0 ? (
              <EmptyState title="No trajectories yet">
                Capture is off by default — turn it on for a dataset in the Datasets section, or
                push runs in with the Python SDK.
              </EmptyState>
            ) : (
              <DataList label="Runs">
                {runs.map((run, index) => (
                  <DataRow
                    key={run.id}
                    index={index}
                    kind={outcomeKind(run.outcome)}
                    selected={selectedId === run.id}
                    onClick={() => void load(run.id)}
                    title={run.goal || '(no goal)'}
                    // Two figures. The column's floor is 240px; beyond that the
                    // goal truncates rather than the figures, which is the right
                    // way round — an ellipsised goal is still scannable, a
                    // half-drawn number is not. `source` and duration are in the
                    // detail.
                    meta={[`${run.steps} steps`, ago(run.started_at)]}
                  >
                    {/* The one state the marks cannot name. `ok`/`fail`/`warn`
                        read as pass/fail/partial on sight, but `idle` reads only
                        as "no verdict" — and ungraded is precisely the row a
                        reader is here to act on. It goes in the body rather than
                        the badge slot because a badge shares the head line with
                        the meta, and at 240px the two land on top of each other. */}
                    {run.outcome && run.outcome !== 'unknown' ? null : 'Not graded yet.'}
                  </DataRow>
                ))}
              </DataList>
            )}
          </div>
          <div style={{ height: '100%', overflow: 'auto', padding: 'var(--space-5)' }}>
            {detail ? (
              <RunDetail
                run={detail}
                onChanged={() => void load(detail.id)}
                onDelete={() => void remove(detail.id)}
                onInspectHarness={onInspectHarness}
              />
            ) : (
              <EmptyState title="No run selected">
                Pick one on the left to walk its steps.
              </EmptyState>
            )}
          </div>
        </SplitPane>
      </div>
    </SectionShell>
  );
}
