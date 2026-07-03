/**
 * Settings-page section listing the **indexed framework packages** and the pip
 * **version** each is resolved at for the active Python interpreter — so it's clear
 * which library versions intellisense (basedpyright + the framework-import source) is
 * running against. Versions come from `/api/editor/python-env` (cached per interpreter,
 * shared by every buffer). A **snapshot** of the versions is persisted (JSON in the
 * scalar settings store) so drift after an install/upgrade is flagged; "Update
 * snapshot" re-resolves and records the current versions. See docs/modules/editor.md.
 */
import { useCallback, useEffect, useState } from 'react';

import { getSetting, setSetting } from '../../settings';
import { getActiveBufferSource } from './index';
import { dirOf } from './lsp';
import { fetchPythonEnv, invalidatePythonEnv, type PythonEnv } from './pythonEnv';
import { FRAMEWORK_PACKAGE_NAMES } from './pythonImports';

const FILE_URI = 'workspace-file:';
const SNAPSHOT_KEY = 'editor.indexedPackages';

interface Snapshot {
  interpreter: string | null;
  packages: Record<string, string>;
}

/** The persisted version snapshot (JSON string in the scalar store), or null. */
function readSnapshot(): Snapshot | null {
  const raw = getSetting<string>(SNAPSHOT_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Snapshot;
  } catch {
    return null;
  }
}

/** Directory to resolve the interpreter from: the active Python buffer's folder, else
 * empty (the backend then falls back to the system interpreter). */
function activeDir(): string {
  const source = getActiveBufferSource();
  if (source && source.startsWith(FILE_URI) && source.endsWith('.py')) {
    return dirOf(source.slice(FILE_URI.length));
  }
  return '';
}

export function IndexedPackages() {
  const [dir] = useState(activeDir);
  const [env, setEnv] = useState<PythonEnv | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(readSnapshot);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void fetchPythonEnv(dir).then(setEnv);
  }, [dir]);
  useEffect(load, [load]);

  const updateSnapshot = async (): Promise<void> => {
    setBusy(true);
    invalidatePythonEnv(dir);
    const fresh = await fetchPythonEnv(dir);
    setEnv(fresh);
    const snap: Snapshot = { interpreter: fresh.interpreter, packages: fresh.packages };
    await setSetting(SNAPSHOT_KEY, JSON.stringify(snap));
    setSnapshot(snap);
    setBusy(false);
  };

  const live = env?.packages ?? {};
  const drift = FRAMEWORK_PACKAGE_NAMES.some((p) => {
    const s = snapshot?.packages[p];
    return s !== undefined && s !== live[p];
  });

  return (
    <div className="indexed-packages">
      <div className="setting-row">
        <div className="setting-label">
          <label>Indexed packages</label>
          <p className="setting-desc">
            Framework versions intellisense resolves against for{' '}
            <code>{env?.interpreter ?? 'the default interpreter'}</code>
            {env?.root ? (
              <>
                {' '}
                (project <code>{env.root}</code>)
              </>
            ) : null}
            .
          </p>
        </div>
        <button className="setting-button" onClick={() => void updateSnapshot()} disabled={busy}>
          {busy ? 'Reindexing…' : 'Update snapshot'}
        </button>
      </div>

      {drift ? (
        <p className="indexed-packages-drift">
          ⚠ Installed versions differ from the saved snapshot — reindex to update.
        </p>
      ) : null}

      <table className="indexed-packages-table">
        <thead>
          <tr>
            <th>Package</th>
            <th>Installed</th>
            <th>Snapshot</th>
          </tr>
        </thead>
        <tbody>
          {FRAMEWORK_PACKAGE_NAMES.map((pkg) => {
            const version = live[pkg];
            const snap = snapshot?.packages[pkg];
            const changed = snap !== undefined && snap !== version;
            return (
              <tr key={pkg} className={version ? '' : 'indexed-packages-missing'}>
                <td>{pkg}</td>
                <td>{version ?? 'not installed'}</td>
                <td className={changed ? 'indexed-packages-changed' : ''}>{snap ?? '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
