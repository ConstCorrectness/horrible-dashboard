/** Shapes from the backend git module (`/api/git/*`). Mirrors backend/modules/git/models.py. */

export interface BlameLine {
  line: number;
  commit: string; // short sha
  author: string;
  summary: string;
  session_id?: string | null; // the conversation that authored this line
  session_title?: string | null;
  text?: string | null;
}
export interface BlameResult {
  is_repo: boolean;
  path: string;
  root?: string | null;
  lines: BlameLine[];
}
export interface CommitInfo {
  sha: string;
  author: string;
  date: string;
  summary: string;
  session_id?: string | null; // set ⇒ agent-authored
  session_title?: string | null;
}
export interface LogResult {
  is_repo: boolean;
  commits: CommitInfo[];
}
export interface DiffResult {
  sha: string;
  diff: string;
}
export interface CommitResult {
  ok: boolean;
  sha?: string | null;
  session_id?: string | null;
  session_title?: string | null;
  error?: string | null;
}
