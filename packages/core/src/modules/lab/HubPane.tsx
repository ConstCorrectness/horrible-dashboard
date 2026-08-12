import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '../../api';
import { usePaneSection } from '../../layout/use-sections';
import { setSetting } from '../../settings';
import {
  isReadable,
  myRepos,
  NOTABLE_FILES,
  repoFile,
  repoInfo,
  searchRepos,
  type RepoFile,
  type RepoHit,
  type RepoInfo,
  type RepoKind,
} from './api';

/**
 * Browse the Hugging Face Hub — models in one section, datasets in the other.
 *
 * The connector has had `huggingface.searchModels` / `searchDatasets` / `repoInfo` /
 * `readFile` since it landed, so the *agent* could always find a dataset and read
 * its card. A person could not. This is the same four capabilities addressed to a
 * human, over the same backend helpers, so the two views cannot drift apart.
 *
 * The section is the `type` — one component, not two — because a model repo and a
 * dataset repo differ in exactly one field (`task`) and nothing else about looking
 * at them changes.
 */

function fmtCount(n: number | null): string {
  if (n == null) return '—';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

function fmtDate(iso: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString();
}

/** `null` from the Hub means "not stated" and must not read as "open". */
function gatedLabel(gated: RepoInfo['gated']): string | null {
  if (gated === null || gated === undefined) return null;
  if (gated === false) return null;
  return gated === true ? 'gated' : `gated (${gated})`;
}

interface Failure {
  message: string;
  /** 409 — the connector isn't set up, which is the user's to fix and worth a
   *  different prompt from "the Hub is down". */
  needsConnect: boolean;
}

function toFailure(error: unknown): Failure {
  const status = error instanceof ApiError ? error.status : 0;
  return {
    message: error instanceof Error ? error.message : String(error),
    needsConnect: status === 409,
  };
}

function RepoRow({
  hit,
  selected,
  onSelect,
}: {
  hit: RepoHit;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button className={`lab-hit${selected ? ' lab-hit-on' : ''}`} onClick={onSelect}>
      <span className="lab-hit-id">{hit.id}</span>
      <span className="lab-hit-meta">
        {hit.task && <span className="lab-tag">{hit.task}</span>}
        <span title="downloads">↓ {fmtCount(hit.downloads)}</span>
        <span title="likes">♥ {fmtCount(hit.likes)}</span>
        {hit.private && <span className="lab-tag">private</span>}
      </span>
    </button>
  );
}

export function LabHub() {
  const { section } = usePaneSection();
  const kind: RepoKind = section === 'datasets' ? 'dataset' : 'model';

  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('downloads');
  const [hits, setHits] = useState<RepoHit[]>([]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [info, setInfo] = useState<RepoInfo | null>(null);
  const [file, setFile] = useState<RepoFile | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);

  // A slow search that resolves after a newer one must not overwrite it. Every
  // request carries a sequence number and a stale reply is dropped — the classic
  // "results flick back to the previous query" bug.
  const seq = useRef(0);

  const run = useCallback(async (loader: () => Promise<{ results: RepoHit[] }>) => {
    const ticket = ++seq.current;
    setBusy(true);
    setFailure(null);
    try {
      const res = await loader();
      if (seq.current !== ticket) return;
      setHits(res.results);
    } catch (error) {
      if (seq.current !== ticket) return;
      setFailure(toFailure(error));
      setHits([]);
    } finally {
      if (seq.current === ticket) setBusy(false);
    }
  }, []);

  // Switching section switches repo type, so the previous section's results and
  // selection are meaningless here. Clearing beats showing models under "Datasets".
  useEffect(() => {
    seq.current++;
    setHits([]);
    setSelected(null);
    setInfo(null);
    setFile(null);
    setFailure(null);
  }, [kind]);

  useEffect(() => {
    if (!selected) {
      setInfo(null);
      setFile(null);
      return;
    }
    let live = true;
    setFile(null);
    setFileError(null);
    repoInfo(selected, kind)
      .then((res) => live && setInfo(res))
      .catch((error) => live && setFailure(toFailure(error)));
    return () => {
      live = false;
    };
  }, [selected, kind]);

  const openFile = useCallback(
    (path: string) => {
      if (!selected) return;
      setFile(null);
      setFileError(null);
      repoFile(selected, path, kind)
        .then(setFile)
        .catch((error) => setFileError(toFailure(error).message));
    },
    [selected, kind],
  );

  /**
   * Pin this repo as the one describing the loaded model.
   *
   * The link back to the model explorer: `interpretability.modelRepo` drives both
   * exact token counts and the architecture description, and until now finding the
   * right repo id meant leaving the app. Models only — a dataset describes nothing
   * about the running model.
   */
  const pinAsModelRepo = useCallback(async () => {
    if (!selected) return;
    await setSetting('interpretability.modelRepo', selected);
    setPinned(selected);
  }, [selected]);

  const notable = (info?.files ?? []).filter((f) =>
    (NOTABLE_FILES as readonly string[]).includes(f),
  );
  const readable = (info?.files ?? []).filter((f) => isReadable(f) && !notable.includes(f));
  const gated = info ? gatedLabel(info.gated) : null;

  return (
    <div className="lab-hub">
      <form
        className="lab-search"
        onSubmit={(event) => {
          event.preventDefault();
          if (query.trim()) void run(() => searchRepos(query.trim(), kind, sort));
        }}
      >
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={kind === 'dataset' ? 'Search datasets…' : 'Search models…'}
          aria-label={kind === 'dataset' ? 'Search datasets' : 'Search models'}
        />
        <select value={sort} onChange={(event) => setSort(event.target.value)} title="Sort by">
          <option value="downloads">downloads</option>
          <option value="likes">likes</option>
          <option value="lastModified">updated</option>
        </select>
        <button type="submit" disabled={!query.trim() || busy}>
          Search
        </button>
        <button type="button" onClick={() => void run(() => myRepos(kind))} disabled={busy}>
          Mine
        </button>
      </form>

      {failure && (
        <div className="interp-warn">
          {failure.needsConnect ? (
            <>
              Hugging Face isn&apos;t connected. Connect it from the home page — the token stays on
              this node and is never handed to the browser.
            </>
          ) : (
            failure.message
          )}
        </div>
      )}

      <div className="lab-body">
        <div className="lab-results">
          {busy && <div className="interp-dim">Searching…</div>}
          {!busy && hits.length === 0 && !failure && (
            <div className="interp-dim lab-hint">
              {kind === 'dataset'
                ? 'Search the Hub for a dataset, or list your own with “Mine”.'
                : 'Search the Hub for a model, or list your own with “Mine”. Pin one as the model repo to drive exact token counts and the model explorer.'}
            </div>
          )}
          {hits.map((hit) => (
            <RepoRow
              key={hit.id}
              hit={hit}
              selected={hit.id === selected}
              onSelect={() => setSelected(hit.id)}
            />
          ))}
        </div>

        <div className="lab-detail">
          {!selected && <div className="interp-dim lab-hint">Select a repo to inspect it.</div>}
          {selected && (
            <>
              <div className="lab-detail-head">
                <b>{selected}</b>
                {gated && (
                  <span
                    className="interp-warn-chip"
                    title="A licence must be accepted on the Hub before this repo can be downloaded."
                  >
                    {gated}
                  </span>
                )}
                {info?.library && <span className="lab-tag">{info.library}</span>}
                {info?.updated_at && (
                  <span className="interp-dim">updated {fmtDate(info.updated_at)}</span>
                )}
              </div>

              <div className="lab-actions">
                {info?.url && (
                  <a href={info.url} target="_blank" rel="noreferrer noopener">
                    Open on the Hub ↗
                  </a>
                )}
                {kind === 'model' && (
                  <button type="button" onClick={() => void pinAsModelRepo()}>
                    {pinned === selected ? '✓ Pinned as model repo' : 'Pin as model repo'}
                  </button>
                )}
              </div>

              {info && (
                <div className="lab-files">
                  {notable.map((path) => (
                    <button
                      key={path}
                      className="lab-file lab-file-key"
                      onClick={() => openFile(path)}
                    >
                      {path}
                    </button>
                  ))}
                  {readable.map((path) => (
                    <button key={path} className="lab-file" onClick={() => openFile(path)}>
                      {path}
                    </button>
                  ))}
                  {info.files.length === 0 && (
                    <span className="interp-dim">No files listed for this repo.</span>
                  )}
                </div>
              )}

              {fileError && <div className="interp-warn">{fileError}</div>}
              {file && (
                <pre className="lab-filebody">
                  {file.content}
                  {file.truncated && (
                    <span className="interp-dim">{'\n\n… truncated for display'}</span>
                  )}
                </pre>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
