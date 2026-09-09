/**
 * Backend clients for the IDE's own panels. The types mirror the Pydantic models
 * they come from (`backend/modules/files/models.py`,
 * `backend/modules/git/models.py`), which is the repo's convention — the backend
 * is the source of truth for a shared shape.
 */
import { apiGet, apiPost } from '../../api';

// ------------------------------------------------------------------ search --

export interface SearchRequest {
  query: string;
  root?: string;
  regex?: boolean;
  caseSensitive?: boolean;
  wholeWord?: boolean;
  include?: string[];
  exclude?: string[];
  maxResults?: number;
}

export interface SearchMatch {
  path: string;
  /** 1-based, as an editor shows it. */
  line: number;
  column: number;
  text: string;
  /** 0-based offsets **into `text`**, which the backend may have trimmed. */
  matchStart: number;
  matchEnd: number;
}

export interface SearchFileResult {
  path: string;
  matches: SearchMatch[];
}

export interface SearchResult {
  /** `ripgrep`, `python`, or `none`. */
  engine: string;
  files: SearchFileResult[];
  total: number;
  /** Hit the result cap. Distinct from an empty tail. */
  truncated: boolean;
  /** Hit the wall clock. Also distinct from an empty tail. */
  timedOut: boolean;
  error: string | null;
}

/** Wire shapes — snake_case, as FastAPI serializes them. */
interface WireMatch {
  path: string;
  line: number;
  column: number;
  text: string;
  match_start: number;
  match_end: number;
}
interface WireResult {
  engine: string;
  files: { path: string; matches: WireMatch[] }[];
  total: number;
  truncated: boolean;
  timed_out: boolean;
  error: string | null;
}

export async function searchFiles(req: SearchRequest, signal?: AbortSignal): Promise<SearchResult> {
  const wire = await apiPost<WireResult>(
    '/files/search',
    {
      query: req.query,
      root: req.root,
      regex: req.regex ?? false,
      case_sensitive: req.caseSensitive ?? false,
      whole_word: req.wholeWord ?? false,
      include: req.include ?? [],
      exclude: req.exclude ?? [],
      ...(req.maxResults ? { max_results: req.maxResults } : {}),
    },
    signal,
  );
  return {
    engine: wire.engine,
    total: wire.total,
    truncated: wire.truncated,
    timedOut: wire.timed_out,
    error: wire.error,
    files: wire.files.map((file) => ({
      path: file.path,
      matches: file.matches.map((m) => ({
        path: m.path,
        line: m.line,
        column: m.column,
        text: m.text,
        matchStart: m.match_start,
        matchEnd: m.match_end,
      })),
    })),
  };
}

// --------------------------------------------------------------------- scm --

/** One changed path, with git's two status characters kept apart. */
export interface ScmEntry {
  path: string;
  /** git's X character — the index (staged) side. */
  index: string;
  /** git's Y character — the working-tree (unstaged) side. */
  worktree: string;
  /** The collapsed category, for the icon. */
  status: string;
  origPath: string | null;
}

export interface ScmStatus {
  isRepo: boolean;
  root: string;
  branch: string | null;
  ahead: number;
  behind: number;
  staged: ScmEntry[];
  unstaged: ScmEntry[];
  untracked: ScmEntry[];
  conflicted: ScmEntry[];
}

interface WireScmEntry {
  path: string;
  index: string;
  worktree: string;
  status: string;
  orig_path: string | null;
}
interface WireScmStatus {
  is_repo: boolean;
  root: string;
  branch: string | null;
  ahead: number;
  behind: number;
  staged: WireScmEntry[];
  unstaged: WireScmEntry[];
  untracked: WireScmEntry[];
  conflicted: WireScmEntry[];
}

function entry(w: WireScmEntry): ScmEntry {
  return {
    path: w.path,
    index: w.index,
    worktree: w.worktree,
    status: w.status,
    origPath: w.orig_path,
  };
}

export async function fetchScmStatus(path?: string): Promise<ScmStatus> {
  const query = path ? `?path=${encodeURIComponent(path)}` : '';
  const w = await apiGet<WireScmStatus>(`/git/scm-status${query}`);
  return {
    isRepo: w.is_repo,
    root: w.root,
    branch: w.branch,
    ahead: w.ahead,
    behind: w.behind,
    staged: w.staged.map(entry),
    unstaged: w.unstaged.map(entry),
    untracked: w.untracked.map(entry),
    conflicted: w.conflicted.map(entry),
  };
}

/** The working-tree (or, with `staged`, the index) diff for one path. */
export function fetchWorkingDiff(path: string, staged = false): Promise<{ diff: string }> {
  return apiGet<{ diff: string }>(
    `/git/diff?path=${encodeURIComponent(path)}&staged=${staged ? 1 : 0}`,
  );
}

export function stagePaths(paths: string[]): Promise<{ ok: boolean; error?: string }> {
  return apiPost('/git/stage', { paths });
}

export function unstagePaths(paths: string[]): Promise<{ ok: boolean; error?: string }> {
  return apiPost('/git/unstage', { paths });
}
