/**
 * The `github.repo` pane: pick a repository, browse its tree, read its README.
 *
 * Files open as ordinary editor buffers via the `github:` source scheme rather than in
 * a viewer of their own — that inherits tabs, splits, find, and syntax highlighting
 * instead of reimplementing them. So this pane owns only navigation.
 *
 * Layout is an in-pane grid, not a docked region: the tree is bound to *this* pane's
 * repo and ref, and a registered region view would be a separate instance needing a
 * channel to stay in sync. Two repos open = two self-contained panes.
 */
import { useEffect, useSyncExternalStore } from 'react';

import { ConnectionGate } from '../../connectors/ConnectionGate';
import { openBuffer } from '../editor';
import { githubUri, splitRepo, type RepoSummary } from './api';
import { ReadmePane } from './ReadmePane';
import { RepoPicker } from './RepoPicker';
import { RepoTree } from './RepoTree';
import {
  clearRepo,
  disposeState,
  getState,
  openRepo,
  rowsFor,
  setActivePath,
  storeVersion,
  subscribe,
  switchRef,
  toggleExpanded,
} from './store';

export function RepoViewer({ instanceId }: { instanceId?: string }) {
  const paneId = instanceId ?? 'github.repo';
  useSyncExternalStore(subscribe, storeVersion);
  const state = getState(paneId);

  // Drop this pane's repo when it closes, so a reopened pane starts at the picker
  // rather than inheriting a tree the user may have finished with.
  useEffect(() => () => disposeState(paneId), [paneId]);

  if (!state.repo) {
    return (
      <div className="gh-viewer">
        {/* Without the connector this pane can only fail: the picker's first call
            returns 409 and it renders the raw message, which tells the user what
            went wrong but not what to do. The gate offers the connect flow
            instead — the same one the home tile opens, reached through
            `requestConnect` so this module owns no connect UI of its own. */}
        <ConnectionGate connector="github">
          <RepoPicker onPick={(repo: RepoSummary) => void openRepo(paneId, repo)} />
        </ConnectionGate>
      </div>
    );
  }

  const parts = splitRepo(state.repo.full_name);
  if (!parts) return null;
  const rows = rowsFor(paneId);

  const openFile = (path: string) => {
    setActivePath(paneId, path);
    openBuffer(githubUri(parts.owner, parts.repo, state.ref, path));
  };

  return (
    <div className="gh-viewer">
      <header className="gh-header">
        <button
          className="gh-back"
          onClick={() => clearRepo(paneId)}
          title="Choose another repository"
        >
          ←
        </button>
        <strong className="gh-header-name">{state.repo.full_name}</strong>
        {state.branches.length > 0 && (
          <select
            className="gh-ref"
            value={state.ref}
            onChange={(e) => switchRef(paneId, e.target.value)}
            title="Branch"
          >
            {state.branches.map((branch) => (
              <option key={branch} value={branch}>
                {branch}
              </option>
            ))}
          </select>
        )}
        {state.repo.url && (
          <a className="gh-open-external" href={state.repo.url} target="_blank" rel="noreferrer">
            ↗
          </a>
        )}
      </header>

      {state.error && <p className="widget-error">{state.error}</p>}
      {state.lazy && (
        <p className="home-hint gh-lazy-note">
          This repository is too large to fetch at once — folders load as you open them.
        </p>
      )}

      <div className="gh-body">
        <div className="gh-sidebar">
          {state.loading ? (
            <p className="home-hint gh-tree-empty">Loading tree…</p>
          ) : (
            <RepoTree
              rows={rows}
              expanded={state.expanded}
              activePath={state.activePath}
              onToggle={(path) => toggleExpanded(paneId, path)}
              onOpenFile={openFile}
            />
          )}
        </div>
        <div className="gh-content">
          <ReadmePane owner={parts.owner} repo={parts.repo} refName={state.ref} />
        </div>
      </div>
    </div>
  );
}
