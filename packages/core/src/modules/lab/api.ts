import { apiGet } from '../../api';

/** One search hit. Mirrors the backend `RepoHit`. */
export interface RepoHit {
  id: string;
  type: string;
  private: boolean | null;
  downloads: number | null;
  likes: number | null;
  updated_at: string | null;
  /** Models only — the pipeline tag. Nothing for datasets. */
  task: string | null;
  tags: string[];
  url: string | null;
}

export interface RepoInfo extends RepoHit {
  files: string[];
  /**
   * Three-state, deliberately not a boolean: `true` / `"auto"` / `"manual"` all
   * mean a licence must be accepted on the Hub, `false` means open, and `null`
   * means the Hub didn't say. "We don't know" is not "it's open".
   */
  gated: boolean | string | null;
  library: string | null;
}

export interface RepoFile {
  repo: string;
  type: string;
  path: string;
  revision: string;
  content: string;
  truncated: boolean;
  url: string | null;
}

export type RepoKind = 'model' | 'dataset';

const BASE = '/connectors/huggingface';

function qs(params: Record<string, string | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '' || value === false) continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export function searchRepos(
  query: string,
  type: RepoKind,
  sort: string,
  fresh = false,
): Promise<{ results: RepoHit[] }> {
  return apiGet(`${BASE}/search${qs({ q: query, type, sort, fresh })}`);
}

export function myRepos(type: RepoKind, fresh = false): Promise<{ results: RepoHit[] }> {
  return apiGet(`${BASE}/mine${qs({ type, fresh })}`);
}

export function repoInfo(repo: string, type: RepoKind): Promise<RepoInfo> {
  return apiGet(`${BASE}/repo${qs({ repo, type })}`);
}

export function repoFile(repo: string, path: string, type: RepoKind): Promise<RepoFile> {
  return apiGet(`${BASE}/file${qs({ repo, path, type })}`);
}

/**
 * Files worth a one-click open, in the order a person actually wants them.
 *
 * A repo lists up to 60 filenames, most of them weight shards nobody can read. The
 * card and the config are the two that answer "what is this and will it fit", so
 * they are surfaced as buttons rather than left to be found in the list.
 */
export const NOTABLE_FILES = [
  'README.md',
  'config.json',
  'generation_config.json',
  'tokenizer_config.json',
  'params.json',
  'adapter_config.json',
] as const;

/** True for a file we can actually display — everything else is weights. */
export function isReadable(path: string): boolean {
  return /\.(md|json|txt|ya?ml|py|jsonl|csv|tsv|toml|cfg)$/i.test(path);
}
