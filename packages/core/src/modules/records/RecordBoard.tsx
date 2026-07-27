/**
 * The board pane: a schema's rows as kanban cards, grouped by its `board_column`
 * (a select field). Dragging a card writes that field — which is why the board is
 * a view of the same rows rather than its own thing: moving a deal to "Won" is a
 * one-field update the grid and the form see immediately.
 */
import { useEffect, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import { usePaneParams } from '../../panes';
import { rowTitle, type RecordRow } from './api';
import { rowSummary } from './fields';
import {
  addRow,
  getActiveSchema,
  getRows,
  getSelectedRowId,
  initRecordsWatch,
  saveRow,
  selectRow,
  useRecords,
} from './store';
import './records.css';

/** Rows with no value for the group field still have to go somewhere. */
const UNSET = '—';

export function RecordBoard() {
  useRecords();
  const params = usePaneParams();
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);

  useEffect(() => {
    initRecordsWatch();
  }, []);

  const schema = getActiveSchema();
  const rows = getRows();
  const selectedId = getSelectedRowId();

  const groupKey =
    (typeof params.groupBy === 'string' ? params.groupBy : null) ?? schema?.board_column ?? null;
  const groupField = schema?.fields.find((f) => f.key === groupKey) ?? null;

  const columns = groupField ? [...groupField.options, UNSET] : [];
  const cardsIn = (column: string): RecordRow[] =>
    rows.filter((r) => {
      const value = groupKey ? r[groupKey] : null;
      return column === UNSET ? !value : value === column;
    });

  useAgentContext(() => ({
    pane: 'records.board',
    schema: schema?.id ?? null,
    groupBy: groupKey,
    columns: columns.map((c) => ({ name: c, count: cardsIn(c).length })),
    selectedRowId: selectedId,
  }));

  if (!schema) return <div className="rec-empty">No record table selected.</div>;
  if (!groupField) {
    return (
      <div className="rec-empty">
        <p>
          <strong>{schema.name}</strong> has no board column.
        </p>
        <p className="rec-dim">
          A board groups rows by a <em>select</em> field. Add one to this table (or set its board
          column) to use this view.
        </p>
      </div>
    );
  }

  const drop = (column: string) => {
    const rowId = dragging;
    setDragging(null);
    setOver(null);
    if (!rowId || !groupKey) return;
    const row = rows.find((r) => r.id === rowId);
    const next = column === UNSET ? null : column;
    if (!row || row[groupKey] === next) return;
    void saveRow(rowId, { [groupKey]: next });
  };

  return (
    <div className="rec-board">
      <div className="rec-toolbar">
        <span className="rec-title">
          {schema.icon ?? '▤'} {schema.name}
        </span>
        <span className="rec-dim">by {groupField.label}</span>
      </div>
      <div className="rec-board-columns">
        {columns.map((column) => {
          const cards = cardsIn(column);
          return (
            <div
              key={column}
              className={over === column ? 'rec-column rec-column-over' : 'rec-column'}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(column);
              }}
              onDragLeave={() => setOver((c) => (c === column ? null : c))}
              onDrop={() => drop(column)}
            >
              <div className="rec-column-head">
                <span>{column}</span>
                <span className="rec-dim">{cards.length}</span>
              </div>
              <div className="rec-column-body">
                {cards.map((row) => (
                  <div
                    key={row.id}
                    className={row.id === selectedId ? 'rec-card rec-card-selected' : 'rec-card'}
                    draggable
                    onDragStart={() => setDragging(row.id)}
                    onDragEnd={() => setDragging(null)}
                    onClick={() => selectRow(row.id)}
                  >
                    <div className="rec-card-title">{rowTitle(schema, row)}</div>
                    <div className="rec-card-sub">{rowSummary(schema.fields, row)}</div>
                  </div>
                ))}
                <button
                  className="rec-btn rec-btn-ghost rec-card-add"
                  onClick={() =>
                    void addRow(column === UNSET || !groupKey ? {} : { [groupKey]: column })
                  }
                >
                  + Add
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
