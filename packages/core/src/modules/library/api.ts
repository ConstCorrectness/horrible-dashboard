/**
 * Typed client for the knowledge library backend (`/api/library/*`).
 *
 * Backs both the Library panel and the module's agent tools, so the agent can add
 * and search sources through `packages/core` without importing the panel.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

export type SourceType = 'blog' | 'note' | 'image' | 'video';
export type SourceStatus = 'queued' | 'fetching' | 'chunking' | 'embedding' | 'ready' | 'failed';

/**
 * A referenced image/video. The bytes stay at `src` — the library never copies
 * them — and the descriptive fields (`alt`, `caption`, `context`) are what get
 * embedded, since the backend embedder is text-only. Mirrors `MediaAsset` in
 * backend/modules/library/models.py.
 */
export interface MediaAsset {
  src: string;
  kind: 'image' | 'video' | 'embed';
  page_url?: string | null;
  alt?: string | null;
  caption?: string | null;
  context?: string[];
  width?: number | null;
  height?: number | null;
  duration?: number | null;
  poster?: string | null;
  mime?: string | null;
}

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
  asset?: MediaAsset | null;
}

export interface IngestRequest {
  type: SourceType;
  library?: string;
  url?: string;
  title?: string;
  text?: string;
  author?: string;
  tags?: string[];
  /** Required for `image`/`video`; ignored otherwise. */
  asset?: MediaAsset;
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
  /** Present on image/video hits: what matched is proxy text, what you want is this. */
  asset?: MediaAsset | null;
  /**
   * Which space(s) matched. A `clip`-only hit means the *picture* matched while its
   * words didn't — and it has no `chunks`, because no passage was involved.
   */
  matched_by?: ('text' | 'clip')[];
}

/** Availability + coverage of CLIP visual search (`GET /api/library/clip`). */
export interface ClipStatus {
  enabled: boolean;
  installed: boolean;
  model: string;
  dim: number;
  media_sources: number;
  libraries_indexed: string[];
}

export function clipStatus(): Promise<ClipStatus> {
  return apiGet<ClipStatus>('/library/clip');
}

export function reindexClip(library?: string): Promise<{ started: boolean; queued: number }> {
  const qs = library ? `?library=${encodeURIComponent(library)}` : '';
  return apiPost<{ started: boolean; queued: number }>(`/library/reindex-clip${qs}`, {});
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
