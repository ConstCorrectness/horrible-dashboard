/** Backend client for the git provenance module. See backend/modules/git/routes.py. */
import { apiGet, apiPost } from '../../api';
import type { BlameResult, CommitResult, DiffResult, LogResult } from './types';

/** Per-line blame for a file, each line tagged with the session that wrote its commit. */
export function fetchBlame(path: string): Promise<BlameResult> {
  return apiGet<BlameResult>(`/git/blame?path=${encodeURIComponent(path)}`);
}

/** Recent commits; a `session_id` marks a commit agent-authored. */
export function fetchLog(limit = 30): Promise<LogResult> {
  return apiGet<LogResult>(`/git/log?limit=${limit}`);
}

/** A commit's metadata + unified diff. */
export function fetchShow(sha: string): Promise<DiffResult> {
  return apiGet<DiffResult>(`/git/show?sha=${encodeURIComponent(sha)}`);
}

/** Stage + commit, stamping the active conversation as a provenance trailer. */
export function commitChanges(message: string, paths?: string[]): Promise<CommitResult> {
  return apiPost<CommitResult>('/git/commit', paths ? { message, paths } : { message });
}
