/**
 * A `{mermaid}` diagram. Mermaid is ~1 MB, so it loads the first time a page has a
 * diagram, never at boot or for pages without one.
 *
 * `securityLevel: 'strict'` makes Mermaid sanitize its own SVG (labels can carry
 * HTML, and pages can be agent-written). Colours come from the live theme tokens, so
 * a diagram is legible in every app theme rather than baked for one.
 */
import { useEffect, useId, useState } from 'react';

function token(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function MermaidBlock({ code }: { code: string }) {
  const id = `scrive-mermaid-${useId().replace(/[^\w-]/g, '')}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void import('mermaid')
      .then(async ({ default: mermaid }) => {
        const text = token('--text', 'currentColor');
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: 'base',
          themeVariables: {
            background: 'transparent',
            primaryColor: token('--bg-raised', 'transparent'),
            primaryBorderColor: token('--accent', text),
            primaryTextColor: text,
            lineColor: token('--text-dim', text),
            fontFamily: 'inherit',
          },
        });
        const out = await mermaid.render(id, code);
        if (alive) setSvg(out.svg);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [code, id]);

  if (error) {
    return (
      <figure className="scrive-mermaid is-error">
        <pre>{code}</pre>
        <figcaption>Diagram did not render: {error}</figcaption>
      </figure>
    );
  }
  if (!svg) return <figure className="scrive-mermaid is-loading" aria-busy="true" />;
  // Mermaid's own sanitized SVG (securityLevel 'strict').
  return <figure className="scrive-mermaid" dangerouslySetInnerHTML={{ __html: svg }} />;
}
