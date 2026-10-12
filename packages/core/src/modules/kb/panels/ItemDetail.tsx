/**
 * The right column of the Knowledge pane: one item, its reading state and notes,
 * how much of it is kept (index mode), and — for a course or an imported site —
 * the tree of lessons with a checkbox each.
 */
import { useEffect, useState } from 'react';

import { toastsStore } from '../../../toasts';
import {
  deleteItem,
  setIndexMode,
  startImport,
  reindexImport,
  stopImport,
  updateItem,
  type IndexMode,
  type Item,
  type Status,
  type TreeNode,
} from '../api';
import {
  BackIcon,
  CheckIcon,
  ChevronIcon,
  ExternalIcon,
  ImportIcon,
  KindIcon,
  OfflineIcon,
  StarIcon,
  StopIcon,
  TrashIcon,
} from '../icons';
import { hasOffline, openItem } from '../open';
import { remove, select, upsert, type ImportProgress } from '../store';
import { RollingNumber } from './RollingNumber';

/** The local calendar day of a backend timestamp. SQLite's `datetime('now')` is UTC
 *  with no zone marker, so slicing it would show tomorrow's date every evening. */
function localDay(stamp: string): string {
  const iso = stamp.includes('T') ? stamp : stamp.replace(' ', 'T');
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(date.getTime()) ? stamp.slice(0, 10) : date.toLocaleDateString('en-CA');
}

const STATUS_LABEL: Record<Status, string> = {
  to_read: 'To read',
  reading: 'Reading',
  done: 'Done',
  archived: 'Archived',
};

const INDEX_LABEL: Record<Exclude<IndexMode, 'site'>, string> = {
  link: 'Link only',
  text: 'Text — searchable',
  archive: 'Text + offline copy',
};

function stagger(i: number): React.CSSProperties {
  return { ['--kb-delay' as string]: `${Math.min(i * 14, 260)}ms` };
}

function host(url?: string | null): string {
  if (!url) return '';
  try {
    return new URL(url).host.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function warn(title: string, e: unknown): void {
  toastsStore.add('warning', title, e instanceof Error ? e.message : String(e));
}

export function ItemDetail(props: {
  item: Item;
  tree: TreeNode | null;
  progress: ImportProgress | null;
}) {
  const { item, tree, progress } = props;
  const [notes, setNotes] = useState(item.notes);
  const [collections, setCollections] = useState(item.collections.join(', '));
  const [tags, setTags] = useState(item.tags.join(', '));
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Re-seed the editable fields when another item is selected (or this one is
  // changed elsewhere) — but not while the user is mid-edit in the same item.
  useEffect(() => {
    setNotes(item.notes);
    setCollections(item.collections.join(', '));
    setTags(item.tags.join(', '));
    setConfirmDelete(false);
    // Keyed on identity + server values; `item` itself changes on every emit.
  }, [item.id, item.notes, item.collections, item.tags]);

  const patch = (fields: Parameters<typeof updateItem>[1]) =>
    updateItem(item.id, fields)
      .then(upsert)
      .catch((e) => warn('Could not save', e));

  const list = (text: string) =>
    text
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);

  const isSite = item.index_mode === 'site' && Boolean(item.docset_id) && !item.parent_id;
  const parentLike = item.child_count > 0 || item.kind === 'course' || item.kind === 'site';
  const pct = item.progress?.total
    ? Math.round((100 * item.progress.done) / item.progress.total)
    : 0;

  return (
    <div className="kb-detail">
      <div className="kb-detail-head">
        {/* Shown only when the pane is too narrow for three columns and the detail
            stands in for the list (kb.css). */}
        <button
          type="button"
          className="kb-back kb-mono"
          onClick={() => void select(null)}
          aria-label="Back to the list"
        >
          <BackIcon size={12} />
          list
        </button>
        <div
          className="kb-mono"
          style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}
        >
          <span style={{ color: 'var(--accent)', display: 'inline-flex' }}>
            <KindIcon kind={item.kind} size={13} />
          </span>
          <span style={{ textTransform: 'uppercase', letterSpacing: '0.1em' }}>{item.kind}</span>
          {item.collections.length > 0 && <span>· {item.collections.join(' · ')}</span>}
        </div>
        <h2 className="kb-detail-title">{item.title}</h2>
        {item.url && (
          <div
            className="kb-mono"
            title={item.url}
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {item.url}
          </div>
        )}
        <div className="kb-actions">
          {item.url && (
            <button
              type="button"
              onClick={() => openItem(item, 'live')}
              title="Open the live page in the browser"
            >
              <ExternalIcon size={13} />
              Open
            </button>
          )}
          {hasOffline(item) && (
            <button
              type="button"
              onClick={() => openItem(item, 'offline')}
              title="Read the archived copy offline"
            >
              <OfflineIcon size={13} />
              Offline
            </button>
          )}
          <button
            type="button"
            onClick={() => patch({ starred: !item.starred })}
            title={
              item.starred
                ? 'Remove from the browser bookmarks bar'
                : 'Pin to the browser bookmarks bar'
            }
            style={item.starred ? { color: 'var(--accent)' } : undefined}
          >
            <StarIcon size={13} filled={item.starred} />
          </button>
          <button
            type="button"
            title={
              isSite
                ? 'Delete this import: its archives, its indexed text and every item under it'
                : 'Delete this item and its indexed text'
            }
            style={
              confirmDelete ? { borderColor: 'var(--danger)', color: 'var(--danger)' } : undefined
            }
            onClick={() => {
              if (!confirmDelete) {
                setConfirmDelete(true);
                return;
              }
              deleteItem(item.id)
                .then(() => remove([item.id]))
                .catch((e) => warn('Delete failed', e));
            }}
          >
            <TrashIcon size={13} />
            {confirmDelete ? 'Confirm' : null}
          </button>
        </div>
      </div>

      <div className="kb-detail-scroll">
        <div className="kb-label">Status</div>
        <div className="kb-segment" role="group" aria-label="Reading status">
          {(Object.keys(STATUS_LABEL) as Status[]).map((s) => (
            <button
              key={s}
              type="button"
              className="kb-segment-btn"
              aria-pressed={item.status === s}
              onClick={() => item.status !== s && patch({ status: s })}
            >
              {STATUS_LABEL[s]}
            </button>
          ))}
        </div>

        {parentLike && item.progress && item.progress.total > 0 ? (
          <div className="kb-stats">
            <Stat label="Done" value={item.progress.done} />
            <Stat label="Lessons" value={item.progress.total} />
            <Stat label="Complete %" value={pct} />
          </div>
        ) : (
          item.chunk_count > 0 && (
            <div className="kb-stats" style={{ gridTemplateColumns: '1fr' }}>
              <Stat label="Passages indexed" value={item.chunk_count} />
            </div>
          )
        )}

        <div className="kb-label">
          Kept as
          <span className={`kb-dot`} data-state={item.index_status} />
          <span className="kb-mono">{item.index_status}</span>
        </div>
        {isSite ? (
          <SiteControls item={item} progress={progress} />
        ) : (
          item.url &&
          item.index_mode !== 'site' && (
            <div className="kb-field">
              <select
                value={item.index_mode}
                onChange={(e) =>
                  setIndexMode(item.id, e.target.value as IndexMode)
                    .then(upsert)
                    .catch((err) => warn('Could not change', err))
                }
              >
                {(Object.keys(INDEX_LABEL) as Exclude<IndexMode, 'site'>[]).map((m) => (
                  <option key={m} value={m}>
                    {INDEX_LABEL[m]}
                  </option>
                ))}
              </select>
            </div>
          )
        )}
        {item.index_error && <div className="kb-error">{item.index_error}</div>}
        {item.content_signal?.replace(/\s/g, '').includes('ai-input=no') && (
          <div className="kb-note">
            This site asks not to be used as AI input (robots.txt Content-Signal), so its text is
            archived but not indexed for the agent.
          </div>
        )}

        <div className="kb-label">Collections</div>
        <div className="kb-field">
          <input
            type="text"
            value={collections}
            placeholder="ml, llm-systems"
            onChange={(e) => setCollections(e.target.value)}
            onBlur={() => {
              const next = list(collections);
              if (next.join(',') !== item.collections.join(',')) void patch({ collections: next });
            }}
            onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          />
        </div>
        <div className="kb-label">Tags</div>
        <div className="kb-field">
          <input
            type="text"
            value={tags}
            placeholder="attention, kv-cache"
            onChange={(e) => setTags(e.target.value)}
            onBlur={() => {
              const next = list(tags);
              if (next.join(',') !== item.tags.join(',')) void patch({ tags: next });
            }}
            onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
          />
        </div>

        <div className="kb-label">Notes</div>
        <div className="kb-field">
          <textarea
            value={notes}
            placeholder="Why it's worth keeping, what to revisit…"
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => notes !== item.notes && void patch({ notes })}
          />
        </div>

        {tree && tree.children.length > 0 && (
          <>
            <div className="kb-label">
              {item.kind === 'course' ? 'Chapters' : 'Contents'}
              <span className="kb-mono">
                {item.progress
                  ? `${item.progress.done}/${item.progress.total}`
                  : tree.children.length}
              </span>
            </div>
            <Tree nodes={tree.children} depth={0} />
          </>
        )}
        <div className="kb-mono" style={{ padding: 'var(--space-5) var(--space-6) 0' }}>
          added {localDay(item.added_at)}
          {item.completed_at ? ` · finished ${localDay(item.completed_at)}` : ''}
          {item.library ? ` · ${item.library}` : ''}
          {item.url ? ` · ${host(item.url)}` : ''}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="kb-stat">
      <div className="kb-stat-value">
        <RollingNumber value={value} />
      </div>
      <div className="kb-mono" style={{ textTransform: 'uppercase', letterSpacing: '0.1em' }}>
        {label}
      </div>
    </div>
  );
}

function SiteControls({ item, progress }: { item: Item; progress: ImportProgress | null }) {
  if (progress?.status === 'indexing') {
    return (
      <div className="kb-field">
        <div className="kb-mono" style={{ color: 'var(--text-dim)' }}>
          re-indexing from the archives · <RollingNumber value={progress.captured} /> of{' '}
          {progress.captured + progress.queued} pages
        </div>
      </div>
    );
  }
  if (progress) {
    return (
      <div className="kb-field">
        <div className="kb-mono" style={{ color: 'var(--text-dim)' }}>
          importing · <RollingNumber value={progress.captured} /> captured
          {progress.failed ? ` · ${progress.failed} failed` : ''} · {progress.queued} queued
        </div>
        {progress.current_url && (
          <div
            className="kb-mono"
            title={progress.current_url}
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {progress.current_url}
          </div>
        )}
        <div className="kb-actions">
          <button
            type="button"
            onClick={() => stopImport(item.id).catch((e) => warn('Could not stop', e))}
          >
            <StopIcon size={12} />
            Stop
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="kb-field">
      <div className="kb-mono">imported site · {item.child_count} pages · offline + indexed</div>
      <div className="kb-actions">
        <button
          type="button"
          title="Capture pages that are missing (resumes; already-archived pages are skipped)"
          onClick={() =>
            startImport({ profile_id: item.profile_id ?? undefined, url: item.url ?? undefined })
              .then(() => toastsStore.add('info', 'Import resumed', item.title))
              .catch((e) => warn('Could not resume', e))
          }
        >
          <ImportIcon size={13} />
          Resume / update
        </button>
        <button
          type="button"
          title="Re-extract and re-embed every page from its offline copy, without fetching the site again — after an extraction fix, or once the embedder is back"
          onClick={() =>
            reindexImport(item.id)
              .then(() => toastsStore.add('info', 'Re-indexing from the archives', item.title))
              .catch((e) => warn('Could not re-index', e))
          }
        >
          Re-index
        </button>
      </div>
    </div>
  );
}

function Tree({ nodes, depth }: { nodes: TreeNode[]; depth: number }) {
  return (
    <>
      {nodes.map((node, i) => (
        <TreeRow key={node.item.id} node={node} depth={depth} index={i} />
      ))}
    </>
  );
}

function TreeRow({ node, depth, index }: { node: TreeNode; depth: number; index: number }) {
  const [open, setOpen] = useState(true);
  const { item } = node;
  const group = node.children.length > 0;
  const done = item.status === 'done';

  const toggleDone = () =>
    updateItem(item.id, { status: done ? 'reading' : 'done' })
      .then(upsert)
      .catch((e) => warn('Could not update', e));

  return (
    <>
      <div
        className="kb-tree-row"
        data-depth={Math.min(depth, 2)}
        data-done={done}
        style={stagger(index)}
      >
        {group ? (
          <button
            type="button"
            className="kb-tree-open"
            aria-label={open ? 'Collapse' : 'Expand'}
            onClick={() => setOpen((o) => !o)}
          >
            <ChevronIcon open={open} />
          </button>
        ) : (
          <input
            type="checkbox"
            checked={done}
            onChange={toggleDone}
            aria-label={`Mark "${item.title}" ${done ? 'not done' : 'done'}`}
          />
        )}
        <span
          style={{ color: done ? 'var(--success)' : 'var(--text-faint)', display: 'inline-flex' }}
        >
          {done ? <CheckIcon size={13} /> : <KindIcon kind={item.kind} size={13} />}
        </span>
        <span className={`kb-tree-title${group ? ' kb-tree-group' : ''}`} title={item.title}>
          {item.title}
          {group && item.progress ? (
            <span className="kb-mono" style={{ marginLeft: 'var(--space-3)' }}>
              {item.progress.done}/{item.progress.total}
            </span>
          ) : null}
        </span>
        <span style={{ display: 'inline-flex', gap: 'var(--space-1)' }}>
          {hasOffline(item) && (
            <button
              type="button"
              className="kb-tree-open"
              title="Read offline"
              onClick={() => openItem(item, 'offline')}
            >
              <OfflineIcon size={12} />
            </button>
          )}
          {item.url && (
            <button
              type="button"
              className="kb-tree-open"
              title="Open live"
              onClick={() => openItem(item, 'live')}
            >
              <ExternalIcon size={12} />
            </button>
          )}
        </span>
      </div>
      {group && open && <Tree nodes={node.children} depth={depth + 1} />}
    </>
  );
}
