/**
 * The generated module, beside the graph that generates it.
 *
 * Read-only for now, and honest about it: Phase 2 makes this editable and re-parses
 * on explicit sync. What it already does is the half that matters most — hovering a
 * node highlights the lines it produced, using the `# horrible:node=` markers the
 * generator emits. That correspondence is the whole argument that the diagram and
 * the code are the same object rather than two descriptions that happen to agree.
 */
import { useMemo } from 'react';

export function CodePane({
  source,
  error,
  markers,
  highlightNode,
  onCopy,
}: {
  source: string;
  error: string | null;
  /** Line number (1-based, as a string key) → node id. */
  markers: Record<string, string>;
  highlightNode: string | null;
  onCopy?: () => void;
}) {
  const rows = useMemo(() => source.split('\n'), [source]);
  const lit = useMemo(() => {
    if (!highlightNode) return new Set<number>();
    return new Set(
      Object.entries(markers)
        .filter(([, nid]) => nid === highlightNode)
        .map(([line]) => Number(line)),
    );
  }, [markers, highlightNode]);

  return (
    <div className="mg-code">
      <div className="mg-code-head">
        <span className="mg-code-title">Generated module</span>
        <span className="mg-code-meta">{rows.length} lines</span>
        <button
          type="button"
          className="mg-button"
          onClick={() => {
            void navigator.clipboard?.writeText(source);
            onCopy?.();
          }}
          disabled={!source}
        >
          Copy
        </button>
      </div>

      {error && (
        <div className="mg-code-error">
          <strong>Not generated:</strong> {error}
          <span className="mg-code-error-note">
            The design is still saved — a canvas is unfinished most of the time, and losing work
            over one unconnected wire would be worse than an incomplete file.
          </span>
        </div>
      )}

      {!error && !source && <div className="mg-code-empty">Nothing to generate yet.</div>}

      {source && (
        <pre className="mg-code-body">
          {rows.map((row, index) => (
            <code
              key={index}
              className={lit.has(index + 1) ? 'mg-code-line mg-code-lit' : 'mg-code-line'}
            >
              <span className="mg-code-num">{index + 1}</span>
              {row}
              {'\n'}
            </code>
          ))}
        </pre>
      )}
    </div>
  );
}
