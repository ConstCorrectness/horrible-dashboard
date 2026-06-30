/**
 * Typed client for the database inspector backend (`/api/database/*`).
 *
 * The same helpers back both the console widget and the module's agent tools, so
 * other modules can reach the app's databases through `packages/core` without
 * importing the widget. Connection secrets are never returned by the backend —
 * `config` values for secret fields come back as booleans (set / unset).
 */
import { apiDelete, apiGet, apiPost, apiPut } from '../../api';

export type ConnectionConfig = Record<string, string | number | boolean>;

export interface ProviderInfo {
  id: string;
  label: string;
  fields: string[];
}

export interface ConnectionInfo {
  id: string;
  name: string;
  provider: string;
  config: ConnectionConfig;
  builtin: boolean;
}

export interface ConnectionsResponse {
  connections: ConnectionInfo[];
  providers: ProviderInfo[];
}

export interface ConnectionInput {
  name: string;
  provider: string;
  config: ConnectionConfig;
}

export interface ConnectionTestResult {
  ok: boolean;
  error?: string | null;
}

export interface ResultColumn {
  name: string;
  type?: string | null;
}

export interface QueryResult {
  columns: ResultColumn[];
  rows: unknown[][];
  rowcount: number;
  elapsed_ms: number;
  truncated: boolean;
  affected?: number | null;
  message?: string | null;
}

export interface QueryRequest {
  connection_id: string;
  sql: string;
  params?: unknown[] | null;
  read_only?: boolean;
  row_limit?: number;
}

export interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
}

export interface SchemaTable {
  name: string;
  schema_name?: string | null;
  columns: SchemaColumn[];
}

export interface SchemaResponse {
  tables: SchemaTable[];
}

export interface VectorStatus {
  db_path: string;
  size_bytes: number;
  num_documents: number;
  collections: { name: string; count: number }[];
  active_provider: string;
  active_model: string;
}

export function listConnections(): Promise<ConnectionsResponse> {
  return apiGet<ConnectionsResponse>('/database/connections');
}

export function createConnection(body: ConnectionInput): Promise<ConnectionInfo> {
  return apiPost<ConnectionInfo>('/database/connections', body);
}

export function updateConnection(id: string, body: ConnectionInput): Promise<ConnectionInfo> {
  return apiPut<ConnectionInfo>(`/database/connections/${encodeURIComponent(id)}`, body);
}

export function deleteConnection(id: string): Promise<{ deleted: boolean; id: string }> {
  return apiDelete<{ deleted: boolean; id: string }>(
    `/database/connections/${encodeURIComponent(id)}`,
  );
}

/** Test an unsaved connection (the form still holds the password). */
export function testConnection(body: ConnectionInput): Promise<ConnectionTestResult> {
  return apiPost<ConnectionTestResult>('/database/connections/test', body);
}

/** Test a stored connection by id (uses server-side credentials). */
export function testSavedConnection(id: string): Promise<ConnectionTestResult> {
  return apiPost<ConnectionTestResult>(`/database/connections/${encodeURIComponent(id)}/test`, {});
}

export function getSchema(id: string): Promise<SchemaResponse> {
  return apiGet<SchemaResponse>(`/database/connections/${encodeURIComponent(id)}/schema`);
}

export function runQuery(req: QueryRequest): Promise<QueryResult> {
  return apiPost<QueryResult>('/database/query', req);
}

// --- Built-in app vector store helpers ---

export function getStatus(): Promise<VectorStatus> {
  return apiGet<VectorStatus>('/database/status');
}

export function semanticSearch(collection: string, text: string, limit: number): Promise<unknown> {
  return apiPost('/database/search', { collection, text, limit });
}
