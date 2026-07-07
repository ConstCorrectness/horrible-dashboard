/** Backend client for the code index. See backend/modules/code/routes.py. */
import { apiGet, apiPost } from '../../api';
import type { DocumentSymbols, FindResult, SemanticSearchResult } from './types';

/** The definitions (outline) in one file. `path` may be absolute or root-relative. */
export function fetchDocumentSymbols(path: string): Promise<DocumentSymbols> {
  return apiGet<DocumentSymbols>(`/code/symbols?path=${encodeURIComponent(path)}`);
}

/** Fuzzy symbol search across all workspace roots (exact name / structural). */
export function findSymbols(q: string, limit = 50): Promise<FindResult> {
  return apiGet<FindResult>(`/code/find?q=${encodeURIComponent(q)}&limit=${limit}`);
}

/** Semantic search over embedded definitions. `building` means a reindex is in flight. */
export function searchSemantic(q: string, limit = 20): Promise<SemanticSearchResult> {
  return apiGet<SemanticSearchResult>(`/code/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

/** Kick a full background rebuild of the semantic index. */
export function reindexCode(): Promise<{ started: boolean }> {
  return apiPost<{ started: boolean }>('/code/reindex', {});
}
