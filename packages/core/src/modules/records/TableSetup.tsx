/**
 * Define or reshape a table: name, icon, and the field declarations the grid, the
 * form and the board all render from.
 *
 * This pane exists because `createSchema`/`updateSchema`/`deleteSchema` shipped in
 * api.ts with **no caller** — a table could only be defined by the agent or by hand
 * over HTTP, which is why the rail's empty state used to point at a *workspace*
 * rather than at an action. A substrate whose only author is the agent is one the
 * user cannot start using on their own.
 *
 * Two backend rules the UI has to be honest about rather than hide (see store.py):
 * a schema id is also a physical table name, so it is validated, immutable after
 * creation, and narrower than a display name; and field removal is **additive-only**
 * — dropping a declaration hides the column, it does not delete the data.
 */
import { useEffect, useMemo, useState } from 'react';

import { dialogs } from '../../dialogs';
import { usePaneParams } from '../../panes';
import { registry } from '../../registry';
import { toastsStore } from '../../toasts';
import type { FieldDecl, FieldType, RecordSchema } from './api';
import {
  addSchema,
  editSchema,
  getActiveSchema,
  getSchemas,
  removeSchema,
  useRecords,
} from './store';
import './records.css';

const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'longtext', label: 'Long text' },
  { value: 'number', label: 'Number' },
  { value: 'date', label: 'Date' },
  { value: 'select', label: 'Choice' },
  { value: 'url', label: 'URL' },
  { value: 'email', label: 'Email' },
  { value: 'ref', label: 'Link to table' },
];

const ID_RE = /^[a-z][a-z0-9_]{0,39}$/;
/** Mirrors RESERVED_KEYS in store.py — the columns every records table owns. */
const RESERVED = new Set(['id', 'created_at', 'updated_at']);

/** Derive a legal id from a display name. Best-effort: the field stays editable,
 * because a name like "2024 leads" has no reasonable automatic id. */
function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^[^a-z]+/, '')
    .replace(/_+$/, '')
    .slice(0, 40);
}

function blankField(): FieldDecl {
  return { key: '', label: '', type: 'text', options: [], required: false, hidden: false };
}

function newSchema(): RecordSchema {
  return {
    id: '',
    name: '',
    icon: '▤',
    fields: [{ ...blankField(), key: 'title', label: 'Title', required: true }],
    board_column: null,
    title_column: 'title',
  };
}

export function TableSetup() {
  useRecords();
  const params = usePaneParams();
  const schemas = getSchemas();
  const active = getActiveSchema();

  // `params.schemaId === ''` (or 'new') means "create one"; otherwise edit the
  // named table, falling back to whatever the rail has selected.
  const requested = typeof params.schemaId === 'string' ? params.schemaId : null;
  const creating = requested === '' || requested === 'new';
  const target = creating ? null : (schemas.find((s) => s.id === requested) ?? active);

  const [draft, setDraft] = useState<RecordSchema>(() => (target ? { ...target } : newSchema()));
  const [loadedId, setLoadedId] = useState<string | null>(target?.id ?? null);
  const [idTouched, setIdTouched] = useState(false);
  const [saving, setSaving] = useState(false);

  // Re-seed the draft when the pane is pointed at a different table. Keyed on the
  // id rather than the object so an unrelated store emit doesn't discard edits.
  useEffect(() => {
    const nextId = creating ? null : (target?.id ?? null);
    if (nextId === loadedId) return;
    setLoadedId(nextId);
    setIdTouched(false);
    setDraft(target && !creating ? { ...target } : newSchema());
  }, [creating, target, loadedId]);

  const isNew = loadedId === null;
  const selectFields = draft.fields.filter((f) => f.type === 'select' && f.key);
  const labelFields = draft.fields.filter((f) => f.key && f.type !== 'longtext');

  const problems = useMemo(() => {
    const found: string[] = [];
    if (!draft.name.trim()) found.push('The table needs a name.');
    if (isNew && !ID_RE.test(draft.id)) {
      found.push('The id must start with a letter and use only lowercase letters, digits and _.');
    }
    if (isNew && schemas.some((s) => s.id === draft.id))
      found.push(`A table "${draft.id}" exists.`);
    const keys = draft.fields.map((f) => f.key);
    if (draft.fields.some((f) => !ID_RE.test(f.key))) {
      found.push('Every field needs a key: lowercase letters, digits and _.');
    }
    if (keys.some((k) => RESERVED.has(k)))
      found.push('id, created_at and updated_at are reserved.');
    if (new Set(keys).size !== keys.length) found.push('Two fields share a key.');
    if (draft.fields.some((f) => !f.label.trim())) found.push('Every field needs a label.');
    if (draft.fields.some((f) => f.type === 'select' && f.options.length === 0)) {
      found.push('A Choice field needs at least one option.');
    }
    if (draft.fields.some((f) => f.type === 'ref' && !f.ref_schema)) {
      found.push('A "Link to table" field needs a target table.');
    }
    return found;
  }, [draft, isNew, schemas]);

  const patchField = (index: number, patch: Partial<FieldDecl>) => {
    setDraft((prev) => ({
      ...prev,
      fields: prev.fields.map((f, i) => (i === index ? { ...f, ...patch } : f)),
    }));
  };

  const save = async () => {
    if (problems.length) return;
    setSaving(true);
    const ok = isNew
      ? (await addSchema(draft)) !== null
      : await editSchema(draft.id, {
          name: draft.name,
          icon: draft.icon,
          fields: draft.fields,
          board_column: draft.board_column || null,
          title_column: draft.title_column || null,
        });
    setSaving(false);
    if (!ok) {
      toastsStore.add('error', 'Could not save', 'The backend rejected the table definition.');
      return;
    }
    toastsStore.add(
      'success',
      isNew ? 'Table created' : 'Table updated',
      isNew ? `"${draft.name}" is ready to use.` : `"${draft.name}" was updated.`,
    );
    if (isNew) setLoadedId(draft.id);
  };

  const destroy = async () => {
    if (isNew) return;
    // Two questions, because they are genuinely different outcomes and the second
    // is the unrecoverable one. The backend keeps the rows by default for exactly
    // this reason — the catalog row is cheap to recreate, the rows are not.
    const confirmed = await dialogs.confirm({
      title: `Delete the "${draft.name}" table?`,
      message:
        'It disappears from the rail and the panes. Its rows are kept unless you say otherwise next.',
      confirmLabel: 'Delete table',
      danger: true,
    });
    if (!confirmed) return;
    const dropData = await dialogs.confirm({
      title: 'Delete the stored rows too?',
      message: `Keeping them means re-creating a table with the id "${draft.id}" later brings the rows back. Deleting them cannot be undone.`,
      confirmLabel: 'Delete the rows',
      cancelLabel: 'Keep the rows',
      danger: true,
    });
    if (await removeSchema(draft.id, dropData)) {
      toastsStore.add(
        'info',
        'Table deleted',
        dropData ? 'The table and its rows are gone.' : 'The rows were kept.',
      );
      registry.openPanel('explorer.home');
    }
  };

  return (
    <div className="rec-setup">
      <div className="rec-toolbar">
        <span className="rec-title">
          {isNew ? 'New table' : `Set up: ${draft.name || draft.id}`}
        </span>
        <div className="rec-setup-actions">
          {!isNew && (
            <button className="rec-btn rec-btn-danger" onClick={() => void destroy()}>
              Delete table
            </button>
          )}
          <button
            className="rec-btn rec-btn-primary"
            disabled={problems.length > 0 || saving}
            onClick={() => void save()}
          >
            {saving ? 'Saving…' : isNew ? 'Create table' : 'Save changes'}
          </button>
        </div>
      </div>

      <div className="rec-setup-body">
        <div className="rec-setup-row">
          <label className="rec-label">Icon</label>
          <input
            className="rec-input rec-input-icon"
            value={draft.icon ?? ''}
            maxLength={2}
            onChange={(e) => setDraft((p) => ({ ...p, icon: e.target.value }))}
          />
          <label className="rec-label">Name</label>
          <input
            className="rec-input"
            value={draft.name}
            placeholder="Papers to read"
            onChange={(e) =>
              setDraft((p) => ({
                ...p,
                name: e.target.value,
                id: isNew && !idTouched ? slug(e.target.value) : p.id,
              }))
            }
          />
        </div>

        <div className="rec-setup-row">
          <label className="rec-label">Table id</label>
          <input
            className="rec-input"
            value={draft.id}
            disabled={!isNew}
            placeholder="papers"
            onChange={(e) => {
              setIdTouched(true);
              setDraft((p) => ({ ...p, id: e.target.value }));
            }}
          />
          <span className="rec-dim">
            {isNew
              ? `stored as rec_${draft.id || '…'} — queryable from the database console`
              : 'fixed: it is the physical table name'}
          </span>
        </div>

        <div className="rec-setup-fields">
          <div className="rec-setup-head">
            <strong>Fields</strong>
            <button
              className="rec-btn"
              onClick={() => setDraft((p) => ({ ...p, fields: [...p.fields, blankField()] }))}
            >
              + Add field
            </button>
          </div>

          {draft.fields.map((field, index) => (
            <div key={index} className="rec-setup-field">
              <input
                className="rec-input rec-input-key"
                value={field.key}
                placeholder="key"
                onChange={(e) => patchField(index, { key: e.target.value })}
              />
              <input
                className="rec-input"
                value={field.label}
                placeholder="Label"
                onChange={(e) =>
                  patchField(index, {
                    label: e.target.value,
                    key: field.key || slug(e.target.value),
                  })
                }
              />
              <select
                className="rec-input rec-input-type"
                value={field.type}
                onChange={(e) => patchField(index, { type: e.target.value as FieldType })}
              >
                {FIELD_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>

              {field.type === 'select' && (
                <input
                  className="rec-input"
                  value={field.options.join(', ')}
                  placeholder="Comma, separated, options"
                  onChange={(e) =>
                    patchField(index, {
                      options: e.target.value
                        .split(',')
                        .map((o) => o.trim())
                        .filter(Boolean),
                    })
                  }
                />
              )}

              {field.type === 'ref' && (
                <select
                  className="rec-input"
                  value={field.ref_schema ?? ''}
                  onChange={(e) => patchField(index, { ref_schema: e.target.value || null })}
                >
                  <option value="">pick a table…</option>
                  {schemas.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              )}

              <label className="rec-setup-flag" title="Must be filled in">
                <input
                  type="checkbox"
                  checked={field.required}
                  onChange={(e) => patchField(index, { required: e.target.checked })}
                />
                required
              </label>

              <button
                className="rec-btn rec-btn-quiet"
                title="Remove this field. The stored column is kept — see the note below."
                onClick={() =>
                  setDraft((p) => ({ ...p, fields: p.fields.filter((_, i) => i !== index) }))
                }
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <div className="rec-setup-row">
          <label className="rec-label">Row label</label>
          <select
            className="rec-input"
            value={draft.title_column ?? ''}
            onChange={(e) => setDraft((p) => ({ ...p, title_column: e.target.value || null }))}
          >
            <option value="">first text field</option>
            {labelFields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label || f.key}
              </option>
            ))}
          </select>
          <label className="rec-label">Board column</label>
          <select
            className="rec-input"
            value={draft.board_column ?? ''}
            onChange={(e) => setDraft((p) => ({ ...p, board_column: e.target.value || null }))}
          >
            <option value="">no board</option>
            {selectFields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label || f.key}
              </option>
            ))}
          </select>
          <span className="rec-dim">a Choice field turns on the Board pane</span>
        </div>

        {problems.length > 0 && (
          <ul className="rec-error rec-setup-problems">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        )}

        <p className="rec-dim rec-setup-note">
          Changes are additive: a new field adds a column, and removing one here hides it without
          deleting what is stored. That is deliberate — a form edit should not be able to destroy
          data. Use <strong>Delete table</strong> if you mean to drop it.
        </p>
      </div>
    </div>
  );
}
