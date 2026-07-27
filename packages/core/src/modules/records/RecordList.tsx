/**
 * The records rail tool: pick a table, search it, and see what's awaiting review.
 * The pending-proposal count belongs here rather than only in the form, because an
 * agent can file a proposal against a record the user isn't currently looking at.
 */
import { useEffect } from 'react';

import { useAgentContext } from '../../agent-context';
import { rowTitle } from './api';
import {
  getActiveSchema,
  getProposals,
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
  const pending = getProposals().length;

  useAgentContext(() => ({
    pane: 'records.list',
    schemas: schemas.map((s) => ({ id: s.id, name: s.name, rows: s.count ?? null })),
    activeSchema: active?.id ?? null,
    pendingProposals: pending,
  }));

  return (
    <div className="rec-list">
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
            <span className="rec-dim">{schema.count ?? ''}</span>
          </button>
        ))}
        {schemas.length === 0 && (
          <div className="rec-dim rec-pad">
            No tables yet — open the CRM or Data Entry workspace, or ask the agent to create one.
          </div>
        )}
      </div>

      {pending > 0 && (
        <div className="rec-pending">
          {pending} proposal{pending === 1 ? '' : 's'} awaiting review
        </div>
      )}

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
