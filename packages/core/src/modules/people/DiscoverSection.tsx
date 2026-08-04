/**
 * **Discover** — find a person by the name they already have.
 *
 * The complaint this answers: adding someone meant asking them to read out an
 * `HD-XXXX-XXXX-XXXX-XXXX-XXXX` friend code, which is not something people do.
 * A callsign is 3–20 characters, globally unique, and already printed next to
 * them on the ladder and in HorribleAssault.
 *
 * The friend code is still here and still first-class, because it is the only
 * path that works **offline and without trusting anyone**: it is derived from the
 * person's own key, so a hostile directory can withhold someone but never
 * substitute a different key for them. Callsign search is the convenience; the
 * code is the guarantee. See docs/modules/social.mdx.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { CommonsDirectory } from '../commons';
import { addFriend, searchDirectory, type DirectoryEntry } from '../social/api';

const DEBOUNCE_MS = 250;

export function DiscoverSection() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryEntry[]>([]);
  const [minPrefix, setMinPrefix] = useState(3);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // Which query the in-flight response belongs to: without this, a slow request
  // for "ro" can land after a fast one for "robert" and repopulate the list with
  // stale, broader results.
  const latest = useRef('');

  useEffect(() => {
    const q = query.trim().replace(/^@/, '');
    latest.current = q;
    if (q.length < minPrefix) {
      setResults([]);
      return;
    }
    const timer = setTimeout(() => {
      setBusy(true);
      searchDirectory(q)
        .then((res) => {
          if (latest.current !== q) return;
          setResults(res.results ?? []);
          if (typeof res.min_prefix === 'number') setMinPrefix(res.min_prefix);
        })
        .catch(() => {
          if (latest.current === q) setResults([]);
        })
        .finally(() => {
          if (latest.current === q) setBusy(false);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, minPrefix]);

  const add = useCallback(async (entry: DirectoryEntry) => {
    setNote(null);
    // Sent as `@handle` rather than the person id we already hold, so the backend
    // re-resolves and re-checks the key fingerprint itself. The browser is not a
    // trusted source of person ids.
    const res = await addFriend(`@${entry.handle}`);
    setNote(res.error ? res.error : `Friend request sent to @${entry.handle}.`);
  }, []);

  const short = query.trim().replace(/^@/, '').length < minPrefix;

  return (
    <div className="people-section">
      <label className="people-field">
        <span className="people-label">Find someone by callsign</span>
        <input
          type="search"
          value={query}
          placeholder="@callsign"
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>

      {query.trim() && short ? (
        <p className="people-hint">Type at least {minPrefix} characters.</p>
      ) : null}
      {busy ? <p className="people-hint">Searching…</p> : null}
      {!busy && !short && query.trim() && results.length === 0 ? (
        <p className="people-hint">No callsign starts with that.</p>
      ) : null}
      {note ? <p className="people-note">{note}</p> : null}

      {results.length > 0 && (
        <ul className="people-list">
          {results.map((entry) => (
            <li key={entry.person_id} className="people-row">
              <div className="people-row-main">
                <span className="people-handle">@{entry.handle}</span>
                {entry.display_name && entry.display_name !== entry.handle ? (
                  <span className="people-dim">{entry.display_name}</span>
                ) : null}
              </div>
              <button type="button" onClick={() => void add(entry)}>
                Add friend
              </button>
            </li>
          ))}
        </ul>
      )}

      <details className="people-fold">
        <summary>Browse the agent commons</summary>
        <div className="people-embed">
          <CommonsDirectory />
        </div>
      </details>
    </div>
  );
}
