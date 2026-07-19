/**
 * Client for the GitHub repo viewer (`/api/connectors/github/*`).
 *
 * Types mirror `backend/modules/connectors/providers/github_routes.py`, which is the
 * source of truth. The token lives server-side — nothing here ever sees it.
 */
import { apiGet } from '../../api';

export interface RepoSummary {
  full_name: string;
  description: string | null;
  private: boolean;
  language: string | null;
  stars: number;
  default_branch: string;
  updated_at: string | null;
  url: string | null;
}

export interface TreeEntry {
  path: string;
  kind: 'file' | 'dir';
  size: number | null;
}

export interface TreeResponse {
  ref: string;
  entries: TreeEntry[];
  /** GitHub gave up on a repo this large — fall back to per-directory listing. */
  truncated: boolean;
}

const repoPath = (owner: string, repo: string) =>
  `/connectors/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;

/** Split `owner/name` into its halves. */
export function splitRepo(fullName: string): { owner: string; repo: string } | null {
  const [owner, repo] = fullName.split('/');
  return owner && repo ? { owner, repo } : null;
}

export function listRepos(fresh = false): Promise<RepoSummary[]> {
  return apiGet<RepoSummary[]>(`/connectors/github/repos${fresh ? '?fresh=true' : ''}`);
}

export function searchRepos(q: string): Promise<RepoSummary[]> {
  return apiGet<RepoSummary[]>(`/connectors/github/search/repos?q=${encodeURIComponent(q)}`);
}

export function getRepo(owner: string, repo: string): Promise<RepoSummary> {
  return apiGet<RepoSummary>(repoPath(owner, repo));
}

export function listBranches(owner: string, repo: string): Promise<string[]> {
  return apiGet<string[]>(`${repoPath(owner, repo)}/branches`);
}

export function getTree(owner: string, repo: string, ref: string): Promise<TreeResponse> {
  return apiGet<TreeResponse>(`${repoPath(owner, repo)}/tree?ref=${encodeURIComponent(ref)}`);
}

export function listContents(
  owner: string,
  repo: string,
  path: string,
  ref: string,
): Promise<{ path: string; entries: TreeEntry[] }> {
  return apiGet(
    `${repoPath(owner, repo)}/contents?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`,
  );
}

export function getReadme(owner: string, repo: string, ref: string): Promise<{ content: string }> {
  return apiGet(`${repoPath(owner, repo)}/readme?ref=${encodeURIComponent(ref)}`);
}

/**
 * The editor source URI for a file in a repo. The editor resolves this scheme itself
 * (see modules/editor/sources.ts), so opening a repo file reuses the whole buffer
 * stack — tabs, splits, find, syntax highlighting — instead of a bespoke viewer.
 */
export function githubUri(owner: string, repo: string, ref: string, path: string): string {
  return `github:${owner}/${repo}@${ref}/${path}`;
}
