/**
 * A compact, local-session view of a notebook's execution provenance.
 *
 * The notebook kernel already publishes the dependency DAG; this component makes
 * it usable at the moment a result looks surprising: select a cell to see its
 * direct upstream inputs, its downstream consumers, and the latest executions.
 */
import { useMemo, useState } from 'react';

import type { DependencyEdge, ExecutionRecord } from './SessionStore';
import type { NotebookCell } from './types';

function shortCell(cells: readonly NotebookCell[], id: string): string {
  const index = cells.findIndex((cell) => cell.id === id);
  const firstLine = cells[index]?.source.split('\n', 1)[0]?.trim();
  return `Cell ${index >= 0 ? index + 1 : '?'}${firstLine ? ` — ${firstLine.slice(0, 44)}` : ''}`;
}

function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function ExecutionTimeline({
  cells,
  edges,
  staleCells,
  history,
  onFocusCell,
}: {
  cells: readonly NotebookCell[];
  edges: readonly DependencyEdge[];
  staleCells: readonly string[];
  history: readonly ExecutionRecord[];
  onFocusCell: (cellId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const stale = new Set(staleCells);
  const selectedCell =
    selected ?? history[0]?.cellId ?? cells.find((c) => stale.has(c.id))?.id ?? null;
  const provenance = useMemo(() => {
    if (!selectedCell) return { upstream: [], downstream: [] };
    return {
      upstream: edges.filter((edge) => edge.to === selectedCell).map((edge) => edge.from),
      downstream: edges.filter((edge) => edge.from === selectedCell).map((edge) => edge.to),
    };
  }, [edges, selectedCell]);

  if (cells.length === 0) return null;

  return (
    <section className="nb-timeline" aria-label="Execution timeline">
      <button
        type="button"
        className={`nb-timeline-toggle${expanded ? ' is-on' : ''}`}
        aria-expanded={expanded}
        onClick={() => setExpanded((open) => !open)}
      >
        Timeline
        {stale.size > 0 && <span className="nb-timeline-stale-count">{stale.size} stale</span>}
      </button>
      {expanded && (
        <div className="nb-timeline-body">
          <div className="nb-timeline-provenance">
            <strong>{selectedCell ? shortCell(cells, selectedCell) : 'No execution yet'}</strong>
            {selectedCell && (
              <button type="button" onClick={() => onFocusCell(selectedCell)}>
                Jump to cell
              </button>
            )}
            <p>
              {provenance.upstream.length
                ? `Runs after ${provenance.upstream.length} upstream cell${provenance.upstream.length === 1 ? '' : 's'}.`
                : 'No upstream notebook-cell dependency.'}
              {provenance.downstream.length
                ? ` Its values feed ${provenance.downstream.length} downstream cell${provenance.downstream.length === 1 ? '' : 's'}.`
                : ''}
            </p>
            {provenance.upstream.length > 0 && (
              <div className="nb-timeline-links">
                {provenance.upstream.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => {
                      setSelected(id);
                      onFocusCell(id);
                    }}
                  >
                    ↑ {shortCell(cells, id)}
                  </button>
                ))}
              </div>
            )}
            {provenance.downstream.length > 0 && (
              <div className="nb-timeline-links">
                {provenance.downstream.map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => {
                      setSelected(id);
                      onFocusCell(id);
                    }}
                  >
                    ↓ {shortCell(cells, id)}
                    {stale.has(id) ? ' · stale' : ''}
                  </button>
                ))}
              </div>
            )}
          </div>
          <ol className="nb-timeline-history">
            {history.length === 0 && <li>No completed executions in this session yet.</li>}
            {history.slice(0, 12).map((record, index) => (
              <li key={`${record.cellId}:${record.finishedAt}:${index}`}>
                <button
                  type="button"
                  onClick={() => {
                    setSelected(record.cellId);
                    onFocusCell(record.cellId);
                  }}
                >
                  <span className={record.state === 'error' ? 'is-error' : 'is-done'}>
                    {record.state}
                  </span>
                  {shortCell(cells, record.cellId)}
                  <time dateTime={new Date(record.finishedAt).toISOString()}>
                    {formatTime(record.finishedAt)}
                  </time>
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
