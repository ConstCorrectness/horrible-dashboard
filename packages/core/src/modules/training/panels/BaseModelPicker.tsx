import { useEffect, useRef, useState } from 'react';

import { searchBaseModels, type BaseModelHit } from '../api';

/**
 * Pick the model a fine-tune starts from, instead of typing its name from memory.
 *
 * The field stays free text — a local directory is a valid base, and so is a repo
 * too new to be indexed — but the name goes verbatim into `from_pretrained(...)`,
 * so every way of getting it wrong used to surface minutes into a run, after the
 * dataset had downloaded. Searching puts the exact id one click away.
 *
 * The one this exists for is `qwen3:0.6b`: the **Ollama** name for the model the
 * Hub calls `Qwen/Qwen3-0.6B`, and the name this app shows everywhere else, so it
 * is the obvious thing to type. The backend's warning names it; this is where the
 * right id comes from.
 */

const dim = { color: 'var(--text-dim)' } as const;
const mono = { fontFamily: 'var(--font-mono, monospace)' } as const;

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

export function BaseModelPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<BaseModelHit[] | null>(null);
  const [status, setStatus] = useState('');
  // The in-flight search, so a slow earlier request cannot overwrite a newer one's
  // results — the classic search race, where the list you see is the query you
  // stopped typing two keystrokes ago.
  const latest = useRef(0);

  useEffect(() => {
    if (!query.trim()) {
      setHits(null);
      setStatus('');
      return;
    }
    const ticket = ++latest.current;
    const timer = setTimeout(() => {
      setStatus('searching…');
      searchBaseModels(query)
        .then((res) => {
          if (ticket !== latest.current) return;
          setHits(res.models);
          setStatus(res.models.length ? '' : 'no models matched');
        })
        .catch((err: unknown) => {
          if (ticket !== latest.current) return;
          setHits(null);
          // Said, not swallowed: an unreachable Hub is a reason the list is empty,
          // and silence here reads as "there is no such model".
          setStatus(err instanceof Error ? err.message : 'search failed');
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
      <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
        <span style={{ ...dim, width: '7rem' }}>Base model</span>
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Qwen/Qwen3-0.6B"
          style={{ flex: 1, padding: '0 0.6rem', ...mono }}
        />
      </label>
      <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
        <span style={{ ...dim, width: '7rem' }}>…or search</span>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="qwen3 0.6b"
          style={{ flex: 1, padding: '0 0.6rem' }}
        />
        {status && <span style={{ ...dim, fontSize: 11 }}>{status}</span>}
      </label>
      {hits && hits.length > 0 && (
        <ul
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            maxHeight: '11rem',
            overflowY: 'auto',
            border: '1px solid var(--border, #262934)',
            borderRadius: 4,
          }}
        >
          {hits.map((hit) => (
            <li key={hit.id}>
              <button
                className="picker-row"
                onClick={() => {
                  onChange(hit.id);
                  setQuery('');
                }}
                style={{
                  display: 'flex',
                  gap: '0.5rem',
                  alignItems: 'baseline',
                  width: '100%',
                  textAlign: 'left',
                  background: hit.id === value ? 'var(--surface-2, #1d2029)' : 'transparent',
                  border: 'none',
                  color: 'inherit',
                  padding: '0.3rem 0.5rem',
                  cursor: 'pointer',
                }}
              >
                <span style={{ ...mono, flex: 1 }}>{hit.id}</span>
                {/* Cautioned rather than hidden: a `…-GGUF` repo is a real model,
                    just the served copy, and a row missing with no explanation is
                    the worse failure. */}
                {hit.servingOnly && (
                  <span style={{ ...dim, fontSize: 11 }}>GGUF — serving only</span>
                )}
                {hit.gated && <span style={{ ...dim, fontSize: 11 }}>gated</span>}
                <span style={{ ...dim, fontSize: 11 }}>{compact(hit.downloads)} ↓</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
