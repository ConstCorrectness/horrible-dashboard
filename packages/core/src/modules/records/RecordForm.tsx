/**
 * The form pane: one record, field by field — and the review surface for the
 * agent's proposals.
 *
 * When a proposal is pending for this record, each affected field shows the
 * current value against the proposed one with its citation, and an accept/reject
 * per field. That review is the whole agentic data-entry loop: the model extracts,
 * the human confirms, and nothing reaches the table in between. Same convention as
 * the editor's proposeEdit diff. See docs/modules/records.mdx.
 */
import { useEffect, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import { toastsStore } from '../../toasts';
import { applyProposal, rejectProposal, rowTitle } from './api';
import { FieldInput, formatValue, visibleFields } from './fields';
import {
  addRow,
  closeProposal,
  getActiveProposal,
  getActiveSchema,
  getError,
  getSelectedRow,
  initRecordsWatch,
  refreshRows,
  saveRow,
  useRecords,
} from './store';
import './records.css';

export function RecordForm() {
  useRecords();
  // Fields the user has ticked off in the current review; reset per proposal.
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [reviewing, setReviewing] = useState<string | null>(null);

  useEffect(() => {
    initRecordsWatch();
  }, []);

  const schema = getActiveSchema();
  const row = getSelectedRow();
  const proposal = getActiveProposal();
  const fields = schema ? visibleFields(schema.fields) : [];

  // A new proposal starts fully ticked: the common case is "the extraction is
  // right", and unticking the one wrong field is less work than ticking six.
  useEffect(() => {
    if (proposal && proposal.id !== reviewing) {
      setReviewing(proposal.id);
      setAccepted(Object.fromEntries(Object.keys(proposal.fields).map((k) => [k, true])));
    } else if (!proposal && reviewing) {
      setReviewing(null);
      setAccepted({});
    }
  }, [proposal, reviewing]);

  useAgentContext(() => ({
    pane: 'records.form',
    schema: schema?.id ?? null,
    recordId: row?.id ?? null,
    title: schema && row ? rowTitle(schema, row) : null,
    values: row ? Object.fromEntries(fields.map((f) => [f.key, formatValue(f, row[f.key])])) : null,
    emptyFields: row ? fields.filter((f) => !row[f.key]).map((f) => f.key) : [],
    pendingProposal: proposal ? { id: proposal.id, fields: Object.keys(proposal.fields) } : null,
  }));

  if (!schema) return <div className="rec-empty">No record table selected.</div>;

  const acceptSelected = async () => {
    if (!proposal) return;
    const keys = Object.keys(accepted).filter((k) => accepted[k]);
    const result = await applyProposal(proposal.id, keys);
    closeProposal(proposal.id);
    await refreshRows();
    toastsStore.add(
      result.applied ? 'success' : 'info',
      result.applied ? 'Record updated' : 'Proposal declined',
      result.applied ? `${keys.length} field(s) accepted.` : 'Nothing was written.',
    );
  };

  const rejectAll = async () => {
    if (!proposal) return;
    await rejectProposal(proposal.id);
    closeProposal(proposal.id);
  };

  if (!row && !proposal) {
    return (
      <div className="rec-empty">
        <p>No record open.</p>
        <button className="rec-btn rec-btn-primary" onClick={() => void addRow()}>
          + New {schema.name.replace(/s$/, '')}
        </button>
      </div>
    );
  }

  return (
    <div className="rec-form">
      <div className="rec-toolbar">
        <span className="rec-title">
          {schema.icon ?? '▤'} {row ? rowTitle(schema, row) : `New ${schema.name}`}
        </span>
        {row && <span className="rec-dim">{row.id}</span>}
      </div>

      {proposal && (
        <div className="rec-proposal">
          <div className="rec-proposal-head">
            <strong>Agent proposal</strong>
            <span className="rec-dim">
              {proposal.record_id ? 'updates this record' : 'creates a new record'}
              {proposal.source ? ` · ${proposal.source}` : ''}
            </span>
          </div>
          <div className="rec-proposal-actions">
            <button className="rec-btn rec-btn-primary" onClick={() => void acceptSelected()}>
              Accept selected
            </button>
            <button className="rec-btn" onClick={() => void rejectAll()}>
              Reject all
            </button>
          </div>
        </div>
      )}

      {getError() && <div className="rec-error">{getError()}</div>}

      <div className="rec-form-body">
        {fields.map((field) => {
          const proposed = proposal?.fields[field.key];
          const currentText = row ? formatValue(field, row[field.key]) : '';
          return (
            <div
              key={field.key}
              className={proposed ? 'rec-field rec-field-proposed' : 'rec-field'}
            >
              <label className="rec-label">
                {field.label}
                {field.required && <span className="rec-required">*</span>}
              </label>

              {proposed ? (
                <div className="rec-diff">
                  <div className="rec-diff-row">
                    <span className="rec-diff-tag">now</span>
                    <span className="rec-diff-old">{currentText || '—'}</span>
                  </div>
                  <div className="rec-diff-row">
                    <span className="rec-diff-tag">proposed</span>
                    <span className="rec-diff-new">
                      {formatValue(field, proposed.value) || '—'}
                    </span>
                  </div>
                  {proposed.source && (
                    <div className="rec-provenance" title={proposed.source}>
                      from: {proposed.source}
                    </div>
                  )}
                  <label className="rec-accept">
                    <input
                      type="checkbox"
                      checked={accepted[field.key] ?? false}
                      onChange={(e) =>
                        setAccepted((prev) => ({ ...prev, [field.key]: e.target.checked }))
                      }
                    />
                    accept
                  </label>
                </div>
              ) : (
                <FieldInput
                  field={field}
                  value={row ? row[field.key] : null}
                  onCommit={(value) => {
                    if (row) void saveRow(row.id, { [field.key]: value });
                  }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
