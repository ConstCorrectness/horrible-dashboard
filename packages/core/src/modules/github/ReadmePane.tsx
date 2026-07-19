/**
 * The repo's README, rendered as the viewer's landing content.
 *
 * Reuses the notebook's dependency-free Markdown renderer. That renderer escapes HTML
 * before parsing and rewrites any non-http(s)/mailto link to `#`, which matters more
 * here than it does for notebook cells: a README is **remote, untrusted input**, not
 * the user's own note. Images aren't rendered (the renderer has no image support), so
 * they degrade to links — which also means no remote image ever loads from a repo the
 * user merely browsed.
 */
import { useEffect, useState } from 'react';

import { renderMarkdown } from '../../notebook/markdown';
import { getReadme } from './api';

export function ReadmePane({
  owner,
  repo,
  refName,
}: {
  owner: string;
  repo: string;
  refName: string;
}) {
  const [html, setHtml] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setHtml(null);
    setMissing(false);
    getReadme(owner, repo, refName)
      .then((res) => {
        if (!cancelled) setHtml(renderMarkdown(res.content));
      })
      .catch(() => {
        // A repo without a README is ordinary, not an error worth shouting about.
        if (!cancelled) setMissing(true);
      });
    return () => {
      cancelled = true;
    };
  }, [owner, repo, refName]);

  if (missing) {
    return <p className="home-hint gh-readme-empty">No README in this repository.</p>;
  }
  if (html === null) {
    return <p className="home-hint gh-readme-empty">Loading README…</p>;
  }
  return <div className="gh-readme" dangerouslySetInnerHTML={{ __html: html }} />;
}
