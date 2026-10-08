/**
 * `{tokenviz} data/run.json`: a recorded generation drawn as a token-probability strip
 * — every token shaded by how sure the model was, hover for what it nearly said.
 *
 * Recorded, not run: the reader downloads nothing, and the figure says exactly what
 * the author saw. Runs come from a `{webllm}` block's "Keep as figure" (or any file of
 * the same shape: `{model, prompt, steps: [{token, p, entropy, topk}]}`).
 *
 * In the app the file is fetched; a published page gets it preloaded by the build
 * (`StaticRender.data`), so the strip is plain markup with CSS hovers.
 */
import { useContext, useEffect, useState } from 'react';

import { parseTokenRun, TokenStrip, type TokenRun } from '../../../token-strip/TokenStrip';
import { assetUrl } from './directives';
import { StaticRenderContext } from './static-context';

function summary(run: TokenRun): string {
  const n = run.steps.length;
  if (!n) return 'no tokens';
  const meanP = run.steps.reduce((a, s) => a + s.p, 0) / n;
  const meanH = run.steps.reduce((a, s) => a + s.entropy, 0) / n;
  const unsure = run.steps.filter((s) => s.p < 0.5).length;
  return `${n} tokens · mean p ${meanP.toFixed(2)} · mean H ${meanH.toFixed(2)} bits · ${unsure} below p 0.5`;
}

export function TokenVizFigure({ run }: { run: TokenRun }) {
  return (
    <figure className="scrive-tokenviz">
      <figcaption className="scrive-space-bar">
        <span className="scrive-space-title">Token probabilities</span>
        {run.model && <span className="scrive-space-meta">{run.model}</span>}
        <span className="scrive-space-meta">{summary(run)}</span>
      </figcaption>
      {run.prompt && <blockquote className="scrive-tokenviz-prompt">{run.prompt}</blockquote>}
      <div className="scrive-tokenviz-strip">
        <TokenStrip steps={run.steps} />
      </div>
    </figure>
  );
}

export function TokenViz({ site, pagePath, src }: { site: string; pagePath: string; src: string }) {
  const published = useContext(StaticRenderContext);
  const preloaded = published ? published.data(pagePath, src) : undefined;
  const [loaded, setLoaded] = useState<TokenRun | string | null>(null);

  useEffect(() => {
    if (published || !src) return;
    let live = true;
    fetch(assetUrl(site, pagePath, src))
      .then((res) => {
        if (!res.ok)
          throw new Error(res.status === 404 ? `${src} is not in the site` : `HTTP ${res.status}`);
        return res.json() as Promise<unknown>;
      })
      .then(
        (json) => live && setLoaded(parseTokenRun(json)),
        (e: unknown) => live && setLoaded(e instanceof Error ? e.message : String(e)),
      );
    return () => {
      live = false;
    };
  }, [published, site, pagePath, src]);

  const run = published
    ? preloaded === undefined
      ? `${src} was not found`
      : parseTokenRun(preloaded)
    : loaded;
  if (run === null)
    return (
      <figure className="scrive-tokenviz scrive-space-note scrive-space-pad">Loading {src}…</figure>
    );
  if (typeof run === 'string') {
    // A reader never sees a broken figure; the author sees why.
    return published ? null : (
      <figure className="scrive-tokenviz" data-status="error">
        <div className="scrive-space-error">
          {'{tokenviz}'} {src}: {run}
        </div>
      </figure>
    );
  }
  return <TokenVizFigure run={run} />;
}
