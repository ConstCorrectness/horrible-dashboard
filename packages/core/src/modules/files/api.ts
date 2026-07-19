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

/**
 * A path belonging to a **virtual root** (`gdrive:/…`) rather than the filesystem.
 *
 * Two or more characters before the colon, deliberately: `C:/Users/x` is a Windows
 * path, not a URI, and a one-character scheme would classify every file on a Windows
 * root as virtual. Mirrors `_SCHEME` in backend/modules/files/providers.py.
 *
 * Virtual roots are read-only, so this is also what gates rename/delete/save in the UI.
 */
const VIRTUAL_SCHEME = /^[a-z][a-z0-9+.-]+:\//;

export function isVirtualPath(path: string): boolean {
  return VIRTUAL_SCHEME.test(path);
}

/**
 * The editor source URI for a tree row. A virtual path is already a URI (`gdrive:/…`)
 * and the editor dispatches on its scheme; a filesystem path needs the
 * `workspace-file:` prefix to become one.
 */
export function bufferUriFor(path: string): string {
  return isVirtualPath(path) ? path : `workspace-file:${path}`;
}

export function listRoots(): Promise<RootInfo[]> {
  return apiGet<RootInfo[]>('/files/roots');
}

/** List a directory. `fresh` bypasses a virtual root's cache (no effect locally). */
export function listDir(
  path: string,
  fresh = false,
): Promise<{ path: string; entries: FileEntry[] }> {
  const q = fresh ? '&fresh=true' : '';
  return apiGet(`/files/list?path=${encodeURIComponent(path)}${q}`);
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
