/**
 * The shared cross-repo search surface, hosted by both the `code.search` pane and the
 * global quick-open modal. One box, two modes (Tab toggles): **Name** — live fuzzy
 * `find_symbols` (Slice 1, instant, no embedding); **Semantic** — `code.search`
 * (embeddings, on Enter). Every result is a locus: selecting one drives the editor via
 * the shared bus (core/locus.ts). See docs/modules/code.mdx.
 */
import { useEffect, useRef, useState } from 'react';

import { setLocus } from '../../locus';
import { subscribeChannel, type WsMessage } from '../../ws';
import { findSymbols, searchSemantic } from './api';
import type { CodeRange } from './types';
import './code.css';

type Mode = 'name' | 'semantic';

interface Row {
  name: string;
  kind: string;
  path: string;
  range: CodeRange | null;
  line: number | null;
  score?: number;
}

const KIND_ICON: Record<string, string> = {
  function: 'ƒ',
  method: 'ƒ',
  class: 'C',
  interface: 'I',
  type: 'T',
  enum: 'E',
};

function basename(p: string): string {
  const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
  return i >= 0 ? p.slice(i + 1) : p;
}

export function SymbolSearch({ onClose }: { onClose?: () => void }) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<Mode>('name');
  const [rows, setRows] = useState<Row[]>([]);
  const [selected, setSelected] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Live index-status ticker — the `index` event on the shared `code` /ws channel.
  useEffect(
    () =>
      subscribeChannel('code', (msg: WsMessage) => {
        if (msg.event !== 'index') return;
        const d = msg.data as { state: string; done: number; total: number };
        if (d.state === 'building') setStatus(`Indexing… ${d.done}/${d.total}`);
        else if (d.state === 'ready') setStatus(null);
        else if (d.state === 'failed') setStatus('Index failed');
      }),
    [],
  );

  // Name mode: live, debounced. Semantic mode waits for Enter (embeddings cost).
  useEffect(() => {
    if (mode !== 'name') return;
    const q = query.trim();
    if (!q) {
      setRows([]);
      return;
    }
    let cancelled = false;
    const t = setTimeout(() => {
      findSymbols(q, 30)
        .then((r) => {
          if (cancelled) return;
          setRows(
            r.hits.map((h) => ({
              name: h.name,
              kind: h.kind,
              path: h.path,
              range: h.range,
              line: h.range.start.line,
            })),
          );
          setSelected(0);
        })
        .catch(() => {});
    }, 120);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query, mode]);

  const runSemantic = () => {
    const q = query.trim();
    if (!q) return;
    setBusy(true);
    searchSemantic(q, 30)
      .then((r) => {
        setRows(
          r.results.map((h) => ({
            name: h.name ?? '?',
            kind: h.kind ?? '',
            path: h.path ?? '',
            range: h.range,
            line: h.range?.start.line ?? null,
            score: h.score,
          })),
        );
        setSelected(0);
        if (r.building) setStatus((s) => s ?? 'Indexing…');
      })
      .catch(() => {})
      .finally(() => setBusy(false));
  };

  const jump = (row: Row) => {
    if (!row.path) return;
    setLocus({ path: row.path, range: row.range ?? undefined, symbol: row.name }, 'search');
    onClose?.();
  };

  const switchMode = () => {
    setMode((m) => (m === 'name' ? 'semantic' : 'name'));
    setRows([]);
    inputRef.current?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      switchMode();
    } else if (e.key === 'Escape') {
      onClose?.();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, rows.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (mode === 'semantic' && rows.length === 0) runSemantic();
      else if (rows.length) jump(rows[selected]);
    }
  };

  return (
    <div className="symbol-search">
      <div className="symbol-search-bar">
        <button
          type="button"
          className={`symbol-search-mode${mode === 'semantic' ? ' semantic' : ''}`}
          title="Tab to switch Name ⇄ Semantic"
          onClick={switchMode}
        >
          {mode === 'name' ? 'Name' : 'Semantic'}
        </button>
        <input
          ref={inputRef}
          value={query}
          placeholder={
            mode === 'name' ? 'Find symbol by name…' : 'Describe the code… (Enter to search)'
          }
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {status && <span className="symbol-search-status">{status}</span>}
      </div>
      <ul className="symbol-search-results">
        {rows.map((row, i) => (
          <li
            key={`${row.path}:${row.line}:${i}`}
            className={`symbol-search-item${i === selected ? ' selected' : ''}`}
            onClick={() => jump(row)}
            onMouseEnter={() => setSelected(i)}
          >
            <span className={`symbol-search-kind code-kind-${row.kind}`}>
              {KIND_ICON[row.kind] ?? '•'}
            </span>
            <span className="symbol-search-name">{row.name}</span>
            {row.score !== undefined && (
              <span className="symbol-search-score">{row.score.toFixed(2)}</span>
            )}
            <span className="symbol-search-path">
              {basename(row.path)}
              {row.line ? `:${row.line}` : ''}
            </span>
          </li>
        ))}
        {busy && <li className="symbol-search-empty">Searching…</li>}
        {!busy && rows.length === 0 && query.trim() && (
          <li className="symbol-search-empty">
            {mode === 'semantic' ? 'Press Enter to search' : 'No matches'}
          </li>
        )}
      </ul>
    </div>
  );
}
