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
 * This also subscribes to `system`, which the backend has been broadcasting to
 * since remote-control landed (`network/remote_control.py:63`) with **nothing
 * listening** — every "say" relayed from a paired phone was dropped on arrival.
 */
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
  at: number;
  read: boolean;
}

/** Keep a short tail, not a log: this is a feed to glance at, and the backend
 * already holds anything durable (messages, invites, watches). */
const MAX_FEED = 50;

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
}

export function dismissNotification(id: string): void {
  feed = feed.filter((n) => n.id !== id);
  emit();
}

const KINDS = new Set(['info', 'success', 'warning', 'error']);

function push(item: Omit<NotificationItem, 'id' | 'at' | 'read'>): void {
  const entry: NotificationItem = {
    ...item,
    id: Math.random().toString(36).slice(2, 10),
    at: Date.now(),
    read: false,
  };
  feed = [entry, ...feed].slice(0, MAX_FEED);
  emit();
  toastsStore.add(entry.kind, entry.title, entry.body);
  void showDesktopNotification(entry.title, entry.body);
}

let started = false;

/** Begin listening. Idempotent, and called at boot — a notification you only
 * receive once the right pane is open is not a notification. */
export function initNotifications(): void {
  if (started) return;
  started = true;
  subscribeChannel('notifications', (msg) => {
    if (msg.event !== 'notify') return;
    const d = (msg.data ?? {}) as Record<string, unknown>;
    const kind = String(d.kind ?? 'info');
    push({
      category: String(d.category ?? 'all'),
      kind: (KINDS.has(kind) ? kind : 'info') as NotificationItem['kind'],
      title: String(d.title ?? 'Notification'),
      body: String(d.body ?? ''),
      personId: d.person_id == null ? null : String(d.person_id),
    });
  });
  subscribeChannel('system', (msg) => {
    if (msg.event !== 'notification') return;
    const d = (msg.data ?? {}) as Record<string, unknown>;
    push({
      category: 'message',
      kind: 'info',
      title: String(d.title ?? 'Notification'),
      body: String(d.text ?? ''),
      personId: null,
    });
  });
}
