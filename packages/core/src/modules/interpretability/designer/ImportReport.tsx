/**
 * What an import of the inspected model had to assume, and what it could not read.
 *
 * This is deliberately a panel rather than the transient notice the rest of the pane
 * uses. A dismissible one-liner is right for "that wire was refused"; it is wrong for
 * a list of five decisions that were made on your behalf about a model you are about
 * to train, because the cost of missing one of them is a model that is quietly the
 * wrong shape. It stays until it is closed.
 *
 * The parameter comparison at the bottom is the import auditing itself. On a real
 * Llama the two numbers differ by 12%, and the entire difference is the output head
 * the model ties to its embedding — which is why the note that explains it matters
 * more than the number that provokes it.
 */
import { formatCount, type ImportResult } from './graph';

export function ImportReport({ result, onClose }: { result: ImportResult; onClose: () => void }) {
  const { assumed, notes, statedParams, estimatedParams } = result;
  const agrees =
    statedParams != null &&
    estimatedParams != null &&
    Math.abs(estimatedParams - statedParams) <= Math.max(1, Math.floor(statedParams / 1000));

  return (
    <section className="mg-import" aria-label="Import report">
      <header className="mg-import-head">
        <h3 className="mg-import-title">Imported {result.model || 'the inspected model'}</h3>
        <span className="mg-chip mg-chip-estimate">{result.source || 'unknown source'}</span>
        <div className="mg-toolbar-spacer" />
        <button type="button" className="mg-button" onClick={onClose}>
          Dismiss
        </button>
      </header>

      {assumed.length > 0 && (
        <>
          <h4 className="mg-import-sub">
            {assumed.length} {assumed.length === 1 ? 'assumption' : 'assumptions'}
          </h4>
          <p className="mg-import-lead">
            The metadata did not state these, so they were chosen for you. Each one is a thing worth
            correcting on the canvas before you trust the shape.
          </p>
          <ul className="mg-import-list">
            {assumed.map((line) => (
              <li key={line} className="mg-import-item mg-import-assumed">
                {line}
              </li>
            ))}
          </ul>
        </>
      )}

      {notes.length > 0 && (
        <>
          <h4 className="mg-import-sub">Worth knowing</h4>
          <ul className="mg-import-list">
            {notes.map((line) => (
              <li key={line} className="mg-import-item">
                {line}
              </li>
            ))}
          </ul>
        </>
      )}

      {statedParams != null && estimatedParams != null && (
        <p className={`mg-import-count${agrees ? ' mg-import-agrees' : ''}`}>
          <span className="mg-mono">{formatCount(estimatedParams)}</span> derived against{' '}
          <span className="mg-mono">{formatCount(statedParams)}</span> stated
          {agrees ? ' — they agree.' : '.'}
        </p>
      )}
    </section>
  );
}
