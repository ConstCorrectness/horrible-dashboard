/**
 * The Preview: the page as a reader will see it, at a chosen device width, on a
 * faint measuring grid (this is a layout canvas — the one place in Scrive the grid
 * belongs). Re-parsed from the editor's live text, so it follows typing.
 *
 * `highlight` marks the top-level blocks starting on those file lines — the blocks an
 * agent just changed — by class, after render, so the renderer itself stays unaware.
 */
import { useEffect, useMemo, useRef } from 'react';

import { notebookTree } from '../myst/notebook';
import { parseMyst, readFrontmatter, splitFrontmatter } from '../myst/parse';
import { MystView } from '../render/MystView';

export type Device = 'phone' | 'tablet' | 'desktop';

export const DEVICE_WIDTH: Record<Device, string> = {
  phone: '390px',
  tablet: '768px',
  desktop: '100%',
};

function asText(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

export function PreviewCanvas({
  text,
  site,
  path,
  device,
  onBlockClick,
  highlight,
}: {
  text: string;
  site: string;
  path: string;
  device: Device;
  onBlockClick?: (line: number) => void;
  highlight?: readonly number[];
}) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const { tree, data, lines } = useMemo(() => {
    if (path.endsWith('.ipynb')) {
      // A notebook: read-only, rendered from its cells and stored outputs.
      const nb = notebookTree(text);
      return { tree: nb.tree, data: { title: nb.title }, lines: 0 };
    }
    const fm = splitFrontmatter(text);
    return { tree: parseMyst(fm.body), data: readFrontmatter(fm.raw), lines: fm.lines };
  }, [text, path]);

  const title = asText(data.title);
  const date = asText(data.date);
  const description = asText(data.description);
  const tags = Array.isArray(data.tags) ? data.tags.map(asText).filter(Boolean) : [];

  useEffect(() => {
    const marked = new Set(highlight ?? []);
    for (const el of sheetRef.current?.querySelectorAll<HTMLElement>('[data-line]') ?? []) {
      // Top-level blocks only: a list's first item shares its list's line.
      const top = !el.parentElement?.closest('[data-line]');
      el.classList.toggle('scrive-agent-changed', top && marked.has(Number(el.dataset.line)));
    }
  }, [highlight, tree]);

  return (
    <div
      className="scrive-canvas"
      style={{
        height: '100%',
        overflow: 'auto',
        padding: 'var(--space-5) var(--space-4)',
      }}
    >
      <div
        ref={sheetRef}
        className="scrive-sheet"
        style={{
          width: DEVICE_WIDTH[device],
          maxWidth: '100%',
          margin: '0 auto',
          padding:
            device === 'desktop'
              ? 'var(--space-6) var(--space-6)'
              : 'var(--space-5) var(--space-4)',
        }}
      >
        <MystView
          tree={tree}
          site={site}
          pagePath={path}
          lineOffset={lines}
          onBlockClick={onBlockClick}
          header={
            (title || date || description || tags.length > 0) && (
              <header className="scrive-page-header">
                {title && <h1 className="scrive-page-title">{title}</h1>}
                {(date || tags.length > 0) && (
                  <div className="scrive-page-byline">
                    {date && <time dateTime={date}>{date}</time>}
                    {tags.map((tag) => (
                      <span key={tag} className="scrive-page-tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                {description && <p className="scrive-page-description">{description}</p>}
              </header>
            )
          }
        />
      </div>
    </div>
  );
}
