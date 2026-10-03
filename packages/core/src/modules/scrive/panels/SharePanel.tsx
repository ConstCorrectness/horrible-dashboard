/**
 * Share a page: compose an X thread, a LinkedIn link post or a YouTube upload, or
 * cross-post the whole article to dev.to or Hashnode; see what preflight says, and
 * approve, send or schedule it.
 *
 * Each composer edits one outbox row (backend/modules/scrive/outbox.py). Edits are
 * saved as you type (debounced) and checked as they are saved, so the counts, the
 * cost and the findings are always the ones approval will see. The agent's drafts
 * (`scrive.draftSocial`) appear in the list with its note, to review like any other.
 *
 * Nothing leaves without three clicks of a person's: approve (preflight, link filled
 * in, payload frozen), then send now or schedule. A row that failed with an unknown
 * outcome says so before Retry posts it again.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';

import { openExternal } from '../../../external';
import { usePaneParams } from '../../../panes';
import { useSetting } from '../../../settings';
import { subscribeChannel } from '../../../ws';
import {
  approveOutbox,
  checkOutbox,
  connectedTargets,
  createOutbox,
  deleteOutbox,
  editOutbox,
  getPublishState,
  listOutbox,
  readPage,
  retryOutbox,
  scheduleOutbox,
  SCRIVE_CHANNEL,
  sendOutbox,
  unscheduleOutbox,
  type DevtoPayload,
  type HashnodePayload,
  type LinkedInPayload,
  type OutboxChanged,
  type OutboxItem,
  type OutboxPayload,
  type OutboxTarget,
  type PublishFinding,
  type XPayload,
  type YouTubePayload,
} from '../api';
import { CheckIcon, CloseIcon, ExternalIcon, PlusIcon, PublishIcon } from '../icons';
import { pageDir } from '../site/urls';
import { POST_URL, X_LIMIT, xCost, xLength, xThread } from '../social/xcount';
import { TargetIcon, TARGET_LABEL, when } from './outbox-ui';
import '../scrive.css';

const SAVE_DEBOUNCE_MS = 600;
const EDITABLE = new Set(['draft', 'failed']);

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** An ISO time for a `datetime-local` input, and back. */
function toLocalInput(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(local: string): string {
  if (!local) return '';
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? '' : d.toISOString().replace('.000Z', 'Z');
}

export function SharePanel() {
  const params = usePaneParams();
  const site = String(params.site ?? '');
  // No path: the site's posts that belong to no page (a clip's render, shared on
  // its own).
  const path = String(params.path ?? '');
  if (!site) {
    return <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>No site.</div>;
  }
  return <Share key={`${site}/${path}`} site={site} page={path} />;
}

function Share({ site, page }: { site: string; page: string }) {
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [title, setTitle] = useState(page || `${site} — no page`);
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [connected, setConnected] = useState<Record<OutboxTarget, boolean> | null>(null);
  const [progress, setProgress] = useState<Record<string, OutboxChanged>>({});
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listOutbox({ site, page }).then(
      (rows) => {
        setItems(rows);
        setSelected((s) => (s && rows.some((r) => r.id === s) ? s : (rows[0]?.id ?? null)));
      },
      (e: unknown) => setError(message(e)),
    );
  }, [site, page]);

  useEffect(() => {
    refresh();
    connectedTargets().then(setConnected, () => {});
    if (!page) return;
    readPage(site, page).then(
      (p) => setTitle(p.meta.title),
      () => {},
    );
    getPublishState(site).then(
      (state) => {
        const dir = pageDir(page);
        const live =
          state.record?.mode === 'static' && state.record.files.includes(`${dir}index.html`);
        setLiveUrl(live && state.record ? state.record.url + dir : null);
      },
      () => {},
    );
  }, [site, page, refresh]);

  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'outbox.changed') return;
        const event = msg.data as OutboxChanged;
        if (event.site !== site || event.page !== page) return;
        setProgress((p) => ({ ...p, [event.id]: event }));
        if (event.label === undefined) refresh();
      }),
    [site, page, refresh],
  );

  const create = async (target: OutboxTarget) => {
    try {
      const item = await createOutbox(site, page, target);
      setItems((rows) => [item, ...rows]);
      setSelected(item.id);
      setError(null);
    } catch (e) {
      setError(message(e));
    }
  };

  const current = items.find((i) => i.id === selected) ?? null;
  const replace = (item: OutboxItem) =>
    setItems((rows) => rows.map((r) => (r.id === item.id ? item : r)));

  return (
    <div className="scrive-publish">
      <header className="scrive-outline-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head">Share · {title}</div>
          <div
            className="scrive-meta"
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {!page
              ? 'posts that belong to no page, such as a clip shared on its own'
              : liveUrl
                ? `live at ${liveUrl}`
                : 'not on the published site yet: a post linking to it cannot be approved'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          {(['x', 'linkedin', 'youtube', 'devto', 'hashnode'] as const).map((target) => (
            <button key={target} type="button" onClick={() => void create(target)}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <PlusIcon /> {TARGET_LABEL[target]}
              </span>
            </button>
          ))}
        </div>
      </header>

      {error && (
        <div className="scrive-publish-error" role="alert">
          {error}
        </div>
      )}

      <div className="scrive-publish-body">
        <div className="scrive-publish-main">
          {current ? (
            <Composer
              key={current.id}
              item={current}
              connected={connected?.[current.target] ?? true}
              progress={progress[current.id]}
              onItem={replace}
              onDeleted={() => {
                setItems((rows) => rows.filter((r) => r.id !== current.id));
                setSelected(null);
              }}
              onError={setError}
            />
          ) : (
            <p className="scrive-meta">
              Nothing drafted for this page yet. Start a draft above, or ask the agent to draft one.
            </p>
          )}
        </div>
        <aside className="scrive-publish-side">
          <section className="scrive-publish-section">
            <div className="scrive-publish-section-head">
              <span className="scrive-head">{page ? "This page's posts" : 'Posts'}</span>
              <span className="scrive-meta">{items.length}</span>
            </div>
            <ul className="scrive-publish-pages">
              {items.map((item, i) => (
                <li
                  key={item.id}
                  className="scrive-publish-page"
                  style={{ '--i': Math.min(i, 10) } as CSSProperties}
                >
                  <button
                    type="button"
                    className="scrive-row"
                    aria-current={item.id === selected}
                    onClick={() => setSelected(item.id)}
                  >
                    <TargetIcon target={item.target} />
                    <span
                      style={{
                        flex: 1,
                        minWidth: 0,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {summary(item)}
                    </span>
                    {item.created_by === 'agent' && <span className="scrive-chip">agent</span>}
                    <span className="scrive-chip" data-status={item.status}>
                      {item.status}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

function summary(item: OutboxItem): string {
  const p = item.payload as Partial<
    XPayload & LinkedInPayload & YouTubePayload & DevtoPayload & HashnodePayload
  >;
  const text =
    item.target === 'x' ? p.posts?.[0]?.text : item.target === 'linkedin' ? p.text : p.title;
  return (text ?? '').replace(/\s+/g, ' ').trim() || '(empty)';
}

function Composer({
  item,
  connected,
  progress,
  onItem,
  onDeleted,
  onError,
}: {
  item: OutboxItem;
  connected: boolean;
  progress?: OutboxChanged;
  onItem: (item: OutboxItem) => void;
  onDeleted: () => void;
  onError: (message: string | null) => void;
}) {
  const [payload, setPayload] = useState<OutboxPayload>(item.payload);
  const [findings, setFindings] = useState<PublishFinding[]>(item.findings);
  const [busy, setBusy] = useState(false);
  const [scheduleAt, setScheduleAt] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const editable = EDITABLE.has(item.status);
  const linkInReply = useSetting<boolean>('scrive.x.linkInReply') ?? true;

  // A row changed elsewhere (the runner, another window) while not being edited.
  useEffect(() => {
    if (!timer.current) setPayload(item.payload);
  }, [item.payload]);

  const check = useCallback(() => {
    checkOutbox(item.id).then(setFindings, () => {});
  }, [item.id]);
  useEffect(() => {
    if (editable) check();
  }, [editable, check]);

  const save = useCallback(
    async (next: OutboxPayload) => {
      timer.current = null;
      try {
        onItem(await editOutbox(item.id, next));
        check();
      } catch (e) {
        onError(message(e));
      }
    },
    [item.id, onItem, onError, check],
  );

  const change = (next: OutboxPayload) => {
    setPayload(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(next), SAVE_DEBOUNCE_MS);
  };

  const act = async (fn: () => Promise<OutboxItem | { item: OutboxItem; approved: boolean }>) => {
    setBusy(true);
    onError(null);
    try {
      if (timer.current) {
        clearTimeout(timer.current);
        await save(payload);
      }
      const out = await fn();
      const next = 'item' in out ? out.item : out;
      onItem(next);
      setFindings(next.findings);
    } catch (e) {
      onError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const blocking = findings.filter((f) => f.blocking);
  const hard = blocking.some((f) =>
    [
      'not-connected',
      'unpublished',
      'too-long',
      'empty',
      'missing-file',
      'media',
      'not-video',
      'bad-text',
      'publish-at',
      'tags',
    ].includes(f.rule),
  );
  const unknownStep = Object.values(item.steps).some((s) => s.status === 'unknown');

  let editor: ReactNode = null;
  if (item.target === 'x')
    editor = (
      <XEditor
        payload={payload as XPayload}
        onChange={change}
        readOnly={!editable}
        linkInReply={linkInReply}
      />
    );
  else if (item.target === 'linkedin')
    editor = (
      <LinkedInEditor payload={payload as LinkedInPayload} onChange={change} readOnly={!editable} />
    );
  else if (item.target === 'youtube')
    editor = (
      <YouTubeEditor payload={payload as YouTubePayload} onChange={change} readOnly={!editable} />
    );
  else
    editor = (
      <ArticleEditor
        target={item.target}
        payload={payload as DevtoPayload | HashnodePayload}
        onChange={change}
        readOnly={!editable}
      />
    );

  return (
    <section className="scrive-publish-section">
      <div className="scrive-publish-section-head">
        <span
          className="scrive-head"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <TargetIcon target={item.target} /> {TARGET_LABEL[item.target]}
        </span>
        <span className="scrive-meta">
          {item.status}
          {item.status === 'scheduled' && item.run_at ? ` for ${when(item.run_at)}` : ''}
          {item.created_by === 'agent' ? ' · drafted by the agent' : ''}
        </span>
      </div>
      {item.note && <p className="scrive-meta scrive-publish-note">agent: {item.note}</p>}
      {!connected && (
        <p className="scrive-publish-warn">
          {TARGET_LABEL[item.target]} isn't connected. Connect it from the home page's connectors.
        </p>
      )}

      <fieldset className="scrive-outline-fields" disabled={!editable || busy}>
        {editor}
      </fieldset>

      {findings.length > 0 && (
        <ul className="scrive-publish-findings">
          {findings.map((f, i) => (
            <li key={i} data-severity={f.blocking ? 'block' : f.severity}>
              {f.file && <span className="scrive-meta">{f.file}</span>}
              <span>{f.message}</span>
            </li>
          ))}
        </ul>
      )}

      {progress?.label && item.status === 'sending' && (
        <div className="scrive-publish-phase" role="status" aria-busy="true">
          <span>
            <span className="scrive-publish-pulse" /> {progress.label}
            {typeof progress.progress === 'number'
              ? ` · ${Math.round(progress.progress * 100)}%`
              : ''}
          </span>
        </div>
      )}
      {item.error && item.status !== 'sent' && (
        <p className="scrive-publish-warn" role="alert">
          {item.error}
        </p>
      )}
      {item.status === 'sent' && item.url && (
        <button
          type="button"
          className="scrive-publish-link"
          onClick={() => void openExternal(item.url)}
        >
          <CheckIcon size={12} /> Sent {item.sent_at ? when(item.sent_at) : ''} · {item.url}{' '}
          <ExternalIcon size={12} />
        </button>
      )}

      <div className="scrive-share-actions">
        {editable && (
          <>
            <button
              type="button"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
              disabled={busy}
              onClick={() => void act(() => approveOutbox(item.id))}
              title="Run the checks and fix the text as it will be sent"
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <CheckIcon /> Approve
              </span>
            </button>
            {blocking.length > 0 && !hard && (
              <button
                type="button"
                style={{ borderColor: 'var(--danger)', color: 'var(--danger)' }}
                disabled={busy}
                onClick={() => void act(() => approveOutbox(item.id, true))}
                title="Approve despite the findings above. They will be public."
              >
                Approve anyway
              </button>
            )}
          </>
        )}
        {(item.status === 'approved' || item.status === 'scheduled') && (
          <>
            <button
              type="button"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
              disabled={busy || !connected}
              onClick={() => void act(() => sendOutbox(item.id))}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <PublishIcon /> Send now
              </span>
            </button>
            <input
              type="datetime-local"
              aria-label="Send at"
              value={scheduleAt}
              onChange={(e) => setScheduleAt(e.target.value)}
              style={{ padding: '0 0.6rem' }}
            />
            <button
              type="button"
              disabled={busy || !scheduleAt}
              onClick={() =>
                void act(() => scheduleOutbox(item.id, new Date(scheduleAt).getTime() / 1000))
              }
            >
              {item.status === 'scheduled' ? 'Reschedule' : 'Schedule'}
            </button>
            {item.status === 'scheduled' && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void act(() => unscheduleOutbox(item.id))}
              >
                Unschedule
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => void act(() => editOutbox(item.id, payload))}
              title="Take the approval back and edit"
            >
              Edit
            </button>
          </>
        )}
        {item.status === 'failed' && (
          <button
            type="button"
            style={
              unknownStep ? { borderColor: 'var(--danger)', color: 'var(--danger)' } : undefined
            }
            disabled={busy}
            onClick={() => void act(() => retryOutbox(item.id))}
            title={
              unknownStep
                ? 'Part of this may already have been posted. Retry posts it again.'
                : 'Send it again from where it stopped'
            }
          >
            {unknownStep ? 'Retry (may post twice)' : 'Retry'}
          </button>
        )}
        {item.status !== 'sending' && (
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await deleteOutbox(item.id);
                onDeleted();
              } catch (e) {
                onError(message(e));
                setBusy(false);
              }
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <CloseIcon /> {item.status === 'sent' ? 'Remove from list' : 'Delete'}
            </span>
          </button>
        )}
      </div>
    </section>
  );
}

function Count({ value, limit }: { value: number; limit: number }) {
  return (
    <span
      className="scrive-meta"
      data-over={value > limit || undefined}
      style={value > limit ? { color: 'var(--danger)' } : undefined}
    >
      {value}/{limit}
    </span>
  );
}

function Field({
  label,
  aside,
  children,
}: {
  label: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="scrive-outline-field">
      <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
        <span className="scrive-head">{label}</span>
        {aside}
      </span>
      {children}
    </label>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="text"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={{ padding: '0 0.6rem' }}
    />
  );
}

function XEditor({
  payload,
  onChange,
  readOnly,
  linkInReply,
}: {
  payload: XPayload;
  onChange: (p: XPayload) => void;
  readOnly: boolean;
  linkInReply: boolean;
}) {
  const [adding, setAdding] = useState<Record<number, string>>({});
  const setPost = (i: number, patch: Partial<XPayload['posts'][number]>) =>
    onChange({
      ...payload,
      posts: payload.posts.map((p, j) => (j === i ? { ...p, ...patch } : p)),
    });
  const thread = useMemo(() => xThread(payload, linkInReply), [payload, linkInReply]);
  const cost = useMemo(() => xCost(payload, linkInReply), [payload, linkInReply]);
  const inReply = payload.link_in_reply ?? linkInReply;
  return (
    <>
      {payload.posts.map((post, i) => {
        // The first post's count includes an inline link.
        const sent =
          i === 0 && payload.link.trim() && !inReply ? (thread[0]?.text ?? post.text) : post.text;
        return (
          <div key={i} className="scrive-share-post">
            <Field
              label={payload.posts.length > 1 ? `Post ${i + 1}` : 'Post'}
              aside={<Count value={xLength(sent)} limit={X_LIMIT} />}
            >
              <textarea
                className="scrive-outline-text"
                rows={4}
                value={post.text}
                readOnly={readOnly}
                onChange={(e) => setPost(i, { text: e.target.value })}
              />
            </Field>
            <div className="scrive-share-media">
              {post.media.map((m) => (
                <span key={m} className="scrive-chip">
                  {m}
                  {!readOnly && (
                    <button
                      type="button"
                      className="btn-mini"
                      aria-label={`Remove ${m}`}
                      onClick={() => setPost(i, { media: post.media.filter((x) => x !== m) })}
                    >
                      <CloseIcon size={10} />
                    </button>
                  )}
                </span>
              ))}
              {!readOnly && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const path = (adding[i] ?? '').trim();
                    if (path) setPost(i, { media: [...post.media, path] });
                    setAdding((a) => ({ ...a, [i]: '' }));
                  }}
                >
                  <input
                    type="text"
                    aria-label="Attach a site file"
                    placeholder="attach media/… (Enter)"
                    value={adding[i] ?? ''}
                    onChange={(e) => setAdding((a) => ({ ...a, [i]: e.target.value }))}
                    style={{ padding: '0 0.6rem' }}
                  />
                </form>
              )}
              {!readOnly && payload.posts.length > 1 && (
                <button
                  type="button"
                  className="btn-mini"
                  aria-label="Remove this post"
                  title="Remove this post"
                  onClick={() =>
                    onChange({ ...payload, posts: payload.posts.filter((_, j) => j !== i) })
                  }
                >
                  <CloseIcon size={12} />
                </button>
              )}
            </div>
          </div>
        );
      })}
      {!readOnly && (
        <button
          type="button"
          className="scrive-outline-add"
          onClick={() =>
            onChange({ ...payload, posts: [...payload.posts, { text: '', media: [] }] })
          }
        >
          <PlusIcon size={12} /> add a post to the thread
        </button>
      )}
      <Field label="Link">
        <TextInput
          value={payload.link}
          onChange={(link) => onChange({ ...payload, link })}
          placeholder={POST_URL}
        />
      </Field>
      <label className="scrive-share-check">
        <input
          type="checkbox"
          checked={inReply}
          disabled={readOnly}
          onChange={(e) => onChange({ ...payload, link_in_reply: e.target.checked })}
        />
        Post the link as a reply, after the thread
      </label>
      <p className="scrive-meta scrive-publish-note">
        {cost.posts} post{cost.posts === 1 ? '' : 's'}
        {cost.withUrl ? `, ${cost.withUrl} with a link` : ''} · X charges about $
        {cost.usd.toFixed(2)}
      </p>
    </>
  );
}

function LinkedInEditor({
  payload,
  onChange,
  readOnly,
}: {
  payload: LinkedInPayload;
  onChange: (p: LinkedInPayload) => void;
  readOnly: boolean;
}) {
  const set = (patch: Partial<LinkedInPayload>) => onChange({ ...payload, ...patch });
  return (
    <>
      <Field label="Post" aside={<Count value={payload.text.length} limit={3000} />}>
        <textarea
          className="scrive-outline-text"
          rows={5}
          value={payload.text}
          readOnly={readOnly}
          onChange={(e) => set({ text: e.target.value })}
        />
      </Field>
      <Field label="Link">
        <TextInput value={payload.link} onChange={(link) => set({ link })} placeholder={POST_URL} />
      </Field>
      <div className="scrive-outline-pair">
        <Field label="Card title">
          <TextInput value={payload.title} onChange={(title) => set({ title })} />
        </Field>
        <Field label="Card image">
          <TextInput
            value={payload.thumbnail}
            onChange={(thumbnail) => set({ thumbnail })}
            placeholder="media/cover.png (optional)"
          />
        </Field>
      </div>
      <Field label="Card description">
        <TextInput value={payload.description} onChange={(description) => set({ description })} />
      </Field>
      <p className="scrive-meta scrive-publish-note">
        LinkedIn's API cannot post an article; this shares a link card to the page.
      </p>
    </>
  );
}

function YouTubeEditor({
  payload,
  onChange,
  readOnly,
}: {
  payload: YouTubePayload;
  onChange: (p: YouTubePayload) => void;
  readOnly: boolean;
}) {
  const set = (patch: Partial<YouTubePayload>) => onChange({ ...payload, ...patch });
  return (
    <>
      <Field label="Video file">
        <TextInput
          value={payload.video}
          onChange={(video) => set({ video })}
          placeholder="media/talk.mp4"
        />
      </Field>
      <Field label="Title" aside={<Count value={payload.title.length} limit={100} />}>
        <TextInput value={payload.title} onChange={(title) => set({ title })} />
      </Field>
      <Field label="Description" aside={<Count value={payload.description.length} limit={5000} />}>
        <textarea
          className="scrive-outline-text"
          rows={6}
          value={payload.description}
          readOnly={readOnly}
          onChange={(e) => set({ description: e.target.value })}
        />
      </Field>
      <div className="scrive-outline-pair">
        <Field label="Tags">
          <TextInput
            value={payload.tags.join(', ')}
            onChange={(tags) =>
              set({
                tags: tags
                  .split(',')
                  .map((t) => t.trim())
                  .filter(Boolean),
              })
            }
            placeholder="comma, separated"
          />
        </Field>
        <Field label="Visibility">
          <select
            value={payload.privacy}
            onChange={(e) => set({ privacy: e.target.value as YouTubePayload['privacy'] })}
            style={{ padding: '0 0.6rem' }}
          >
            <option value="private">Private</option>
            <option value="unlisted">Unlisted</option>
            <option value="public">Public</option>
          </select>
        </Field>
        <Field label="Goes public at (optional)">
          <input
            type="datetime-local"
            value={toLocalInput(payload.publish_at)}
            onChange={(e) => set({ publish_at: fromLocalInput(e.target.value) })}
            style={{ padding: '0 0.6rem' }}
          />
        </Field>
      </div>
      <label className="scrive-share-check">
        <input
          type="checkbox"
          checked={payload.synthetic}
          disabled={readOnly}
          onChange={(e) => set({ synthetic: e.target.checked })}
        />
        Contains realistic altered or synthetic media (YouTube's disclosure)
      </label>
      <label className="scrive-share-check">
        <input
          type="checkbox"
          checked={payload.made_for_kids}
          disabled={readOnly}
          onChange={(e) => set({ made_for_kids: e.target.checked })}
        />
        Made for kids
      </label>
    </>
  );
}

/** The tag limit each platform enforces (preflight refuses more). */
const TAG_LIMIT: Partial<Record<OutboxTarget, number>> = { devto: 4, hashnode: 5 };

function ArticleEditor({
  target,
  payload,
  onChange,
  readOnly,
}: {
  target: OutboxTarget;
  payload: DevtoPayload | HashnodePayload;
  onChange: (p: DevtoPayload | HashnodePayload) => void;
  readOnly: boolean;
}) {
  const set = (patch: Partial<DevtoPayload & HashnodePayload>) =>
    onChange({ ...payload, ...patch } as DevtoPayload | HashnodePayload);
  const devto = target === 'devto' ? (payload as DevtoPayload) : null;
  const lead = devto ? devto.description : (payload as HashnodePayload).subtitle;
  const limit = TAG_LIMIT[target] ?? 5;
  return (
    <>
      <Field label="Title">
        <TextInput value={payload.title} onChange={(title) => set({ title })} />
      </Field>
      <Field label={devto ? 'Description' : 'Subtitle'}>
        <TextInput
          value={lead}
          onChange={(text) => set(devto ? { description: text } : { subtitle: text })}
        />
      </Field>
      <Field
        label="Article"
        aside={<span className="scrive-meta">{payload.body.length.toLocaleString()} chars</span>}
      >
        <textarea
          className="scrive-outline-text scrive-share-article"
          rows={16}
          spellCheck={false}
          value={payload.body}
          readOnly={readOnly}
          onChange={(e) => set({ body: e.target.value })}
        />
      </Field>
      <div className="scrive-outline-pair">
        <Field label="Tags" aside={<Count value={payload.tags.length} limit={limit} />}>
          <TextInput
            value={payload.tags.join(', ')}
            onChange={(tags) =>
              set({
                tags: tags
                  .split(',')
                  .map((t) => t.trim())
                  .filter(Boolean),
              })
            }
            placeholder="comma, separated"
          />
        </Field>
        <Field label="Cover">
          <TextInput
            value={payload.cover}
            onChange={(cover) => set({ cover })}
            placeholder="media/cover.png (optional)"
          />
        </Field>
      </div>
      <div className="scrive-outline-pair">
        <Field label="Canonical URL">
          <TextInput
            value={payload.canonical_url}
            onChange={(canonical_url) => set({ canonical_url })}
            placeholder={POST_URL}
          />
        </Field>
        {devto && (
          <Field label="Series (optional)">
            <TextInput value={devto.series} onChange={(series) => set({ series })} />
          </Field>
        )}
      </div>
      {devto && (
        <label className="scrive-share-check">
          <input
            type="checkbox"
            checked={!devto.published}
            disabled={readOnly}
            onChange={(e) => set({ published: !e.target.checked })}
          />
          Send as a dev.to draft, to finish and publish there
        </label>
      )}
      <p className="scrive-meta scrive-publish-note">
        The whole article, converted from MyST. {'{{site.url}}'} and {POST_URL} are filled in from
        the published site at approval; the canonical URL tells search engines the site has the
        original.
      </p>
    </>
  );
}
