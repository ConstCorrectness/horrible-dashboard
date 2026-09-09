/**
 * **Source Control** — the working tree, grouped and actionable.
 *
 * Distinct from `git.provenance` next door, which answers "who wrote this line
 * and in which conversation". This answers "what have I changed and what am I
 * about to commit", which is the question you ask before every commit and the
 * one the app had no surface for.
 *
 * The commit itself goes through the git module's existing `commitChanges`, not
 * a second implementation: that one stamps the session provenance trailers the
 * blame view reads, and a commit made around it would be invisible there.
 */
import { useCallback, useEffect, useState } from 'react';

import { IconBranch, IconMinus, IconPlus } from '../../glyphs';
import { minibuffer } from '../../minibuffer';
import { openBuffer } from '../editor';
import { commitChanges } from '../git/api';
import { UnifiedDiff } from '../git';
import {
  fetchScmStatus,
  fetchWorkingDiff,
  stagePaths,
  unstagePaths,
  type ScmEntry,
  type ScmStatus,
} from './api';
import './ide.css';

/** The strip is narrow and git is quiet, so a slow poll while it is mounted is
 * the honest way to stay current. Cheap: one `git status` on a local repo. */
const POLL_MS = 5000;

interface Selected {
  path: string;
  staged: boolean;
}

export function ScmPane() {
  const [status, setStatus] = useState<ScmStatus | null>(null);
  const [message, setMessage] = useState('');
  const [selected, setSelected] = useState<Selected | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    fetchScmStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!selected) {
      setDiff(null);
      return;
    }
    let live = true;
    fetchWorkingDiff(selected.path, selected.staged)
      .then((res) => live && setDiff(res.diff))
      .catch(() => live && setDiff(''));
    return () => {
      live = false;
    };
  }, [selected]);

  const act = async (
    fn: (paths: string[]) => Promise<{ ok: boolean; error?: string }>,
    paths: string[],
  ) => {
    if (paths.length === 0) return;
    setBusy(true);
    try {
      const res = await fn(paths);
      if (!res.ok) minibuffer.say(res.error ?? 'git refused that', 'error');
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const commit = async () => {
    if (!message.trim()) return;
    setBusy(true);
    try {
      const res = await commitChanges(message.trim());
      if (res.ok) {
        setMessage('');
        minibuffer.say(`Committed ${res.sha ?? ''}`.trim());
      } else {
        minibuffer.say(res.error ?? 'commit failed', 'error');
      }
    } finally {
      setBusy(false);
      refresh();
    }
  };

  if (!status) return <p className="ide-panel-note">Reading the working tree…</p>;
  if (!status.isRepo) {
    return <p className="ide-panel-note">This workspace root is not a git repository.</p>;
  }

  const clean =
    status.staged.length +
      status.unstaged.length +
      status.untracked.length +
      status.conflicted.length ===
    0;

  return (
    <div className="ide-panel">
      <div className="ide-scm-head">
        <span className="ide-branch">
          <IconBranch />
          <span>{status.branch ?? 'detached'}</span>
        </span>
        {status.ahead > 0 ? <span className="ide-count">↑{status.ahead}</span> : null}
        {status.behind > 0 ? <span className="ide-count">↓{status.behind}</span> : null}
      </div>

      <div className="ide-scm-commit">
        {/* A textarea, not an input: it is exempt from the fixed control height
            precisely because it must grow, and a commit message has a body. */}
        <textarea
          className="ide-scm-message"
          rows={2}
          placeholder="Message"
          aria-label="Commit message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
        {/* Classless on purpose: that is the app's standard 30px action button
            (`button:not([class])` in controls.css). Its primary colouring comes
            from the descendant rule in ide.css, so it keeps the height. */}
        <button
          disabled={busy || !message.trim() || status.staged.length === 0}
          title={status.staged.length === 0 ? 'Stage something first' : 'Commit the staged changes'}
          onClick={() => void commit()}
        >
          Commit
        </button>
      </div>

      {clean ? <p className="ide-panel-note">No changes</p> : null}

      <div className="ide-results">
        <Group
          label="Merge Changes"
          entries={status.conflicted}
          selected={selected}
          staged={false}
          onSelect={setSelected}
          onStage={(paths) => void act(stagePaths, paths)}
        />
        <Group
          label="Staged Changes"
          entries={status.staged}
          selected={selected}
          staged
          onSelect={setSelected}
          onUnstage={(paths) => void act(unstagePaths, paths)}
        />
        <Group
          label="Changes"
          entries={status.unstaged}
          selected={selected}
          staged={false}
          onSelect={setSelected}
          onStage={(paths) => void act(stagePaths, paths)}
        />
        <Group
          label="Untracked"
          entries={status.untracked}
          selected={selected}
          staged={false}
          onSelect={setSelected}
          onStage={(paths) => void act(stagePaths, paths)}
        />
      </div>

      {selected ? (
        <div className="ide-scm-diff">
          <div className="ide-scm-diff-head">
            <span className="ide-result-name">{basename(selected.path)}</span>
            <button
              className="btn-mini"
              onClick={() => openBuffer(`workspace-file:${selected.path}`)}
            >
              Open file
            </button>
            <button className="btn-mini" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
          <UnifiedDiff
            diff={diff ?? ''}
            empty={diff === null ? 'Loading…' : 'No diff — the file is untracked or binary'}
          />
        </div>
      ) : null}
    </div>
  );
}

function Group({
  label,
  entries,
  staged,
  selected,
  onSelect,
  onStage,
  onUnstage,
}: {
  label: string;
  entries: ScmEntry[];
  staged: boolean;
  selected: Selected | null;
  onSelect: (s: Selected | null) => void;
  onStage?: (paths: string[]) => void;
  onUnstage?: (paths: string[]) => void;
}) {
  if (entries.length === 0) return null;
  const paths = entries.map((e) => e.path);
  return (
    <div className="ide-result-group">
      <div className="ide-scm-group">
        <span className="ide-scm-group-label">{label}</span>
        <span className="ide-count">{entries.length}</span>
        {onStage ? (
          <button className="btn-mini" title={`Stage all ${label}`} onClick={() => onStage(paths)}>
            <IconPlus />
          </button>
        ) : null}
        {onUnstage ? (
          <button
            className="btn-mini"
            title={`Unstage all ${label}`}
            onClick={() => onUnstage(paths)}
          >
            <IconMinus />
          </button>
        ) : null}
      </div>
      {entries.map((entry) => {
        const isSelected = selected?.path === entry.path && selected.staged === staged;
        return (
          <div key={`${label}:${entry.path}`} className="ide-scm-row-wrap">
            <button
              className={`ide-result-row ide-scm-row${isSelected ? ' ide-scm-row--on' : ''}`}
              title={entry.path}
              onClick={() => onSelect(isSelected ? null : { path: entry.path, staged })}
            >
              <span className={`ide-scm-badge ide-scm-badge--${entry.status}`}>
                {badge(entry, staged)}
              </span>
              <span className="ide-result-name">{basename(entry.path)}</span>
              <span className="ide-result-dir">{dirname(entry.path)}</span>
            </button>
            {onStage ? (
              <button
                className="btn-mini ide-scm-action"
                title="Stage this file"
                aria-label={`Stage ${basename(entry.path)}`}
                onClick={() => onStage([entry.path])}
              >
                <IconPlus />
              </button>
            ) : null}
            {onUnstage ? (
              <button
                className="btn-mini ide-scm-action"
                title="Unstage this file"
                aria-label={`Unstage ${basename(entry.path)}`}
                onClick={() => onUnstage([entry.path])}
              >
                <IconMinus />
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/**
 * The status letter — git's own, from the side of the change this row is showing.
 * Taking it from the wrong side is how a staged rename displays as a delete.
 */
function badge(entry: ScmEntry, staged: boolean): string {
  if (entry.status === 'untracked') return 'U';
  const char = staged ? entry.index : entry.worktree;
  return char === '.' || char === ' ' ? '·' : char;
}

function basename(path: string): string {
  return path.slice(Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')) + 1);
}

function dirname(path: string): string {
  return path.slice(0, Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')));
}
