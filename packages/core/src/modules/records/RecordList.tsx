/**
 * The records rail tool: pick a table, search it, define a new one, and see what is
 * awaiting review.
 *
 * The pending-review banner counts **every** table, not the selected one. That is
 * the whole point of it: an agent files against whatever table it was asked about,
 * not whichever one you happen to have open, so a queue you can only find by first
 * guessing the right table is one you would never find. Clicking the banner jumps
 * to the table holding the oldest proposal.
 */
import { useEffect } from 'react';

import { useAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { rowTitle } from './api';
import {
  getActiveSchema,
  getAllProposals,
  getPendingBySchema,
  getRows,
  getSchemas,
  getSearch,
  getSelectedRowId,
  initRecordsWatch,
  selectRow,
  setActiveSchema,
  setSearch,
  useRecords,
} from './store';
import './records.css';

export function RecordList() {
  useRecords();

  useEffect(() => {
    initRecordsWatch();
  }, []);

  const schemas = getSchemas();
  const active = getActiveSchema();
  const rows = getRows();
  const selectedId = getSelectedRowId();
  const pending = getAllProposals();
  const pendingBySchema = getPendingBySchema();

  useAgentContext(() => ({
    pane: 'records.list',
    schemas: schemas.map((s) => ({ id: s.id, name: s.name, rows: s.count ?? null })),
    activeSchema: active?.id ?? null,
    pendingProposals: pending.length,
  }));

  /** Jump to the table holding the oldest pending proposal and open the review. */
  const goToReview = () => {
    const oldest = pending[pending.length - 1];
    if (!oldest) return;
    if (oldest.schema_id !== active?.id) setActiveSchema(oldest.schema_id);
    if (oldest.record_id) selectRow(oldest.record_id);
    registry.openPanel('records.form');
  };

  const newTable = () => registry.openPanel('records.schema', { params: { schemaId: 'new' } });

  return (
    <div className="rec-list">
      {pending.length > 0 && (
        <button className="rec-pending" onClick={goToReview}>
          {pending.length} field set{pending.length === 1 ? '' : 's'} awaiting review →
        </button>
      )}

      <div className="rec-list-schemas">
        {schemas.map((schema) => (
          <button
            key={schema.id}
            className={schema.id === active?.id ? 'rec-schema rec-schema-active' : 'rec-schema'}
            onClick={() => setActiveSchema(schema.id)}
          >
            <span>
              {schema.icon ?? '▤'} {schema.name}
            </span>
            <span className="rec-schema-meta">
              {pendingBySchema[schema.id] ? (
                <span className="rec-dot" title={`${pendingBySchema[schema.id]} awaiting review`}>
                  ●
                </span>
              ) : null}
              <span className="rec-dim">{schema.count ?? ''}</span>
            </span>
          </button>
        ))}

        {schemas.length === 0 && (
          <div className="rec-dim rec-pad">
            No tables yet. A table is just columns you name — papers to read, job applications,
            anything row-shaped. The agent can then fill one in for you.
          </div>
        )}
      </div>

      <div className="rec-list-tools">
        <button className="rec-btn rec-btn-wide" onClick={newTable}>
          + New table
        </button>
        {active && (
          <button
            className="rec-btn rec-btn-quiet"
            title={`Edit the fields of ${active.name}`}
            onClick={() =>
              registry.openPanel('records.schema', { params: { schemaId: active.id } })
            }
          >
            ⚙
          </button>
        )}
      </div>

      {active && (
        <>
          <input
            className="rec-input rec-search"
            placeholder={`Search ${active.name}…`}
            value={getSearch()}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="rec-list-rows">
            {rows.map((row) => (
              <button
                key={row.id}
                className={row.id === selectedId ? 'rec-item rec-item-active' : 'rec-item'}
                onClick={() => selectRow(row.id)}
              >
                {rowTitle(active, row)}
              </button>
            ))}
            {rows.length === 0 && <div className="rec-dim rec-pad">No rows.</div>}
          </div>
        </>
      )}
    </div>
  );
}
