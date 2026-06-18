/**
 * Frontend client for the workspace file API (backend/modules/files). All paths
 * are absolute and validated server-side against the configured workspace roots —
 * see docs/modules/file-explorer.md.
 */
import { apiGet, apiPost, apiPut } from '../../api';

export interface RootInfo {
  name: string;
  path: string;
}

export interface FileEntry {
  name: string;
  path: string;
  kind: 'file' | 'dir';
  size: number | null;
  mtime: number | null;
}

export function listRoots(): Promise<RootInfo[]> {
  return apiGet<RootInfo[]>('/files/roots');
}

export function listDir(path: string): Promise<{ path: string; entries: FileEntry[] }> {
  return apiGet(`/files/list?path=${encodeURIComponent(path)}`);
}

export type GitStatusKind = 'modified' | 'added' | 'deleted' | 'untracked' | 'renamed' | 'conflict';

export interface GitEntry {
  path: string;
  status: GitStatusKind;
}

export interface GitStatus {
  is_repo: boolean;
  root: string;
  branch: string | null;
  entries: GitEntry[];
}

/** Working-tree status for a workspace root (`is_repo:false` if not a repo). */
export function gitStatus(path: string): Promise<GitStatus> {
  return apiGet(`/files/git-status?path=${encodeURIComponent(path)}`);
}

export function readFile(
  path: string,
): Promise<{ path: string; content: string; truncated: boolean }> {
  return apiGet(`/files/read?path=${encodeURIComponent(path)}`);
}

export function writeFile(path: string, content: string): Promise<FileEntry> {
  return apiPut<FileEntry>('/files/write', { path, content });
}

export function createEntry(path: string, kind: 'file' | 'dir', content = ''): Promise<FileEntry> {
  return apiPost<FileEntry>('/files/create', { path, kind, content });
}

export function renameEntry(path: string, newPath: string): Promise<FileEntry> {
  return apiPost<FileEntry>('/files/rename', { path, new_path: newPath });
}

export function deleteEntry(path: string, recursive = false): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>('/files/delete', { path, recursive });
}

/** Join a directory and a child name with the directory's own separator. */
export function joinPath(dir: string, name: string): string {
  const sep = dir.includes('\\') ? '\\' : '/';
  return dir.endsWith(sep) ? `${dir}${name}` : `${dir}${sep}${name}`;
}

/** The parent directory of a path (its own separator). */
export function parentDir(path: string): string {
  const idx = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return idx <= 0 ? path : path.slice(0, idx);
}
