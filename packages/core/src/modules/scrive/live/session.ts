/**
 * Live sessions in the browser: the host's (a page pane sharing its buffer) and a
 * guest's (the live pane), over the `/ws` `scrive-live` channel. See `provider.ts` for
 * the wire and backend/modules/scrive/live.py for the relay.
 *
 * Quiet rules:
 *
 * - **A document is seeded exactly once.** The host's pane puts the page text into a
 *   fresh `Y.Doc` only when no other pane on its machine holds the session; otherwise
 *   it syncs from that one. Two documents seeded separately would *merge*, and the
 *   page would appear twice. A host that comes back after a reload reseeds, and the
 *   guests are told to drop their copy (`reset`).
 * - **The host saves.** Guests edit the host's buffer; the file is written by the
 *   host's Save (and its autosave while live). Nothing here touches the disk.
 */
import { Prec, type Extension } from '@codemirror/state';
import { keymap } from '@codemirror/view';
import { yCollab, yUndoManagerKeymap } from 'y-codemirror.next';
import * as Y from 'yjs';

import { onSocketOpen, sendChannel, subscribeChannel } from '../../../ws';
import { getSocialState } from '../../social/ws';
import { LIVE_CHANNEL, type LiveSummary } from './invites';
import { fromBase64, LiveProvider, toBase64, type LiveTransport } from './provider';

export { LIVE_CHANNEL, type LiveSummary };

export interface LivePerson {
  personId: string;
  name: string;
}

/** Someone in the document right now, from awareness. */
export interface LivePresence {
  clientId: number;
  name: string;
  color: string;
}

/** Cursor colours as theme tokens: each viewer sees the others in their own theme's
 * palette (the value travels as a `var()` and resolves on the far side). */
const COLORS = [
  'var(--accent)',
  'var(--success)',
  'var(--warn)',
  'var(--danger)',
  'var(--accent-2, var(--accent))',
];

/** This person's name and a colour picked from their name, for cursors. */
export function localUser(): { name: string; color: string; colorLight: string } {
  const name = getSocialState().roster?.self_profile.display_name || 'Someone';
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const color = COLORS[hash % COLORS.length];
  return { name, color, colorLight: `color-mix(in srgb, ${color} 22%, transparent)` };
}

/** The `/ws` leg of a provider, for one room. `rejoin` re-announces this browser
 * to the relay before every resync (a reconnected socket is a new connection). */
export function wsTransport(key: () => string, rejoin: () => void): LiveTransport {
  return {
    send(frame) {
      sendChannel(LIVE_CHANNEL, 'msg', { key: key(), data: toBase64(frame) });
    },
    onFrame(handler) {
      return subscribeChannel(LIVE_CHANNEL, (msg) => {
        const data = msg.data as { key?: string; data?: string } | undefined;
        if (msg.event === 'msg' && data?.key === key() && data.data) handler(fromBase64(data.data));
      });
    },
    onOpen(handler) {
      let first = true;
      return onSocketOpen(() => {
        // The first open is the provider's own start; it rejoined already.
        if (!first) rejoin();
        first = false;
        handler();
      });
    },
  };
}

/** The CodeMirror side: the shared text, others' cursors, and an undo that only
 * undoes your own typing (CodeMirror's would undo a guest's too). */
export function collabExtension(ytext: Y.Text, provider: LiveProvider): Extension {
  const undoManager = new Y.UndoManager(ytext);
  return [
    yCollab(ytext, provider.awareness, { undoManager }),
    Prec.high(keymap.of(yUndoManagerKeymap)),
  ];
}

export function presenceOf(provider: LiveProvider): LivePresence[] {
  const out: LivePresence[] = [];
  provider.awareness.getStates().forEach((state, clientId) => {
    const user = (state as { user?: { name?: string; color?: string } }).user;
    if (user?.name) out.push({ clientId, name: user.name, color: user.color ?? COLORS[0] });
  });
  return out;
}

/** Wait for one reply event on the channel (for `hosted` / `joined`). */
function reply<T>(event: string, match: (data: T) => boolean, timeoutMs = 8000): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      off();
      reject(new Error('The live session did not answer.'));
    }, timeoutMs);
    const off = subscribeChannel(LIVE_CHANNEL, (msg) => {
      const data = msg.data as T;
      if (msg.event === 'error') {
        clearTimeout(timer);
        off();
        reject(new Error(String((msg.data as { message?: string })?.message ?? 'live error')));
      } else if (msg.event === event && match(data)) {
        clearTimeout(timer);
        off();
        resolve(data);
      }
    });
  });
}

export interface HostedSession {
  key: string;
  doc: Y.Doc;
  text: Y.Text;
  provider: LiveProvider;
  people: LivePerson[];
  /** True when this pane joined a session another pane here was already holding. */
  joined: boolean;
  destroy(end: boolean): void;
}

/**
 * Start (or join, on this machine) the live session for a page. `seed` is the
 * editor's text, used only when this pane is the one that seeds the document.
 * Resolves once the document holds the page.
 */
export async function hostPage(
  site: string,
  path: string,
  title: string,
  seed: () => string,
): Promise<HostedSession> {
  const answer = reply<LiveSummary & { people: LivePerson[]; existing: boolean }>(
    'hosted',
    (d) => d.site === site && d.path === path,
  );
  sendChannel(LIVE_CHANNEL, 'host', { site, path, title });
  const hosted = await answer;
  const doc = new Y.Doc();
  const text = doc.getText('source');
  if (!hosted.existing) text.insert(0, seed());
  const provider = new LiveProvider(
    doc,
    wsTransport(
      () => hosted.key,
      () => sendChannel(LIVE_CHANNEL, 'host', { site, path, title }),
    ),
  );
  // The provider asked for what it lacks as it connected; say who we are.
  provider.awareness.setLocalStateField('user', localUser());
  if (hosted.existing && !provider.synced) {
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        off();
        provider.destroy();
        sendChannel(LIVE_CHANNEL, 'leave', { key: hosted.key });
        reject(new Error('Another pane is hosting this page live but did not answer.'));
      }, 8000);
      const off = provider.onSynced(() => {
        clearTimeout(timer);
        off();
        resolve();
      });
    });
  }
  return {
    key: hosted.key,
    doc,
    text,
    provider,
    people: hosted.people,
    joined: hosted.existing,
    destroy(end) {
      provider.destroy();
      sendChannel(LIVE_CHANNEL, end ? 'end' : 'leave', { key: hosted.key });
      doc.destroy();
    },
  };
}

export function invite(key: string, personId: string): void {
  sendChannel(LIVE_CHANNEL, 'invite', { key, personId });
}

export function uninvite(key: string, personId: string): void {
  sendChannel(LIVE_CHANNEL, 'uninvite', { key, personId });
}

/** Room events for one key: who it is shared with, errors, its end, a reset. */
export function onRoom(
  key: string,
  handlers: {
    people?: (people: LivePerson[]) => void;
    error?: (message: string) => void;
    ended?: () => void;
    reset?: () => void;
  },
): () => void {
  return subscribeChannel(LIVE_CHANNEL, (msg) => {
    const data = (msg.data ?? {}) as { key?: string; people?: LivePerson[]; message?: string };
    if (data.key !== key) return;
    if (msg.event === 'people') handlers.people?.(data.people ?? []);
    else if (msg.event === 'error') handlers.error?.(data.message ?? 'live error');
    else if (msg.event === 'ended') handlers.ended?.();
    else if (msg.event === 'reset') handlers.reset?.();
  });
}

export interface JoinedSession {
  summary: LiveSummary;
  doc: Y.Doc;
  text: Y.Text;
  provider: LiveProvider;
  destroy(): void;
}

/** Join a session a host invited this machine to. Resolves once the host's
 * document has arrived (`synced`), or rejects if nobody answers in time. */
export async function joinSession(key: string, timeoutMs = 15_000): Promise<JoinedSession> {
  const answer = reply<LiveSummary>('joined', (d) => d.key === key);
  sendChannel(LIVE_CHANNEL, 'join', { key });
  const summary = await answer;
  const doc = new Y.Doc();
  const text = doc.getText('source');
  const provider = new LiveProvider(
    doc,
    wsTransport(
      () => key,
      () => sendChannel(LIVE_CHANNEL, 'join', { key }),
    ),
  );
  provider.awareness.setLocalStateField('user', localUser());
  const synced = new Promise<void>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('The host’s page has not arrived. Their app may be closed.')),
      timeoutMs,
    );
    const off = provider.onSynced(() => {
      clearTimeout(timer);
      off();
      resolve();
    });
  });
  try {
    await synced;
  } catch (e) {
    provider.destroy();
    sendChannel(LIVE_CHANNEL, 'leave', { key });
    doc.destroy();
    throw e;
  }
  return {
    summary,
    doc,
    text,
    provider,
    destroy() {
      provider.destroy();
      sendChannel(LIVE_CHANNEL, 'leave', { key });
      doc.destroy();
    },
  };
}
