/**
 * Typed client for the knowledge library backend (`/api/library/*`).
 *
 * Backs both the Library panel and the module's agent tools, so the agent can add
 * and search sources through `packages/core` without importing the panel.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

export type SourceType = 'blog' | 'note';
export type SourceStatus = 'queued' | 'fetching' | 'chunking' | 'embedding' | 'ready' | 'failed';

export interface SourceModel {
  id: string;
  library: string;
  type: SourceType;
  title: string;
  url?: string | null;
  author?: string | null;
  tags: string[];
  status: SourceStatus;
  error?: string | null;
  chunk_count: number;
  added_at: string;
}

export interface IngestRequest {
  type: SourceType;
  library?: string;
  url?: string;
  title?: string;
  text?: string;
  author?: string;
  tags?: string[];
}

export interface LibraryInfo {
  name: string;
  source_count: number;
  chunk_count: number;
}

export interface ChunkModel {
  index: number;
  text: string;
}

export interface ChunksResponse {
  source: SourceModel;
  chunks: ChunkModel[];
}

export interface SearchChunk {
  chunk_index: number;
  text: string;
  score: number;
}

export interface SearchGroup {
  source_id: string;
  title: string;
  type: string;
  url?: string | null;
  tags: string[];
  top_score: number;
  chunks: SearchChunk[];
}

export interface LibrarySearchResponse {
  query: string;
  library: string;
  groups: SearchGroup[];
}

export function ingestSource(req: IngestRequest): Promise<SourceModel> {
  return apiPost<SourceModel>('/library/sources', req);
}

export function listSources(
  library?: string,
  type?: SourceType,
  tag?: string,
): Promise<{ sources: SourceModel[] }> {
  const params = new URLSearchParams();
  if (library) params.set('library', library);
  if (type) params.set('type', type);
  if (tag) params.set('tag', tag);
  const qs = params.toString();
  return apiGet<{ sources: SourceModel[] }>(`/library/sources${qs ? `?${qs}` : ''}`);
}

export function listLibraries(): Promise<{ libraries: LibraryInfo[] }> {
  return apiGet<{ libraries: LibraryInfo[] }>('/library/libraries');
}

export function getChunks(id: string): Promise<ChunksResponse> {
  return apiGet<ChunksResponse>(`/library/sources/${encodeURIComponent(id)}/chunks`);
}

export function deleteSource(id: string): Promise<{ deleted: boolean; id: string }> {
  return apiDelete<{ deleted: boolean; id: string }>(`/library/sources/${encodeURIComponent(id)}`);
}

export function librarySearch(
  library: string,
  text: string,
  limit = 5,
): Promise<LibrarySearchResponse> {
  return apiPost<LibrarySearchResponse>('/library/search', { library, text, limit });
}
