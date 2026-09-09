/**
 * **Problems** — language-server diagnostics across the files you have open.
 *
 * The scope is a real limit, not an implementation gap to be quietly grown out
 * of, and the empty state says so out loud. Diagnostics here come from live LSP
 * sessions, which exist for buffers that have been opened; there is no
 * project-wide check, no `workspace/diagnostic` pull and no compiler run behind
 * this panel. A Problems list that showed nothing and *implied* a clean project
 * would be worse than no panel, so it states its own scope instead.
 *
 * What makes it more than a mirror of the active buffer's gutter is
 * `retainDiagnostics`: without it the registry drops a file's diagnostics when
 * its CodeMirror view is destroyed, which happens on every tab switch, and this
 * would list exactly the one file already showing its errors.
 */
import { useMemo, useState, useSyncExternalStore } from 'react';

import { setLocus } from '../../locus';
import { openBuffer, sourceTitle } from '../editor';
import {
  listDiagnostics,
  subscribeDiagnostics,
  type AgentDiagnostic,
} from '../editor/lsp-registry';
import './ide.css';

type Severity = AgentDiagnostic['severity'];

const ORDER: Record<Severity, number> = { error: 0, warning: 1, info: 2, hint: 3 };

// `useSyncExternalStore` compares by identity, so the snapshot must be cached
// until the registry actually changes — a fresh array each call loops forever.
let cache: ReturnType<typeof listDiagnostics> = [];
let cacheKey = '';
function snapshot() {
  const next = listDiagnostics();
  const key = next.map((d) => `${d.uri}:${d.diagnostics.length}`).join('|');
  if (key !== cacheKey) {
    cacheKey = key;
    cache = next;
  }
  return cache;
}

export function ProblemsPane() {
  const entries = useSyncExternalStore(subscribeDiagnostics, snapshot, snapshot);
  const [filter, setFilter] = useState<Severity | 'all'>('all');

  const rows = useMemo(() => {
    return entries
      .map((entry) => ({
        uri: entry.uri,
        diagnostics: entry.diagnostics
          .filter((d) => filter === 'all' || d.severity === filter)
          .sort((a, b) => ORDER[a.severity] - ORDER[b.severity] || a.line - b.line),
      }))
      .filter((entry) => entry.diagnostics.length > 0);
  }, [entries, filter]);

  const counts = useMemo(() => {
    const out = { error: 0, warning: 0, info: 0, hint: 0 } as Record<Severity, number>;
    for (const entry of entries) for (const d of entry.diagnostics) out[d.severity] += 1;
    return out;
  }, [entries]);

  const open = (uri: string, d: AgentDiagnostic) => {
    openBuffer(uri);
    if (!uri.startsWith('workspace-file:')) return;
    setLocus(
      {
        path: uri.slice('workspace-file:'.length),
        range: {
          start: { line: d.line, column: d.column },
          end: { line: d.endLine, column: d.endColumn },
        },
      },
      'ide.problems',
    );
  };

  return (
    <div className="ide-panel">
      <div className="ide-filters">
        {(['all', 'error', 'warning', 'info'] as const).map((kind) => (
          <button
            key={kind}
            className={`ide-toggle${filter === kind ? ' ide-toggle--on' : ''}`}
            aria-pressed={filter === kind}
            onClick={() => setFilter(kind)}
          >
            <span className="ide-filter-label">{kind}</span>
            {kind !== 'all' ? <span className="ide-count">{counts[kind]}</span> : null}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="ide-panel-note">
          No problems. Diagnostics come from the language servers of files you have opened — this is
          not a project-wide check.
        </p>
      ) : (
        <div className="ide-results">
          {rows.map((entry) => (
            <div key={entry.uri} className="ide-result-group">
              <div className="ide-scm-group">
                <span className="ide-result-name">{sourceTitle(entry.uri)}</span>
                <span className="ide-count">{entry.diagnostics.length}</span>
              </div>
              {entry.diagnostics.map((d, index) => (
                <button
                  key={`${d.line}:${d.column}:${index}`}
                  className="ide-result-row"
                  onClick={() => open(entry.uri, d)}
                >
                  <span className={`ide-sev ide-sev--${d.severity}`} aria-label={d.severity} />
                  <span className="ide-result-text">{d.message}</span>
                  <span className="ide-result-line">
                    {d.line}:{d.column}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
