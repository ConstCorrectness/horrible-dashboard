/**
 * Choosing a repository: your own repos by default, GitHub-wide search on demand.
 *
 * Shown when the pane has no repo open, and reachable again from the viewer header.
 */
import { useEffect, useState } from 'react';

import { listRepos, searchRepos, type RepoSummary } from './api';

export function RepoPicker({ onPick }: { onPick: (repo: RepoSummary) => void }) {
  const [own, setOwn] = useState<RepoSummary[] | null>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<RepoSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listRepos()
      .then((repos) => {
        if (!cancelled) setOwn(repos);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) {
      setResults(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResults(await searchRepos(query));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const shown = results ?? own;

  return (
    <div className="gh-picker">
      <form className="gh-picker-search" onSubmit={(e) => void search(e)}>
        <input
          type="search"
          placeholder="Search GitHub, or pick one of yours below"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!e.target.value.trim()) setResults(null);
          }}
        />
        <button type="submit" disabled={busy}>
          {busy ? '…' : 'Search'}
        </button>
      </form>

      {error && <p className="widget-error">{error}</p>}

      {shown === null && !error && <p className="home-hint">Loading repositories…</p>}
      {shown?.length === 0 && <p className="home-hint">No repositories found.</p>}

      <ul className="gh-repo-list">
        {shown?.map((repo) => (
          <li key={repo.full_name}>
            <button className="gh-repo-item" onClick={() => onPick(repo)}>
              <span className="gh-repo-name">
                {repo.full_name}
                {repo.private && <span className="gh-badge">private</span>}
              </span>
              {repo.description && <span className="gh-repo-desc">{repo.description}</span>}
              <span className="gh-repo-meta">
                {repo.language && <span>{repo.language}</span>}
                {repo.stars > 0 && <span>★ {repo.stars}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
