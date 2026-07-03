/**
 * The resolved Python environment for a directory — interpreter, project root, and
 * installed framework-package versions — fetched from `/api/editor/python-env` and
 * memoized per directory (the backend caches too). Powers the framework-import
 * gate/version labels, the interpreter reported to basedpyright, the per-project
 * server pool key (see lsp.ts), and the indexed-packages settings panel.
 */
import { apiGet } from '../../api';

export interface PythonEnv {
  /** Interpreter basedpyright should analyze against, or null for its default. */
  interpreter: string | null;
  /** Project root (nearest marker up-tree) — anchors the LSP root and server pool. */
  root: string;
  /** Installed framework packages: pip dist name → version (installed only). */
  packages: Record<string, string>;
}

const cache = new Map<string, Promise<PythonEnv>>();

/** The Python environment for `dir`, memoized. Never rejects — a failed probe resolves
 * to an empty env so completions still work (framework suggestions just don't gate). */
export function fetchPythonEnv(dir: string): Promise<PythonEnv> {
  let pending = cache.get(dir);
  if (!pending) {
    pending = apiGet<PythonEnv>(`/editor/python-env?path=${encodeURIComponent(dir)}`).catch(
      (): PythonEnv => ({ interpreter: null, root: dir, packages: {} }),
    );
    cache.set(dir, pending);
  }
  return pending;
}

/** Drop the memoized env (one dir, or all) so the next fetch re-resolves — used when the
 * indexed-packages panel reindexes after an install/upgrade. */
export function invalidatePythonEnv(dir?: string): void {
  if (dir) cache.delete(dir);
  else cache.clear();
}
