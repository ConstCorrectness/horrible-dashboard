/**
 * Typed client for the records backend (`/records/*`).
 *
 * Mirrors backend/modules/records/models.py. Rows are open records rather than a
 * generated type because the columns are user-defined — a schema's `fields` is the
 * runtime type, and the panes render from it.
 */
import { apiDelete, apiGet, apiPatch, apiPost } from '../../api';

export type FieldType =
  | 'text'
  | 'longtext'
  | 'number'
  | 'date'
  | 'select'
  | 'url'
  | 'email'
  | 'ref';

export interface FieldDecl {
  key: string;
  label: string;
  type: FieldType;
  options: string[];
  required: boolean;
  ref_schema?: string | null;
  hidden: boolean;
}

export interface RecordSchema {
  id: string;
  name: string;
  icon?: string | null;
  fields: FieldDecl[];
  /** The `select` field the board groups cards by; null = no board view. */
  board_column?: string | null;
  title_column?: string | null;
  /** Row count, present on the list endpoint only. */
  count?: number;
}

/** A stored row: the reserved columns plus one entry per declared field. */
export interface RecordRow {
  id: string;
  created_at: string;
  updated_at: string;
  [field: string]: unknown;
}

export interface ProposedField {
  value: unknown;
  source?: string | null;
  confidence?: number | null;
}

/** An agent's proposed write, awaiting per-field review in the form pane. */
export interface Proposal {
  id: string;
  schema_id: string;
  record_id?: string | null;
  fields: Record<string, ProposedField>;
  source?: string | null;
  status: 'pending' | 'applied' | 'rejected';
  created_at?: string | null;
}

export function listSchemas(): Promise<{ schemas: RecordSchema[] }> {
  return apiGet('/records/schemas');
}

export function createSchema(body: Partial<RecordSchema> & { id: string }): Promise<RecordSchema> {
  return apiPost('/records/schemas', body);
}

export function updateSchema(id: string, body: Partial<RecordSchema>): Promise<RecordSchema> {
  return apiPatch(`/records/schemas/${id}`, body);
}

export function deleteSchema(id: string, dropTable = false): Promise<{ ok: boolean }> {
  return apiDelete(`/records/schemas/${id}?drop_table=${dropTable}`);
}

/** Create the built-in CRM/intake schemas that don't exist yet. Idempotent. */
export function seedSchemas(ids?: string[]): Promise<{ created: string[] }> {
  return apiPost('/records/seed', ids ?? null);
}

export function listRows(schemaId: string, search?: string): Promise<{ rows: RecordRow[] }> {
  const query = search ? `?search=${encodeURIComponent(search)}` : '';
  return apiGet(`/records/${schemaId}/rows${query}`);
}

export function createRow(schemaId: string, values: Record<string, unknown>): Promise<RecordRow> {
  return apiPost(`/records/${schemaId}/rows`, { values });
}

export function updateRow(
  schemaId: string,
  recordId: string,
  values: Record<string, unknown>,
): Promise<RecordRow> {
  return apiPatch(`/records/${schemaId}/rows/${recordId}`, { values });
}

export function deleteRow(schemaId: string, recordId: string): Promise<{ ok: boolean }> {
  return apiDelete(`/records/${schemaId}/rows/${recordId}`);
}

export function listProposals(schemaId?: string): Promise<{ proposals: Proposal[] }> {
  const query = schemaId ? `?schema_id=${encodeURIComponent(schemaId)}` : '';
  return apiGet(`/records/proposals/pending${query}`);
}

/** Accept a proposal's fields (all of them when `accept` is omitted). */
export function applyProposal(
  proposalId: string,
  accept?: string[],
): Promise<{ applied: boolean; row: RecordRow | null }> {
  return apiPost(`/records/proposals/${proposalId}/apply`, { accept: accept ?? null });
}

export function rejectProposal(proposalId: string): Promise<{ ok: boolean }> {
  return apiPost(`/records/proposals/${proposalId}/reject`, {});
}

/** A row's human label: its schema's title column, else the first text-ish value. */
export function rowTitle(schema: RecordSchema, row: RecordRow): string {
  const key = schema.title_column ?? schema.fields.find((f) => f.type === 'text' && !f.hidden)?.key;
  const value = key ? row[key] : null;
  return value ? String(value) : `(untitled ${row.id})`;
}
