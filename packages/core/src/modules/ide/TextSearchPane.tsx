/**
 * **Search** — repo-wide text search, the panel the app did not have.
 *
 * `code.search` next door is symbol and semantic search: it answers "where is
 * this thing defined". This answers "where does this string appear", which is a
 * different question and the one you ask far more often.
 *
 * Opening a hit does two things in a specific order — `openInWorkbench` first,
 * then `setLocus`. `BufferView` applies the locus when it mounts, so setting it
 * first and opening second means the buffer arrives after the instruction it was
 * meant to follow and lands at line 1.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { IconMatchCase, IconRegex, IconSearch, IconWholeWord } from '../../glyphs';
import { setLocus } from '../../locus';
import { openBuffer } from '../editor';
import { searchFiles, type SearchFileResult, type SearchMatch, type SearchResult } from './api';
import './ide.css';

/**
 * Focus the query box — what `ide.findInFiles` calls after revealing the strip.
 * Published the way the browser module publishes its URL-bar focuser: only the
 * mounted component holds the element, but a command has to reach it.
 *
 * The **pending** flag is the part that matters. `revealRegionView` opens the
 * strip but the pane mounts a tick later, so a command that reveals and focuses
 * in the same breath finds no input at all and silently does nothing — you press
 * the shortcut and then still have to click the box. Recording the intent lets
 * the pane consume it when it arrives, and it covers both orders: already open,
 * and opening now.
 */
let focusQuery: (() => void) | null = null;
let focusPending = false;

export function focusSearchQuery(): void {
  if (focusQuery) focusQuery();
  else focusPending = true;
}

const DEBOUNCE_MS = 250;

export function TextSearchPane() {
  const [query, setQuery] = useState('');
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [include, setInclude] = useState('');
  const [exclude, setExclude] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Deferred by a tick, not called inline: revealing a strip also focuses its
    // *host pane*, and that focus lands after this effect runs — so focusing the
    // input here directly wins the race and then immediately loses it, which
    // looks exactly like the shortcut doing nothing.
    focusQuery = () => setTimeout(() => inputRef.current?.focus(), 0);
    if (focusPending) {
      focusPending = false;
      focusQuery();
    }
    return () => {
      focusQuery = null;
    };
  }, []);

  useEffect(() => {
    if (!query.trim()) {
      setResult(null);
      return;
    }
    // One in-flight request at a time: a fast typist would otherwise queue eight
    // walks and see them land out of order.
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setBusy(true);
      searchFiles(
        {
          query,
          regex,
          caseSensitive,
          wholeWord,
          include: splitGlobs(include),
          exclude: splitGlobs(exclude),
        },
        controller.signal,
      )
        .then(setResult)
        .catch(() => undefined)
        .finally(() => setBusy(false));
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, regex, caseSensitive, wholeWord, include, exclude]);

  const open = useCallback((match: SearchMatch) => {
    openBuffer(`workspace-file:${match.path}`);
    setLocus(
      {
        path: match.path,
        range: {
          start: { line: match.line, column: match.column },
          end: { line: match.line, column: match.column + (match.matchEnd - match.matchStart) },
        },
      },
      // Tagged so the editor does not echo this straight back at us.
      'ide.search',
    );
  }, []);

  const toggle = (path: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (!next.delete(path)) next.add(path);
      return next;
    });

  return (
    <div className="ide-panel">
      <div className="ide-search-controls">
        <div className="ide-search-query">
          <input
            ref={inputRef}
            type="text"
            className="ide-search-input"
            placeholder="Search"
            aria-label="Search the workspace"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Toggle on={caseSensitive} onChange={setCaseSensitive} label="Match case">
            <IconMatchCase />
          </Toggle>
          <Toggle on={wholeWord} onChange={setWholeWord} label="Match whole word">
            <IconWholeWord />
          </Toggle>
          <Toggle on={regex} onChange={setRegex} label="Use regular expression">
            <IconRegex />
          </Toggle>
        </div>
        <input
          type="text"
          className="ide-search-input"
          placeholder="files to include"
          aria-label="Files to include"
          value={include}
          onChange={(e) => setInclude(e.target.value)}
        />
        <input
          type="text"
          className="ide-search-input"
          placeholder="files to exclude"
          aria-label="Files to exclude"
          value={exclude}
          onChange={(e) => setExclude(e.target.value)}
        />
      </div>

      <Summary busy={busy} result={result} query={query} />

      <div className="ide-results">
        {(result?.files ?? []).map((file) => (
          <FileGroup
            key={file.path}
            file={file}
            collapsed={collapsed.has(file.path)}
            onToggle={() => toggle(file.path)}
            onOpen={open}
          />
        ))}
      </div>
    </div>
  );
}

function Summary({
  busy,
  result,
  query,
}: {
  busy: boolean;
  result: SearchResult | null;
  query: string;
}) {
  if (!query.trim()) {
    return (
      <p className="ide-panel-note">
        <IconSearch /> Search every file under your workspace roots.
      </p>
    );
  }
  if (busy && !result) return <p className="ide-panel-note">Searching…</p>;
  if (!result) return null;
  if (result.error) return <p className="ide-panel-note ide-panel-note--warn">{result.error}</p>;

  const files = result.files.length;
  const summary =
    result.total === 0
      ? 'No results'
      : `${result.total} result${result.total === 1 ? '' : 's'} in ${files} file${files === 1 ? '' : 's'}`;
  return (
    <p className="ide-panel-note">
      <span>{summary}</span>
      {/* "We stopped looking" is not "there is no more", and saying so is the
          difference between a partial answer and a wrong one. */}
      {result.truncated ? <span className="ide-panel-note--warn"> · more not shown</span> : null}
      {result.timedOut ? <span className="ide-panel-note--warn"> · search timed out</span> : null}
    </p>
  );
}

function FileGroup({
  file,
  collapsed,
  onToggle,
  onOpen,
}: {
  file: SearchFileResult;
  collapsed: boolean;
  onToggle: () => void;
  onOpen: (m: SearchMatch) => void;
}) {
  const slash = Math.max(file.path.lastIndexOf('/'), file.path.lastIndexOf('\\'));
  return (
    <div className="ide-result-group">
      <button
        className="ide-result-file"
        aria-expanded={!collapsed}
        title={file.path}
        onClick={onToggle}
      >
        <span className={`ide-caret${collapsed ? '' : ' ide-caret--open'}`} aria-hidden="true" />
        <span className="ide-result-name">{file.path.slice(slash + 1)}</span>
        <span className="ide-result-dir">{file.path.slice(0, slash)}</span>
        <span className="ide-count">{file.matches.length}</span>
      </button>
      {collapsed
        ? null
        : file.matches.map((match) => (
            <button
              key={`${match.line}:${match.column}`}
              className="ide-result-row"
              onClick={() => onOpen(match)}
            >
              <span className="ide-result-line">{match.line}</span>
              <span className="ide-result-text">
                {match.text.slice(0, match.matchStart)}
                <mark className="ide-hit">
                  {match.text.slice(match.matchStart, match.matchEnd)}
                </mark>
                {match.text.slice(match.matchEnd)}
              </span>
            </button>
          ))}
    </div>
  );
}

function Toggle({
  on,
  onChange,
  label,
  children,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`ide-toggle${on ? ' ide-toggle--on' : ''}`}
      aria-pressed={on}
      aria-label={label}
      title={label}
      onClick={() => onChange(!on)}
    >
      {children}
    </button>
  );
}

/** `*.ts, src/**` → two globs. Commas and spaces both separate, as in VS Code. */
function splitGlobs(raw: string): string[] {
  return raw
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
