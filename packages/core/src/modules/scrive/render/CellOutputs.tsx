/**
 * A code cell's run bar and outputs, shared by the Preview (`MystView`) and the block
 * editor's code-cell node.
 *
 * Outputs go through the notebook's `OutputRenderer`, but not as they come: that
 * renderer trusts HTML and SVG (a notebook is the user's own code on their own
 * machine). A Scrive page may be agent-written or downloaded, and the preview shares
 * the app's origin, so here HTML is sanitized (`sanitize.ts`, which keeps tables —
 * a pandas DataFrame survives) and SVG is drawn as an image, which never runs script.
 */
import { useSyncExternalStore } from 'react';

import { OutputRenderer } from '../../../notebook/OutputRenderer';
import type { NbOutput } from '../../../notebook/types';
import { pageCells, type CellView, type PageCells } from '../cells';
import { sanitizeHtml } from './sanitize';

const joined = (v: unknown): string => (Array.isArray(v) ? v.join('') : String(v ?? ''));

function base64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

/** An output made safe for a page: HTML sanitized, SVG removed (drawn separately). */
export function safeOutput(output: NbOutput): NbOutput {
  const data = output.data as Record<string, unknown> | undefined;
  if (!data) return output;
  const next: Record<string, unknown> = { ...data };
  delete next['image/svg+xml'];
  if (next['text/html'] != null) next['text/html'] = sanitizeHtml(joined(next['text/html']));
  return { ...output, data: next };
}

function Output({ output }: { output: NbOutput }) {
  const data = output.data as Record<string, unknown> | undefined;
  const svg = data?.['image/svg+xml'];
  // The notebook renderer ranks SVG second, after TeX; keep its choice, but draw the
  // SVG as an image so nothing in it can run.
  if (svg != null && data?.['text/latex'] == null) {
    return (
      <div className="nb-output-media">
        <img src={`data:image/svg+xml;base64,${base64Utf8(joined(svg))}`} alt="cell output" />
      </div>
    );
  }
  return <OutputRenderer output={safeOutput(output)} />;
}

/** Subscribe to a page's cells; re-renders when outputs or the kernel change. */
export function usePageCells(site: string, path: string): PageCells | null {
  const cells = site && path.endsWith('.md') ? pageCells(site, path) : null;
  // The server snapshot is the same: `renderToStaticMarkup` (tests, and the static
  // site build) renders whatever outputs are already known.
  const snapshot = cells ? cells.snapshot : zero;
  useSyncExternalStore(cells ? cells.subscribe : noopSubscribe, snapshot, snapshot);
  return cells;
}

const noopSubscribe = () => () => {};
const zero = () => 0;

const STATE_LABEL: Record<CellView['state'], string> = {
  none: 'not run',
  queued: 'queued',
  running: 'running',
  done: 'ran',
  error: 'error',
};

export function CellRunBar({
  cells,
  source,
  occurrence,
  language,
}: {
  cells: PageCells | null;
  source: string;
  occurrence: number;
  language: string;
}) {
  const view = cells?.view(source, occurrence);
  const busy = view?.state === 'queued' || view?.state === 'running';
  return (
    <div className="scrive-cell-bar" contentEditable={false}>
      <span className="scrive-code-label">code cell · {language || 'python'}</span>
      <span className="scrive-cell-state" data-state={view?.state ?? 'none'}>
        {view?.executionCount != null ? `[${view.executionCount}] ` : ''}
        {view ? STATE_LABEL[view.state] : ''}
      </span>
      {cells && (
        <button
          type="button"
          className="scrive-cell-run"
          title={busy ? 'Interrupt the kernel' : 'Run this cell (Shift+Enter)'}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => (busy ? cells.interrupt() : void cells.run({ source, occurrence }))}
        >
          {busy ? (
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" />
            </svg>
          ) : (
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M7 5v14l12-7z" />
            </svg>
          )}
          {busy ? 'Stop' : 'Run'}
        </button>
      )}
    </div>
  );
}

/** Outputs held by the page itself (an `.ipynb`), not by the kernel cache. */
export function StoredOutputs({ outputs }: { outputs: NbOutput[] }) {
  if (!outputs.length) return null;
  return (
    <div className="scrive-cell-outputs">
      {outputs.map((output, i) => (
        <Output key={i} output={output} />
      ))}
    </div>
  );
}

export function CellOutputs({
  cells,
  source,
  occurrence,
}: {
  cells: PageCells | null;
  source: string;
  occurrence: number;
}) {
  const view = cells?.view(source, occurrence);
  if (!view?.outputs.length) return null;
  return (
    <div className="scrive-cell-outputs" contentEditable={false}>
      {view.outputs.map((output, i) => (
        <Output key={i} output={output} />
      ))}
    </div>
  );
}
