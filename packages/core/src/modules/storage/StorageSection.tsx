/**
 * Settings-page section: **where this node keeps its files**, and why there.
 *
 * The question this answers is "where did my models go" — asked after a 300 MB
 * llama.cpp build, a 20 GB GGUF or a week of traces appears to have vanished.
 * Before `backend/paths.py` the honest answer was "it depends which launcher
 * started the backend", and there was nowhere in the app to read it.
 *
 * Two things it renders that are not decoration:
 *
 * - **The source of each root**, not just its path. `environment` / `checkout` /
 *   `platform` is the difference between something the user can change and a
 *   property of their install, and a bare path cannot distinguish them.
 * - **"Not created yet" as a normal state.** A root appears on first write, so an
 *   absent cache dir is not an error — but the Open button must not offer to show
 *   a folder that isn't there, because the shell would reject it and the click
 *   would silently do nothing.
 *
 * See docs/architecture/data-directories.mdx.
 */
import { useCallback, useEffect, useState } from 'react';

import { isDesktopShell, openPath } from '../../external';
import { getPaths, type RootSource, type StoragePaths, type StorageRoot } from './api';

const SOURCE_LABEL: Record<RootSource, string> = {
  environment: 'set by environment',
  checkout: 'checkout',
  platform: 'system default',
};

function sourceExplanation(root: StorageRoot): string {
  switch (root.source) {
    case 'environment':
      return `${root.envVar} is set, which overrides everything else.`;
    case 'checkout':
      // Deliberately not "…so your existing data is already there": true of the
      // data dir, meaningless for a cache that has never been written.
      return 'Running from a git checkout, so this stays in the tree rather than moving to a system location.';
    default:
      return `The convention for this OS. Override it with ${root.envVar}.`;
  }
}

function RootRow({ root }: { root: StorageRoot }) {
  // Three states, not two: untried, copied, and "the clipboard refused" — which
  // happens without a secure context and must not look like success.
  const [copied, setCopied] = useState<boolean | null>(null);
  const [openFailed, setOpenFailed] = useState(false);

  const copy = () => {
    void navigator.clipboard
      .writeText(root.path)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };

  const reveal = () => {
    setOpenFailed(false);
    void openPath(root.path).then((opened) => {
      if (!opened) setOpenFailed(true);
    });
  };

  return (
    <div className="storage-root">
      <div className="storage-root-head">
        <span className="storage-root-title">{root.title}</span>
        <span className={`storage-source storage-source--${root.source}`}>
          {SOURCE_LABEL[root.source]}
        </span>
        {!root.exists && <span className="storage-missing">not created yet</span>}
      </div>

      <code className="storage-path">{root.path}</code>

      <div className="storage-actions">
        {/* Desktop only: a web page cannot open a file manager, and a dead button
            is worse than an absent one. */}
        {isDesktopShell() && (
          <button
            type="button"
            className="setting-button"
            onClick={reveal}
            disabled={!root.exists}
            title={root.exists ? 'Show in file manager' : 'This folder does not exist yet'}
          >
            Open
          </button>
        )}
        <button type="button" className="setting-button" onClick={copy}>
          {copied === true ? 'Copied' : copied === false ? 'Copy failed' : 'Copy path'}
        </button>
      </div>

      {openFailed && (
        <p className="storage-warn">
          The file manager did not open. The path above is still correct — copy it instead.
        </p>
      )}

      <p className="setting-desc storage-note">{root.note}</p>
      <p className="setting-desc storage-why">{sourceExplanation(root)}</p>
    </div>
  );
}

export function StorageSection() {
  const [paths, setPaths] = useState<StoragePaths | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setError('');
    void getPaths()
      .then(setPaths)
      .catch((exc: unknown) => setError(String(exc)));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="storage-section">
      <div className="setting-row">
        <div className="setting-label">
          <label>Locations</label>
          <p className="setting-desc">
            Where this node keeps its files. An app update never touches the data directory —
            llama.cpp builds, GGUFs and traces are versioned independently of the app and none of
            them is reinstallable in a reasonable time.
          </p>
        </div>
        <button className="setting-button" onClick={load}>
          Refresh
        </button>
      </div>

      {error ? <p className="machine-error">⚠ {error}</p> : null}

      {paths ? (
        <>
          <div className="storage-roots">
            {paths.roots.map((root) => (
              <RootRow key={root.id} root={root} />
            ))}
          </div>
          <p className="setting-desc storage-origin">
            {paths.repo ? (
              <>
                Running from the checkout at <code>{paths.repo}</code>.
              </>
            ) : (
              'Packaged install — the per-OS locations above are in use.'
            )}
          </p>
        </>
      ) : (
        !error && <p className="setting-desc">Resolving…</p>
      )}
    </div>
  );
}
