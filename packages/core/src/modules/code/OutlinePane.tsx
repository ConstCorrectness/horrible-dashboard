/**
 * Symbol outline for the file at the current **code locus** (see core/locus.ts). It
 * both *follows* the locus — highlighting the definition the editor cursor sits in —
 * and *drives* it: clicking a symbol sets the locus, so the editor scrolls/selects it.
 * That two-way wiring is the whole thesis; the pane never talks to the editor directly.
 * See docs/modules/code.mdx.
 */
import { useEffect, useState } from 'react';

import { useLocus, setLocus } from '../../locus';
import { fetchDocumentSymbols } from './api';
import type { DocumentSymbols } from './types';
import './code.css';

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

export function OutlinePane() {
  const locus = useLocus();
  const path = locus.path ?? null;
  const cursorLine = locus.range?.start.line ?? null;
  const [doc, setDoc] = useState<DocumentSymbols | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Refetch the outline whenever the locus points at a different file.
  useEffect(() => {
    if (!path) {
      setDoc(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchDocumentSymbols(path)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setDoc(null);
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (!path) return <div className="code-outline-empty">Open a code file to see its outline.</div>;
  if (error) return <div className="code-outline-empty">{error}</div>;
  if (!doc) return <div className="code-outline-empty">Loading…</div>;
  if (doc.symbols.length === 0)
    return <div className="code-outline-empty">No symbols in {basename(path)}.</div>;

  // The tightest definition whose range contains the cursor — the "active" symbol
  // (later start line = more deeply nested = tighter).
  let activeIdx = -1;
  if (cursorLine != null) {
    doc.symbols.forEach((s, i) => {
      if (s.range.start.line <= cursorLine && cursorLine <= s.range.end.line) {
        if (activeIdx < 0 || s.range.start.line >= doc.symbols[activeIdx].range.start.line) {
          activeIdx = i;
        }
      }
    });
  }

  return (
    <div className="code-outline">
      <div className="code-outline-header">{basename(path)}</div>
      <ul className="code-outline-list">
        {doc.symbols.map((s, i) => (
          <li
            key={`${s.name}:${s.range.start.line}:${i}`}
            className={`code-outline-item${i === activeIdx ? ' active' : ''}`}
            style={{ paddingLeft: `${8 + (s.container ? 16 : 0)}px` }}
            title={`${s.kind} ${s.name}${s.container ? ` — in ${s.container}` : ''}`}
            onClick={() => setLocus({ path, range: s.range, symbol: s.name }, 'outline')}
          >
            <span className={`code-outline-kind code-kind-${s.kind}`}>
              {KIND_ICON[s.kind] ?? '•'}
            </span>
            <span className="code-outline-name">{s.name}</span>
            <span className="code-outline-line">{s.range.start.line}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
