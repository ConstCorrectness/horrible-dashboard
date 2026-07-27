/**
 * The grid pane: a schema's rows as an editable table. Double-clicking a cell edits
 * it in place; clicking a row selects it, which is what moves the form and board.
 *
 * Two modes, and the distinction matters. Unpinned, the grid follows the shared
 * selection — one table, one open record, three views of it. Pinned via
 * `params.schemaId`, it is a standalone table with its own rows and its own
 * selection: that's how the CRM workspace shows the activity log under the record
 * form without the log stealing the workspace's active table.
 */
import { useCallback, useEffect, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import { usePaneParams } from '../../panes';
import {
  createRow,
  deleteRow,
  listRows,
  rowTitle,
  updateRow,
  type RecordRow,
  type RecordSchema,
} from './api';
import { FieldInput, formatValue, visibleFields } from './fields';
import {
  addRow,
  getActiveSchema,
  getError,
  getRows,
  getSchemas,
  getSelectedRowId,
  initRecordsWatch,
  onRowEvent,
  removeRow,
  saveRow,
  selectRow,
  useRecords,
} from './store';
import './records.css';

interface Cell {
  rowId: string;
  key: string;
}

/** A pinned grid's own copy of one schema's rows, independent of the shared store. */
function usePinnedSchema(schemaId: string | null) {
  const [rows, setRows] = useState<RecordRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!schemaId) return;
    try {
      setRows((await listRows(schemaId)).rows);
    } catch {
      /* backend down — the empty state stands until the next event */
    }
  }, [schemaId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Agent commits arrive on the ws channel; the shared store only tracks the
  // *active* schema, so a pinned grid listens for its own.
  useEffect(
    () =>
      onRowEvent((eventSchemaId, row) => {
        if (eventSchemaId !== schemaId) return;
        setRows((prev) =>
          prev.some((r) => r.id === row.id)
            ? prev.map((r) => (r.id === row.id ? row : r))
            : [row, ...prev],
        );
      }),
    [schemaId],
  );

  return { rows, selected, setSelected, setRows, reload };
}

export function RecordGrid() {
  useRecords();
  const params = usePaneParams();
  const [editing, setEditing] = useState<Cell | null>(null);
  const pinnedId = typeof params.schemaId === 'string' ? params.schemaId : null;
  const pinned = usePinnedSchema(pinnedId);

  useEffect(() => {
    initRecordsWatch();
  }, []);

  const schema: RecordSchema | null = pinnedId
    ? (getSchemas().find((s) => s.id === pinnedId) ?? null)
    : getActiveSchema();
  const rows = pinnedId ? pinned.rows : getRows();
  const selectedId = pinnedId ? pinned.selected : getSelectedRowId();
  const fields = schema ? visibleFields(schema.fields) : [];

  useAgentContext(() => ({
    pane: 'records.grid',
    schema: schema?.id ?? null,
    pinned: Boolean(pinnedId),
    fields: fields.map((f) => f.key),
    rowCount: rows.length,
    selectedRowId: selectedId,
    // A sample, not the table — records.query is the real read.
    rows: rows.slice(0, 10).map((r) => ({ id: r.id, title: schema ? rowTitle(schema, r) : r.id })),
  }));

  if (!schema) {
    return (
      <div className="rec-empty">
        {pinnedId
          ? `No table “${pinnedId}” yet.`
          : getSchemas().length === 0
            ? 'No record tables yet. Ask the agent to create one, or run “Records: Create the built-in tables”.'
            : 'Pick a table from the Tables rail.'}
      </div>
    );
  }

  const select = (rowId: string) => (pinnedId ? pinned.setSelected(rowId) : selectRow(rowId));

  const commit = async (row: RecordRow, key: string, value: unknown) => {
    setEditing(null);
    if (row[key] === value) return;
    if (!pinnedId) {
      void saveRow(row.id, { [key]: value });
      return;
    }
    const updated = await updateRow(schema.id, row.id, { [key]: value });
    pinned.setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
  };

  const add = async () => {
    if (!pinnedId) {
      void addRow();
      return;
    }
    const row = await createRow(schema.id, {});
    pinned.setRows((prev) => [row, ...prev]);
    pinned.setSelected(row.id);
  };

  const remove = async (rowId: string) => {
    if (!pinnedId) {
      void removeRow(rowId);
      return;
    }
    await deleteRow(schema.id, rowId);
    pinned.setRows((prev) => prev.filter((r) => r.id !== rowId));
  };

  return (
    <div className="rec-grid">
      <div className="rec-toolbar">
        <span className="rec-title">
          {schema.icon ?? '▤'} {schema.name}
        </span>
        <span className="rec-dim">{rows.length} rows</span>
        <button className="rec-btn rec-btn-primary" onClick={() => void add()}>
          + Row
        </button>
      </div>
      {!pinnedId && getError() && <div className="rec-error">{getError()}</div>}
      <div className="rec-grid-scroll">
        <table className="rec-table">
          <thead>
            <tr>
              {fields.map((f) => (
                <th key={f.key}>{f.label}</th>
              ))}
              <th className="rec-col-actions" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                className={row.id === selectedId ? 'rec-row rec-row-selected' : 'rec-row'}
                onClick={() => select(row.id)}
              >
                {fields.map((f) => (
                  <td key={f.key} onDoubleClick={() => setEditing({ rowId: row.id, key: f.key })}>
                    {editing?.rowId === row.id && editing.key === f.key ? (
                      <FieldInput
                        field={f}
                        value={row[f.key]}
                        autoFocus
                        onCommit={(value) => void commit(row, f.key, value)}
                      />
                    ) : (
                      <span className="rec-cell">{formatValue(f, row[f.key]) || '—'}</span>
                    )}
                  </td>
                ))}
                <td className="rec-col-actions">
                  <button
                    className="rec-btn rec-btn-ghost"
                    title="Delete row"
                    onClick={(e) => {
                      e.stopPropagation();
                      void remove(row.id);
                    }}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="rec-dim" colSpan={fields.length + 1}>
                  No rows yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="rec-hint">
        Double-click a cell to edit{pinnedId ? '' : ' · click a row to open it in the form'}
      </div>
    </div>
  );
}
