/**
 * Git provenance pane. **Blame** follows the code locus (core/locus.ts): a row per
 * line with author, short sha, and — for commits the agent authored — a **session
 * chip** that opens the conversation that wrote it. **History** lists recent commits,
 * flags agent-authored ones, and shows a commit's diff on select. This is the
 * agentic-native git view: line → the conversation that produced it. See
 * docs/modules/git.mdx.
 */
import { useEffect, useState } from 'react';

import { openChatSession } from '../agent/openSession';
import { setLocus, useLocus } from '../../locus';
import { fetchBlame, fetchLog, fetchShow } from './api';
import type { BlameResult, CommitInfo, DiffResult, LogResult } from './types';
import './git.css';

type Tab = 'blame' | 'history';

function basename(p: string): string {
  const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
  return i >= 0 ? p.slice(i + 1) : p;
}

function SessionChip({ id, title }: { id: string; title?: string | null }) {
  return (
    <button
      type="button"
      className="git-session-chip"
      title={`From conversation "${title ?? id}" — click to open it`}
      onClick={(e) => {
        e.stopPropagation();
        openChatSession(id);
      }}
    >
      ⤳ {title ?? id.slice(0, 8)}
    </button>
  );
}

export function ProvenancePane() {
  const locus = useLocus();
  const path = locus.path ?? null;
  const [tab, setTab] = useState<Tab>('blame');
  return (
    <div className="git-pane">
      <div className="git-tabs">
        <button className={tab === 'blame' ? 'active' : ''} onClick={() => setTab('blame')}>
          Blame
        </button>
        <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
          History
        </button>
      </div>
      {tab === 'blame' ? <BlameView path={path} /> : <HistoryView />}
    </div>
  );
}

function BlameView({ path }: { path: string | null }) {
  const [blame, setBlame] = useState<BlameResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) {
      setBlame(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchBlame(path)
      .then((b) => {
        if (!cancelled) setBlame(b);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setBlame(null);
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (!path) return <div className="git-empty">Open a file to see its provenance.</div>;
  if (error) return <div className="git-empty">{error}</div>;
  if (!blame) return <div className="git-empty">Loading…</div>;
  if (!blame.is_repo)
    return <div className="git-empty">{basename(path)} isn’t in a git repository.</div>;
  if (blame.lines.length === 0)
    return <div className="git-empty">No blame for {basename(path)}.</div>;

  return (
    <div className="git-blame">
      <div className="git-blame-header">{basename(path)}</div>
      <ul className="git-blame-list">
        {blame.lines.map((ln) => (
          <li
            key={ln.line}
            className={`git-blame-row${ln.session_id ? ' agent' : ''}`}
            title={ln.summary}
            onClick={() =>
              setLocus(
                {
                  path,
                  range: { start: { line: ln.line, column: 1 }, end: { line: ln.line, column: 1 } },
                },
                'git',
              )
            }
          >
            <span className="git-blame-lineno">{ln.line}</span>
            <span className="git-blame-sha">{ln.commit}</span>
            {ln.session_id ? (
              <SessionChip id={ln.session_id} title={ln.session_title} />
            ) : (
              <span className="git-blame-author">{ln.author}</span>
            )}
            <span className="git-blame-text">{ln.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function HistoryView() {
  const [log, setLog] = useState<LogResult | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchLog(40)
      .then((l) => {
        if (!cancelled) setLog(l);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const select = (c: CommitInfo) => {
    setSelected(c.sha);
    setDiff(null);
    fetchShow(c.sha)
      .then(setDiff)
      .catch(() => setDiff(null));
  };

  if (!log) return <div className="git-empty">Loading…</div>;
  if (!log.is_repo) return <div className="git-empty">Not a git repository.</div>;

  return (
    <div className="git-history">
      <ul className="git-log-list">
        {log.commits.map((c) => (
          <li
            key={c.sha}
            className={`git-log-row${c.session_id ? ' agent' : ''}${c.sha === selected ? ' selected' : ''}`}
            onClick={() => select(c)}
          >
            <span className="git-log-sha">{c.sha}</span>
            <span className="git-log-summary">{c.summary}</span>
            {c.session_id ? (
              <SessionChip id={c.session_id} title={c.session_title} />
            ) : (
              <span className="git-log-author">{c.author}</span>
            )}
          </li>
        ))}
      </ul>
      {diff && <pre className="git-diff">{diff.diff}</pre>}
    </div>
  );
}
