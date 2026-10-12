/**
 * "Save to Knowledge" popover — what the browser's star opens.
 *
 * A bookmark and a knowledge-base item are one record now (the bookmarks strip shows
 * the *starred* items of the knowledge base), so this is where a page is saved with
 * how much of it to keep — just the link, its text for search, or an offline copy —
 * and, when the site has an import profile, where the whole site or course can be
 * pulled in. For a page already saved it shows its state instead: reading status,
 * done, unpin.
 */
import { useEffect, useRef, useState } from 'react';

import { registry } from '../../../registry';
import { subscribeChannel, type WsMessage } from '../../../ws';
import {
  getItem,
  saveItem,
  setIndexMode,
  updateItem,
  type IndexMode,
  type Item,
  type Kind,
  type Profile,
} from '../../kb/api';

const KIND_CHOICES: Kind[] = [
  'article',
  'bookmark',
  'paper',
  'video',
  'course',
  'lesson',
  'primer',
];
const MODE_LABEL: Record<Exclude<IndexMode, 'site'>, string> = {
  link: 'Link only',
  text: 'Text — searchable by you and the agent',
  archive: 'Text + offline copy',
};

const card: React.CSSProperties = {
  position: 'absolute',
  top: 8,
  right: 8,
  zIndex: 4,
  width: 320,
  maxWidth: 'calc(100% - 16px)',
  // A browser pane can be shorter than the form; scroll inside rather than clip.
  maxHeight: 'calc(100% - 16px)',
  overflowY: 'auto',
  padding: 'var(--space-5)',
  background: 'var(--bg-raised)',
  border: '1px solid var(--border)',
  borderTop: '2px solid var(--accent)',
  boxShadow: 'var(--shadow-overlay)',
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-3)',
  fontSize: 'var(--fs-body)',
};

const label: React.CSSProperties = {
  fontSize: 'var(--fs-meta)',
  fontWeight: 700,
  letterSpacing: '0.14em',
  textTransform: 'uppercase',
  color: 'var(--text-faint)',
};

const mono: React.CSSProperties = {
  fontFamily: 'var(--font-mono)',
  fontSize: 'var(--fs-label)',
  color: 'var(--text-faint)',
};

const field: React.CSSProperties = { width: '100%', padding: '0 0.6rem' };

export function SaveToKnowledge(props: {
  url: string;
  title: string;
  saved: Item | null;
  profile: Profile | null;
  onChanged: (item: Item | null) => void;
  onClose: () => void;
}) {
  const { url, saved, profile } = props;
  const [title, setTitle] = useState(props.title);
  const [kind, setKind] = useState<Kind>('article');
  const [mode, setMode] = useState<Exclude<IndexMode, 'site'>>('text');
  const [collections, setCollections] = useState('');
  const [notes, setNotes] = useState('');
  const [pin, setPin] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Untouched fields follow the site's import profile, which arrives after the
  // popover opens: an aman.ai page is a primer in "ml", not an article in nothing.
  const [kindTouched, setKindTouched] = useState(false);
  const [collectionsTouched, setCollectionsTouched] = useState(false);

  useEffect(() => setTitle(props.title), [props.title]);

  // A save returns while the text is still being extracted and embedded; follow the
  // library source so "index queued" becomes "ready" (or "failed", with the reason)
  // without reopening the popover.
  const sourceId = saved?.source_id ?? null;
  const savedId = saved?.id ?? null;
  const onChanged = useRef(props.onChanged);
  onChanged.current = props.onChanged;
  useEffect(() => {
    if (!sourceId || !savedId) return;
    return subscribeChannel('library', (msg: WsMessage) => {
      if (msg.event !== 'source') return;
      if ((msg.data as { id?: string } | undefined)?.id !== sourceId) return;
      getItem(savedId)
        .then((item) => onChanged.current(item))
        .catch(() => {});
    });
  }, [sourceId, savedId]);
  useEffect(() => {
    if (!profile) return;
    const pageKind = profile.page_kind as Kind | undefined;
    if (!kindTouched && pageKind && KIND_CHOICES.includes(pageKind)) setKind(pageKind);
    if (!collectionsTouched && profile.collections?.length) {
      setCollections(profile.collections.join(', '));
    }
  }, [profile, kindTouched, collectionsTouched]);

  const run = async (fn: () => Promise<Item | null>) => {
    setBusy(true);
    setError(null);
    try {
      props.onChanged(await fn());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const importSite = () => {
    registry.openPanel('kb.browse', { params: { import: url } });
    props.onClose();
  };

  return (
    <div style={card} role="dialog" aria-label="Save to Knowledge">
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <span style={{ ...label, color: 'var(--text-strong)', flex: 1 }}>
          {saved ? 'In your knowledge base' : 'Save to Knowledge'}
        </span>
        <button type="button" className="btn-mini" onClick={props.onClose} aria-label="Close">
          ×
        </button>
      </div>

      {saved ? (
        <>
          <div style={{ fontWeight: 600, color: 'var(--text-strong)' }}>{saved.title}</div>
          <div style={mono}>
            {saved.kind} · {saved.status.replace('_', ' ')} · index {saved.index_status}
            {saved.collections.length ? ` · ${saved.collections.join(', ')}` : ''}
          </div>
          {saved.index_status === 'failed' && saved.index_error && (
            <div style={{ color: 'var(--danger)', fontSize: 'var(--fs-label)' }}>
              {saved.index_error}
            </div>
          )}
          <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
            <button
              type="button"
              disabled={busy || saved.status === 'done'}
              onClick={() => run(() => updateItem(saved.id, { status: 'done' }))}
            >
              Mark done
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => run(() => updateItem(saved.id, { starred: !saved.starred }))}
            >
              {saved.starred ? 'Unpin from bar' : 'Pin to bar'}
            </button>
            <button
              type="button"
              onClick={() => {
                registry.openPanel('kb.browse', {
                  params: { itemId: saved.parent_id ?? saved.id },
                });
                props.onClose();
              }}
            >
              Open in Knowledge
            </button>
          </div>
          {saved.index_mode !== 'site' && (
            <select
              value={saved.index_mode}
              disabled={busy}
              onChange={(e) => run(() => setIndexMode(saved.id, e.target.value as IndexMode))}
              style={field}
            >
              {(Object.keys(MODE_LABEL) as Exclude<IndexMode, 'site'>[]).map((m) => (
                <option key={m} value={m}>
                  {MODE_LABEL[m]}
                </option>
              ))}
            </select>
          )}
        </>
      ) : (
        <>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            style={field}
          />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
            <select
              value={kind}
              onChange={(e) => {
                setKindTouched(true);
                setKind(e.target.value as Kind);
              }}
              style={field}
            >
              {KIND_CHOICES.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={collections}
              placeholder="collections"
              onChange={(e) => {
                setCollectionsTouched(true);
                setCollections(e.target.value);
              }}
              style={field}
            />
          </div>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
            style={field}
          >
            {(Object.keys(MODE_LABEL) as Exclude<IndexMode, 'site'>[]).map((m) => (
              <option key={m} value={m}>
                {MODE_LABEL[m]}
              </option>
            ))}
          </select>
          <textarea
            value={notes}
            placeholder="Why it's worth keeping (optional)"
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            style={{ ...field, padding: 'var(--space-2) 0.6rem', resize: 'vertical' }}
          />
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-3)',
              color: 'var(--text-dim)',
            }}
          >
            <input type="checkbox" checked={pin} onChange={(e) => setPin(e.target.checked)} />
            Pin to the bookmarks bar
          </label>
          <button
            type="button"
            disabled={busy}
            style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
            onClick={() =>
              run(() =>
                saveItem({
                  url,
                  title: title.trim() || undefined,
                  kind,
                  collections: collections
                    .split(',')
                    .map((c) => c.trim())
                    .filter(Boolean),
                  notes: notes.trim() || undefined,
                  starred: pin,
                  index_mode: mode,
                }),
              )
            }
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      )}

      {profile && (
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 'var(--space-3)' }}>
          <div style={mono}>this site has an import profile: {profile.label ?? profile.id}</div>
          <button type="button" style={{ marginTop: 'var(--space-2)' }} onClick={importSite}>
            Import the whole {profile.id === 'apxml-course' ? 'course' : 'site'}…
          </button>
        </div>
      )}
      {error && <div style={{ color: 'var(--danger)', fontSize: 'var(--fs-label)' }}>{error}</div>}
    </div>
  );
}
