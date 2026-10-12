/**
 * Typed client for the knowledge base backend (`/api/kb/*`).
 *
 * Mirrors `backend/modules/kb/models.py`. Shared by the Knowledge pane, the
 * browser's Save to Knowledge popover and the module's agent tools.
 */
import { apiDelete, apiGet, apiPatch, apiPost } from '../../api';

export type Kind =
  | 'bookmark'
  | 'article'
  | 'primer'
  | 'course'
  | 'chapter'
  | 'lesson'
  | 'paper'
  | 'video'
  | 'site'
  | 'note';
export type Status = 'to_read' | 'reading' | 'done' | 'archived';
export type IndexMode = 'link' | 'text' | 'archive' | 'site';
export type IndexStatus = 'none' | 'queued' | 'indexing' | 'ready' | 'failed' | 'skipped';

export const KINDS: Kind[] = [
  'article',
  'bookmark',
  'primer',
  'course',
  'lesson',
  'paper',
  'video',
  'site',
  'note',
];
export const STATUSES: Status[] = ['to_read', 'reading', 'done', 'archived'];

export interface Progress {
  done: number;
  total: number;
}

export interface Item {
  id: string;
  kind: Kind;
  url?: string | null;
  canonical_url?: string | null;
  alias_url?: string | null;
  title: string;
  description: string;
  author?: string | null;
  tags: string[];
  status: Status;
  rating?: number | null;
  notes: string;
  parent_id?: string | null;
  ordinal: number;
  index_mode: IndexMode;
  index_status: IndexStatus;
  index_error?: string | null;
  library?: string | null;
  source_id?: string | null;
  chunk_count: number;
  docset_id?: string | null;
  docpage_id?: string | null;
  artifact_id?: string | null;
  content_signal?: string | null;
  starred: boolean;
  profile_id?: string | null;
  collections: string[];
  added_at: string;
  updated_at?: string | null;
  opened_at?: string | null;
  completed_at?: string | null;
  progress?: Progress | null;
  child_count: number;
}

export interface TreeNode {
  item: Item;
  children: TreeNode[];
}

export interface Collection {
  id: string;
  name: string;
  description: string;
  item_count: number;
}

export interface Summary {
  items: number;
  indexed: number;
  by_kind: Record<string, number>;
  collections: string[];
}

export interface Passage {
  item_id?: string | null;
  source_id: string;
  title: string;
  section?: string | null;
  url?: string | null;
  link?: string | null;
  docpage_id?: string | null;
  artifact_id?: string | null;
  library: string;
  score: number;
  text: string;
}

export interface SearchResponse {
  query: string;
  passages: Passage[];
  libraries: string[];
  fused_by_rank: boolean;
}

export interface Profile {
  id: string;
  label?: string;
  description?: string;
  builtin?: boolean;
  match?: string[];
  max_pages?: number;
  collections?: string[];
  /** The kind the profile files a page as (`primer`, `lesson`, …). */
  page_kind?: string;
}

export interface ImportResponse {
  root_item_id: string;
  set_id: string;
  resumed: boolean;
  plan: Profile & Record<string, unknown>;
}

export interface ListFilters {
  collection?: string;
  tag?: string;
  status?: Status;
  kind?: Kind;
  q?: string;
  parent_id?: string;
  starred?: boolean;
}

function query(filters: ListFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '' && value !== null) params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

export function listItems(filters: ListFilters = {}): Promise<{ items: Item[]; total: number }> {
  return apiGet(`/kb/items${query(filters)}`);
}

export function getItem(id: string): Promise<Item> {
  return apiGet(`/kb/items/${id}`);
}

export function getTree(id: string): Promise<TreeNode> {
  return apiGet(`/kb/items/${id}/tree`);
}

export interface CreateItemRequest {
  url?: string;
  kind?: Kind;
  title?: string;
  description?: string;
  tags?: string[];
  collections?: string[];
  status?: Status;
  notes?: string;
  rating?: number;
  starred?: boolean;
  index_mode?: IndexMode;
}

export function saveItem(req: CreateItemRequest): Promise<Item> {
  return apiPost('/kb/items', req);
}

export type UpdateItemRequest = Partial<
  Pick<
    Item,
    'title' | 'kind' | 'description' | 'tags' | 'collections' | 'status' | 'notes' | 'starred'
  >
> & { rating?: number | null };

export function updateItem(id: string, patch: UpdateItemRequest): Promise<Item> {
  return apiPatch(`/kb/items/${id}`, patch);
}

export function markOpened(id: string): Promise<Item> {
  return apiPost(`/kb/items/${id}/opened`, {});
}

export function deleteItem(id: string): Promise<{ ok: boolean; deleted: number }> {
  return apiDelete(`/kb/items/${id}`);
}

export function setIndexMode(id: string, mode: IndexMode): Promise<Item> {
  return apiPost(`/kb/items/${id}/index`, { mode });
}

export function lookup(url: string): Promise<{ item: Item | null }> {
  return apiGet(`/kb/lookup?url=${encodeURIComponent(url)}`);
}

export function summary(): Promise<Summary> {
  return apiGet('/kb/summary');
}

export function listCollections(): Promise<{ collections: Collection[] }> {
  return apiGet('/kb/collections');
}

export function search(req: {
  query: string;
  collections?: string[];
  item_id?: string;
  limit?: number;
}): Promise<SearchResponse> {
  return apiPost('/kb/search', req);
}

export function listProfiles(): Promise<{ profiles: Profile[] }> {
  return apiGet('/kb/profiles');
}

export function matchProfile(
  url: string,
): Promise<{ profile: Profile | null; params: Record<string, string> }> {
  return apiGet(`/kb/profiles/match?url=${encodeURIComponent(url)}`);
}

export function startImport(req: {
  profile_id?: string;
  url?: string;
  max_pages?: number;
  collections?: string[];
  force?: boolean;
}): Promise<ImportResponse> {
  return apiPost('/kb/imports', req);
}

export function stopImport(itemId: string): Promise<{ ok: boolean }> {
  return apiPost(`/kb/imports/${itemId}/stop`, {});
}

/** Re-extract and re-embed an imported site from its archives (no re-crawl). */
export function reindexImport(itemId: string): Promise<{ ok: boolean }> {
  return apiPost(`/kb/imports/${itemId}/reindex`, {});
}
