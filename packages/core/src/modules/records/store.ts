/**
 * Shared store for the records panes. The grid, the form and the board are three
 * views of one thing, so schema declarations, loaded rows, the selected record and
 * the pending proposal queue all live here rather than in any one component —
 * clicking a card on the board has to move the form, and both survive a remount.
 *
 * Agent writes arrive live on the `records` `/ws` channel: a `proposal` event puts
 * a reviewable diff in front of the user, a `row` event upserts a committed row.
 * See docs/modules/records.mdx.
 */
import { useSyncExternalStore } from 'react';

import { subscribeChannel, type WsMessage } from '../../ws';
import {
  createRow as apiCreateRow,
  createSchema as apiCreateSchema,
  deleteRow as apiDeleteRow,
  deleteSchema as apiDeleteSchema,
  listProposals,
  listRows,
  listSchemas,
  seedSchemas,
  updateRow as apiUpdateRow,
  updateSchema as apiUpdateSchema,
  type Proposal,
  type RecordRow,
  type RecordSchema,
} from './api';

// --- reactive core (useSyncExternalStore) ---
let version = 0;
const listeners = new Set<() => void>();

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

export function subscribeRecords(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function recordsVersion(): number {
  return version;
}

/** Re-render the calling pane on any store change; read state with the getters
 * below. All four panes are views of one store, so they all subscribe to all of
 * it — the state is small and a selection change moves three of them anyway. */
export function useRecords(): void {
  useSyncExternalStore(subscribeRecords, recordsVersion);
}

// --- state ---
let schemas: RecordSchema[] = [];
let activeSchemaId: string | null = null;
let rows: RecordRow[] = [];
let selectedRowId: string | null = null;
let proposals: Proposal[] = [];
let search = '';
let loading = false;
let error: string | null = null;

export function getSchemas(): RecordSchema[] {
  return schemas;
}

export function getActiveSchema(): RecordSchema | null {
  return schemas.find((s) => s.id === activeSchemaId) ?? null;
}

export function getRows(): RecordRow[] {
  return rows;
}

export function getSelectedRow(): RecordRow | null {
  return rows.find((r) => r.id === selectedRowId) ?? null;
}

export function getSelectedRowId(): string | null {
  return selectedRowId;
}

/** Pending proposals for the active schema, newest first. */
export function getProposals(): Proposal[] {
  return proposals.filter((p) => p.schema_id === activeSchemaId);
}

/** Every pending proposal, across all tables. The rail's badge counts *this*, not
 * `getProposals()` — an agent files against whatever table it was asked about, not
 * whichever one you happen to have selected, and a review queue you can only see
 * by first guessing the right table is one you will never see. */
export function getAllProposals(): Proposal[] {
  return proposals;
}

/** Pending count per schema id, for the per-table markers in the rail. */
export function getPendingBySchema(): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const proposal of proposals) {
    counts[proposal.schema_id] = (counts[proposal.schema_id] ?? 0) + 1;
  }
  return counts;
}

/** The proposal the form should be showing: one against the selected row, else a
 * new-row proposal. Reviewing is per record, so an unrelated pending proposal on
 * another row must not hijack the open form. */
export function getActiveProposal(): Proposal | null {
  const forSchema = getProposals();
  return (
    forSchema.find((p) => p.record_id && p.record_id === selectedRowId) ??
    forSchema.find((p) => !p.record_id) ??
    null
  );
}

export function getSearch(): string {
  return search;
}

export function isLoading(): boolean {
  return loading;
}

export function getError(): string | null {
  return error;
}

// --- actions ---

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// Seeding is attempted at most once per session: an empty catalog on a fresh node
// means "never used records", but an empty catalog after the user deleted every
// table means "leave me alone", and re-seeding on every refresh would fight them.
let seedAttempted = false;

export async function refreshSchemas(): Promise<void> {
  try {
    schemas = (await listSchemas()).schemas;
    if (schemas.length === 0 && !seedAttempted) {
      seedAttempted = true;
      await seedSchemas();
      schemas = (await listSchemas()).schemas;
    }
    error = null;
    if (!activeSchemaId || !schemas.some((s) => s.id === activeSchemaId)) {
      // Prefer a table with a board: it's the one with a workflow, and it means a
      // workspace opens on something the Board pane can actually draw rather than
      // on whichever table sorts first (which would render its empty state).
      activeSchemaId = (schemas.find((s) => s.board_column) ?? schemas[0])?.id ?? null;
      if (activeSchemaId) void refreshRows();
    }
  } catch (err) {
    error = message(err);
  }
  emit();
}

export async function refreshRows(): Promise<void> {
  if (!activeSchemaId) {
    rows = [];
    emit();
    return;
  }
  loading = true;
  emit();
  const schemaId = activeSchemaId;
  try {
    const result = await listRows(schemaId, search || undefined);
    // A slow response for a schema the user has since switched away from must not
    // overwrite the rows they're now looking at.
    if (schemaId !== activeSchemaId) return;
    rows = result.rows;
    error = null;
    if (selectedRowId && !rows.some((r) => r.id === selectedRowId)) selectedRowId = null;
  } catch (err) {
    error = message(err);
  } finally {
    loading = false;
    emit();
  }
}

export async function refreshProposals(): Promise<void> {
  try {
    proposals = (await listProposals()).proposals;
  } catch {
    /* backend down — the queue refills on the next event */
  }
  emit();
}

export function setActiveSchema(schemaId: string): void {
  if (schemaId === activeSchemaId) return;
  activeSchemaId = schemaId;
  rows = [];
  selectedRowId = null;
  emit();
  void refreshRows();
}

export function selectRow(recordId: string | null): void {
  selectedRowId = recordId;
  emit();
}

export function setSearch(text: string): void {
  search = text;
  emit();
  void refreshRows();
}

function upsertRow(row: RecordRow): void {
  const index = rows.findIndex((r) => r.id === row.id);
  rows = index === -1 ? [row, ...rows] : rows.map((r) => (r.id === row.id ? row : r));
  emit();
}

export async function saveRow(recordId: string, values: Record<string, unknown>): Promise<void> {
  if (!activeSchemaId) return;
  try {
    upsertRow(await apiUpdateRow(activeSchemaId, recordId, values));
    error = null;
  } catch (err) {
    error = message(err);
    emit();
  }
}

export async function addRow(values: Record<string, unknown> = {}): Promise<string | null> {
  if (!activeSchemaId) return null;
  try {
    const row = await apiCreateRow(activeSchemaId, values);
    upsertRow(row);
    selectedRowId = row.id;
    error = null;
    emit();
    return row.id;
  } catch (err) {
    error = message(err);
    emit();
    return null;
  }
}

export async function removeRow(recordId: string): Promise<void> {
  if (!activeSchemaId) return;
  try {
    await apiDeleteRow(activeSchemaId, recordId);
    rows = rows.filter((r) => r.id !== recordId);
    if (selectedRowId === recordId) selectedRowId = null;
    error = null;
  } catch (err) {
    error = message(err);
  }
  emit();
}

// --- schema actions ----------------------------------------------------------
// These three wrap the API calls that shipped with no caller at all: until now a
// table could only be defined by the agent or by hand over HTTP, which is why the
// rail's empty state pointed at a *workspace* instead of at an action.

/** Define a new table and select it. Returns the id, or null on failure. */
export async function addSchema(schema: RecordSchema): Promise<string | null> {
  try {
    const created = await apiCreateSchema(schema);
    error = null;
    await refreshSchemas();
    setActiveSchema(created.id);
    return created.id;
  } catch (err) {
    error = message(err);
    emit();
    return null;
  }
}

/** Rewrite a table's declaration. Additive on the backend: new fields become
 * columns, a dropped field only disappears from the UI (see store.py). */
export async function editSchema(schemaId: string, patch: Partial<RecordSchema>): Promise<boolean> {
  try {
    await apiUpdateSchema(schemaId, patch);
    error = null;
    await refreshSchemas();
    if (schemaId === activeSchemaId) await refreshRows();
    return true;
  } catch (err) {
    error = message(err);
    emit();
    return false;
  }
}

/** Forget a table. `dropData` also drops the physical `rec_*` table — without it
 * the rows survive, which is the backend's default and the recoverable choice. */
export async function removeSchema(schemaId: string, dropData = false): Promise<boolean> {
  try {
    await apiDeleteSchema(schemaId, dropData);
    error = null;
    if (schemaId === activeSchemaId) {
      activeSchemaId = null;
      rows = [];
      selectedRowId = null;
    }
    proposals = proposals.filter((p) => p.schema_id !== schemaId);
    await refreshSchemas();
    return true;
  } catch (err) {
    error = message(err);
    emit();
    return false;
  }
}

/** Drop a closed proposal from the queue (after accept/reject). */
export function closeProposal(proposalId: string): void {
  proposals = proposals.filter((p) => p.id !== proposalId);
  emit();
}

// --- live updates ---
let watching = false;

/** Listeners for committed rows arriving from elsewhere (the agent, another
 * window). A *pinned* grid is not part of the shared selection — it shows one
 * schema regardless of what the rail is on — so it can't use the state above and
 * needs the raw event instead. */
type RowListener = (schemaId: string, row: RecordRow) => void;
const rowListeners = new Set<RowListener>();

export function onRowEvent(listener: RowListener): () => void {
  rowListeners.add(listener);
  return () => {
    rowListeners.delete(listener);
  };
}

/** Subscribe to the `records` channel once per session (panes call it on mount). */
export function initRecordsWatch(): void {
  if (watching) return;
  watching = true;
  subscribeChannel('records', (msg: WsMessage) => {
    const data = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'proposal') {
      const proposal = data as unknown as Proposal;
      proposals = [proposal, ...proposals.filter((p) => p.id !== proposal.id)];
      emit();
    } else if (msg.event === 'proposal_closed') {
      closeProposal(String(data.id ?? ''));
    } else if (msg.event === 'row') {
      // A committed row — from the agent, or from another window on this node.
      if (!data.row) return;
      const row = data.row as RecordRow;
      const schemaId = String(data.schemaId ?? '');
      if (schemaId === activeSchemaId) upsertRow(row);
      for (const listener of rowListeners) listener(schemaId, row);
    }
  });
  void refreshSchemas();
  void refreshProposals();
}
