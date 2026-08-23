/**
 * The generated module, beside the graph that generates it — and editable.
 *
 * Two things make this more than a preview:
 *
 * **Hovering a node lights its lines**, using the `# horrible:node=` markers the
 * generator emits. That correspondence is the whole argument that the diagram and the
 * code are the same object rather than two descriptions that happen to agree.
 *
 * **Edits flow back**, on an *explicit* sync — the save key, or leaving the editor —
 * never per keystroke. A parse mid-word would replace your graph with whatever
 * half-typed source currently parses to, which at best redraws the canvas under your
 * cursor and at worst quietly drops the node you were describing.
 *
 * Reading and editing are separate modes rather than a textarea overlaid on a
 * highlighted `<pre>`. The overlay is the prettier trick and it depends on two
 * elements agreeing about font metrics to the pixel; when they disagree the caret
 * drifts from the text, which is a far worse failure than a button press.
 */
import { useEffect, useMemo, useState } from 'react';

export function CodePane({
  source,
  error,
  markers,
  highlightNode,
  onSync,
  syncState,
}: {
  source: string;
  error: string | null;
  /** Line number (1-based, as a string key) → node id. */
  markers: Record<string, string>;
  highlightNode: string | null;
  /** Called with the edited buffer on an explicit sync. */
  onSync: (source: string) => void;
  /** What the last sync did, so the pane can say rather than silently discard. */
  syncState: { status: 'idle' | 'syncing' | 'ok' | 'error'; message?: string };
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const dirty = editing && draft !== source;

  // Leaving edit mode, or a successful sync, hands the buffer back to the generator.
  useEffect(() => {
    if (!editing) setDraft(source);
  }, [editing, source]);

  const rows = useMemo(() => source.split('\n'), [source]);
  const lit = useMemo(() => {
    if (!highlightNode) return new Set<number>();
    return new Set(
      Object.entries(markers)
        .filter(([, nid]) => nid === highlightNode)
        .map(([line]) => Number(line)),
    );
  }, [markers, highlightNode]);

  const apply = () => {
    if (dirty) onSync(draft);
  };

  return (
    <div className="mg-code">
      <div className="mg-code-head">
        <span className="mg-code-title">Generated module</span>
        <span className="mg-code-meta">
          {dirty ? 'edited — Ctrl-S to apply' : `${rows.length} lines`}
        </span>
        {editing ? (
          <>
            <button
              type="button"
              className="mg-button mg-button-on"
              onClick={apply}
              disabled={!dirty}
            >
              Apply
            </button>
            <button
              type="button"
              className="mg-button"
              onClick={() => {
                setEditing(false);
                setDraft(source);
              }}
            >
              Done
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="mg-button"
              onClick={() => setEditing(true)}
              disabled={!source}
              title="Edit the module directly. Applying re-reads it into the graph; node positions survive."
            >
              Edit
            </button>
            <button
              type="button"
              className="mg-button"
              onClick={() => void navigator.clipboard?.writeText(source)}
              disabled={!source}
            >
              Copy
            </button>
          </>
        )}
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

      {syncState.status === 'error' && syncState.message && (
        <div className="mg-code-error">
          <strong>Could not read that:</strong> {syncState.message}
          <span className="mg-code-error-note">
            Your graph is untouched and your edit is still here. A file is read whole or not at all
            — half a graph silently replacing a whole one is the worst outcome available.
          </span>
        </div>
      )}

      {syncState.status === 'ok' && syncState.message && (
        <div className="mg-code-note">{syncState.message}</div>
      )}

      {!error && !source && <div className="mg-code-empty">Nothing to generate yet.</div>}

      {source &&
        (editing ? (
          <textarea
            className="mg-code-edit"
            spellCheck={false}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
                e.preventDefault();
                apply();
              }
            }}
            aria-label="Generated module source"
          />
        ) : (
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
        ))}
    </div>
  );
}
