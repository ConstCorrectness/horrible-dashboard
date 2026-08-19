/**
 * Client for the `/ws` `notifications` channel — the landing pad for everything the
 * backend wants to tell you without a chat turn or an open pane: a fired watch
 * ("Andrew is online"), an invite, a message while you were elsewhere.
 *
 * **The mute check is not here.** It runs at the producer
 * (`backend/modules/notifications/service.notify`), so a suppressed notification
 * never crosses the socket. Filtering in the browser would still have buzzed the
 * phone before the filter ran.
 *
 * **The feed is not the truth either.** It used to be: notifications lived only in
 * the array below, so a reload emptied it and anything that arrived while the app
 * was closed was never seen by anyone. The backend now writes each one to
 * `app.db` first, this module hydrates from there at boot, and read/cleared state
 * is sent back rather than kept locally — which is what lets one notification
 * reach four surfaces and be answered once. A toast is a cache of a row.
 *
 * This also subscribes to `system`, which the backend has been broadcasting to
 * since remote-control landed (`network/remote_control.py:63`) with **nothing
 * listening** — every "say" relayed from a paired phone was dropped on arrival.
 */
import { apiGet, apiPost } from '../../api';
import { subscribeChannel } from '../../ws';
import { toastsStore } from '../../toasts';
import { showDesktopNotification } from './desktop';

export interface NotificationItem {
  id: string;
  /** One of `store.CATEGORIES` on the backend. */
  category: string;
  kind: 'info' | 'success' | 'warning' | 'error';
  title: string;
  body: string;
  /** Who it is *about*, when that is a person. */
  personId: string | null;
  /**
   * The key that ties every surface showing this notification together.
   *
   * Null for one-off information. Set for anything answerable — an invite is a
   * toast *and* a feed row *and* an OS notification *and* an in-game card, and
   * without a shared key, answering one leaves three stale copies of it.
   */
  dedupe: string | null;
  at: number;
  read: boolean;
  /**
   * What a surface can *do* about it, if anything: `{ action, ...payload }` as the
   * producer sent it. Untyped on purpose — each category owns its own payload, and
   * a union here would make `notifications` depend on every module that notifies.
   */
  data: Record<string, unknown>;
}

/** Keep a short tail in memory. The backend holds the durable copy, so this is a
 * window onto it rather than the only record. */
const MAX_FEED = 100;

let feed: NotificationItem[] = [];
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function getNotifications(): NotificationItem[] {
  return feed;
}

export function unreadCount(): number {
  return feed.filter((n) => !n.read).length;
}

export function subscribeNotifications(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function markAllRead(): void {
  if (!feed.some((n) => !n.read)) return;
  feed = feed.map((n) => ({ ...n, read: true }));
  emit();
  // Fire-and-forget: the local state is already right, and a failed write costs
  // a re-read of "unread" on the next boot, not a lost notification.
  void apiPost('/notifications/read', {}).catch(() => {});
}

export function dismissNotification(id: string): void {
  const item = feed.find((n) => n.id === id);
  feed = feed.filter((n) => n.id !== id);
  emit();
  // Dismissing something answerable retires it from *every* surface, not just
  // this list — that is the difference between closing a card and answering it.
  void apiPost('/notifications/clear', item?.dedupe ? { dedupe: item.dedupe } : { id }).catch(
    () => {},
  );
}

/** Retire whatever is showing this key, locally. Called on the backend's
 * `retract` and by whoever just acted on the notification. */
export function retractNotification(dedupe: string): void {
  const before = feed.length;
  feed = feed.filter((n) => n.dedupe !== dedupe);
  if (feed.length !== before) emit();
}

/**
 * What a category's toast offers to do, keyed by the `action` its payload names.
 *
 * A registry rather than a `switch` here because the handler belongs to the module
 * that produces the notification — joining a match is HorribleAssault's business,
 * and `notifications` importing it would invert the dependency and drag a three.js
 * pane into the boot path of the notification feed.
 *
 * `Toast` permits exactly one action, and that constraint is kept: the click *is*
 * the answer. Anything with a second choice belongs in the inbox, not a toast.
 */
export interface NotificationAction {
  label: string;
  run: (item: NotificationItem) => void;
}

const actions = new Map<string, NotificationAction>();

export function registerNotificationAction(name: string, action: NotificationAction): void {
  actions.set(name, action);
}

const KINDS = new Set(['info', 'success', 'warning', 'error']);

/** The fields every notification has, whatever it is about. Everything else in
 * the message belongs to the category and is carried in `data`. */
const ENVELOPE_KEYS = new Set([
  'id',
  'category',
  'kind',
  'title',
  'body',
  'person_id',
  'dedupe',
  'at',
  'read',
]);

function toItem(d: Record<string, unknown>): NotificationItem {
  const kind = String(d.kind ?? 'info');
  // Whatever the producer sent that isn't an envelope field is the category's own
  // payload — an invite's room and map, a watch's id — and it rides through
  // untouched so a surface can act on it. Subtracting the known keys rather than
  // listing the unknown ones is the only way round: `notifications` must not have
  // to know what every module puts in there.
  const rest: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(d)) {
    if (!ENVELOPE_KEYS.has(key)) rest[key] = value;
  }
  return {
    // The backend's row id, so clearing and marking read address the same row it
    // does. A locally invented id would make both no-ops on the server.
    id: String(d.id ?? Math.random().toString(36).slice(2, 10)),
    category: String(d.category ?? 'all'),
    kind: (KINDS.has(kind) ? kind : 'info') as NotificationItem['kind'],
    title: String(d.title ?? 'Notification'),
    body: String(d.body ?? ''),
    personId: d.person_id == null ? null : String(d.person_id),
    dedupe: d.dedupe == null ? null : String(d.dedupe),
    at: typeof d.at === 'number' ? d.at * 1000 : Date.now(),
    read: d.read === true,
    data: rest,
  };
}

function insert(entry: NotificationItem): void {
  // A repeat of a dedupe key *replaces* rather than stacks — the backend already
  // decided that by updating one row, and a feed that showed two would disagree
  // with the bell count.
  const rest = entry.dedupe ? feed.filter((n) => n.dedupe !== entry.dedupe) : feed;
  feed = [entry, ...rest].slice(0, MAX_FEED);
  emit();
}

function announce(entry: NotificationItem): void {
  const name = entry.data.action;
  const action = typeof name === 'string' ? actions.get(name) : undefined;
  toastsStore.add(
    entry.kind,
    entry.title,
    entry.body,
    4000,
    action ? { action: { label: action.label, run: () => action.run(entry) } } : {},
  );
  void showDesktopNotification(entry.title, entry.body);
}

let started = false;

/** Begin listening, and load what arrived while we were away. Idempotent, and
 * called at boot — a notification you only receive once the right pane is open is
 * not a notification. */
export function initNotifications(): void {
  if (started) return;
  started = true;

  // Hydrate before subscribing rather than after: a notification that lands
  // during the fetch is inserted by `insert`, and a hydrate that finished second
  // would overwrite it with a snapshot taken before it existed.
  void apiGet<Record<string, unknown>[]>('/notifications/feed')
    .then((rows) => {
      const live = rows.map(toItem);
      const known = new Set(live.map((n) => n.id));
      // Anything the socket delivered mid-fetch stays on top.
      feed = [...feed.filter((n) => !known.has(n.id)), ...live].slice(0, MAX_FEED);
      emit();
    })
    .catch(() => {
      /* Backend down at boot: the socket will still deliver anything new. */
    });

  subscribeChannel('notifications', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'retract') {
      if (typeof d.dedupe === 'string') retractNotification(d.dedupe);
      return;
    }
    if (msg.event !== 'notify') return;
    const entry = toItem(d);
    insert(entry);
    announce(entry);
  });

  subscribeChannel('system', (msg) => {
    if (msg.event !== 'notification') return;
    const d = (msg.data ?? {}) as Record<string, unknown>;
    const entry = toItem({
      category: 'message',
      kind: 'info',
      title: d.title ?? 'Notification',
      body: d.text ?? '',
    });
    insert(entry);
    announce(entry);
  });
}
