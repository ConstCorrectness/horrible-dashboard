/**
 * The capture form: seed URL, scope and bounds for a new doc set.
 *
 * A form rather than a prompt because the two numbers matter and their defaults are
 * guesses. `prefix` is what stops a crawl of a framework's docs wandering into its
 * marketing site, and the backend's default (the seed's own directory) is right most
 * of the time and wrong loudly when it isn't — so it is shown, editable, and
 * explained rather than hidden.
 */
import { useState } from 'react';

import { toastsStore } from '../../../toasts';
import { createSet, type DocSet } from '../api';

const CONTROL_HEIGHT = 30;

const inputStyle: React.CSSProperties = {
  height: CONTROL_HEIGHT,
  width: '100%',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border)',
  background: 'var(--bg-inset)',
  color: 'var(--text)',
  padding: '0 var(--space-5)',
  fontSize: '0.8125rem',
  fontFamily: 'inherit',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '0.625rem',
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-faint)',
  marginBottom: 'var(--space-3)',
};

const hintStyle: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: '0.6875rem',
  color: 'var(--text-faint)',
  marginTop: 'var(--space-2)',
};

export function NewDocSet(props: { onCancel: () => void; onCreated: (doc: DocSet) => void }) {
  const [seedUrl, setSeedUrl] = useState('');
  const [title, setTitle] = useState('');
  const [prefix, setPrefix] = useState('');
  const [maxPages, setMaxPages] = useState(200);
  const [maxDepth, setMaxDepth] = useState(6);
  const [busy, setBusy] = useState(false);

  async function submit() {
    const seed = seedUrl.trim();
    if (!seed) return;
    setBusy(true);
    try {
      const doc = await createSet({
        seed_url: seed,
        title: title.trim() || undefined,
        prefix: prefix.trim() || undefined,
        max_pages: maxPages,
        max_depth: maxDepth,
      });
      toastsStore.add('info', 'Capturing docs', doc.title);
      props.onCreated(doc);
    } catch (err) {
      toastsStore.add(
        'warning',
        'Could not start capture',
        err instanceof Error ? err.message : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        height: '100%',
        overflowY: 'auto',
        background: 'var(--bg)',
        borderTop: '2px solid var(--accent)',
        padding: 'var(--space-7)',
      }}
    >
      <div style={{ maxWidth: 520, display: 'grid', gap: 'var(--space-6)' }}>
        <div>
          <h2
            style={{
              margin: 0,
              fontSize: '0.75rem',
              fontWeight: 700,
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
              color: 'var(--text-strong)',
            }}
          >
            Capture a doc set
          </h2>
          <p
            style={{
              margin: 'var(--space-3) 0 0',
              fontSize: '0.8125rem',
              color: 'var(--text-dim)',
            }}
          >
            Every page is loaded in a real browser and saved with its stylesheets, fonts and
            scripts, so it reads offline the way it reads online. The text is also indexed, so the
            set is searchable and the agent can quote it.
          </p>
        </div>

        <div>
          <label style={labelStyle} htmlFor="docviewer-seed">
            Seed URL
          </label>
          <input
            id="docviewer-seed"
            value={seedUrl}
            placeholder="https://docs.example.dev/getting-started"
            onChange={(e) => setSeedUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void submit();
            }}
            style={inputStyle}
          />
          <div style={hintStyle}>where the crawl starts</div>
        </div>

        <div>
          <label style={labelStyle} htmlFor="docviewer-prefix">
            Scope
          </label>
          <input
            id="docviewer-prefix"
            value={prefix}
            placeholder="defaults to the seed's own directory"
            onChange={(e) => setPrefix(e.target.value)}
            style={inputStyle}
          />
          <div style={hintStyle}>only URLs under this prefix are captured</div>
        </div>

        <div>
          <label style={labelStyle} htmlFor="docviewer-title">
            Name
          </label>
          <input
            id="docviewer-title"
            value={title}
            placeholder="defaults to the site's hostname"
            onChange={(e) => setTitle(e.target.value)}
            style={inputStyle}
          />
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-5)' }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle} htmlFor="docviewer-max-pages">
              Page limit
            </label>
            <input
              id="docviewer-max-pages"
              type="number"
              min={1}
              max={2000}
              value={maxPages}
              onChange={(e) => setMaxPages(Number(e.target.value) || 1)}
              style={inputStyle}
            />
          </div>
          <div style={{ flex: 1 }}>
            <label style={labelStyle} htmlFor="docviewer-max-depth">
              Link depth
            </label>
            <input
              id="docviewer-max-depth"
              type="number"
              min={0}
              max={20}
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value) || 0)}
              style={inputStyle}
            />
          </div>
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-4)' }}>
          <button
            type="button"
            disabled={busy || !seedUrl.trim()}
            onClick={() => void submit()}
            style={{
              height: CONTROL_HEIGHT,
              padding: '0 var(--space-6)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--accent)',
              background: 'var(--accent)',
              color: 'var(--accent-contrast)',
              fontSize: '0.8125rem',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {busy ? 'Starting…' : 'Capture'}
          </button>
          <button
            type="button"
            onClick={props.onCancel}
            style={{
              height: CONTROL_HEIGHT,
              padding: '0 var(--space-6)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border)',
              background: 'transparent',
              color: 'var(--text-dim)',
              fontSize: '0.8125rem',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
