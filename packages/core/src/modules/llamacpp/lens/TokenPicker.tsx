import { useEffect, useRef, useState } from 'react';

import { searchVocab, type VocabEntry } from '../api';
import { displayToken } from './grid-model';

/**
 * Pick a token out of the model's own vocabulary.
 *
 * The model's, and not an HF tokenizer's: a swap has to name a token the traced
 * weights actually have, and `tokenizer.py`'s family fallback can hand back a
 * different generation's vocabulary while looking exact. The list therefore
 * comes from the GGUF that produced the trace.
 *
 * Used for two different jobs — replacing a token in the strip, and pinning one
 * to track — so it takes an `onPick` and says nothing about what happens next.
 */
export function TokenPicker({
  modelPath,
  label,
  onPick,
  onCancel,
}: {
  modelPath: string;
  label: string;
  onPick: (entry: VocabEntry) => void;
  onCancel: () => void;
}) {
  const [query, setQuery] = useState('');
  const [entries, setEntries] = useState<VocabEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    // Debounced: the vocabulary is a quarter of a million entries and the
    // backend scans it linearly, so a request per keystroke is a request storm
    // over the same array.
    const timer = window.setTimeout(() => {
      void searchVocab(modelPath, query, 40)
        .then((res) => {
          if (!alive.current) return;
          setEntries(res.tokens);
          setTotal(res.total);
          setTruncated(res.truncated);
          setError('');
        })
        .catch((err: unknown) => {
          if (alive.current) setError(err instanceof Error ? err.message : String(err));
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [modelPath, query]);

  return (
    <div className="llama-card llama-picker">
      <h3>
        {label}
        <button className="llama-linkbtn" onClick={onCancel}>
          cancel
        </button>
      </h3>
      <input
        type="text"
        autoFocus
        value={query}
        placeholder="search the model's vocabulary…"
        onChange={(e) => setQuery(e.target.value)}
      />
      {error ? <p className="llama-error">{error}</p> : null}
      <ul className="llama-picker-list">
        {entries.map((entry) => (
          <li key={entry.id}>
            <button className="llama-picker-row" onClick={() => onPick(entry)}>
              <span className="llama-picker-text">{displayToken(entry.text, 24)}</span>
              <span className="llama-meta">#{entry.id}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="llama-meta">
        {entries.length === 0 && !error ? 'no match · ' : ''}
        {total.toLocaleString()} tokens in this model
        {truncated ? ' · showing the first 40 matches' : ''}
      </p>
    </div>
  );
}
