/**
 * Client for the `/ws` `share` channel: keeps a live snapshot the share pane
 * renders.
 *
 * The session is process-global on the node, so state is *pushed* rather than
 * polled — two tabs on one machine are two renderers of one session, not two
 * sessions, and a grant the host changes in one tab has to land in the other
 * immediately.
 *
 * Same shape as the social module's store: one shared snapshot, re-synced on
 * every socket (re)connect.
 */
import { onSocketOpen, sendChannel, subscribeChannel } from '../../ws';
import { toastsStore } from '../../toasts';
import type { GrantLevel, RemoteSession, SessionOut, ShareInvite, ShareSession } from './api';
import type { MirrorFrame, MirrorSummary } from './mirror';

export interface ShareState extends SessionOut {
  /** The last error the backend reported, for the pane to surface. */
  error: string | null;
  /**
   * The latest projection from each session we have joined, by session id.
   * Absent means the host has not published one yet — which the mirror pane
   * shows as "waiting", never as an empty workspace.
   */
  mirrors: Record<string, MirrorFrame>;
  /**
   * What each guest has done, newest last. Host-only — the backend broadcasts
   * this on the local channel and never over the fabric, because one guest
   * reading it would learn what every other guest did.
   */
  audit: AuditEntry[];
  /**
   * Where each guest's pointer is, by node id. Ephemeral and deliberately not
   * part of the audit log: a cursor is a hundred events a minute and the log has
   * to stay readable.
   */
  cursors: Record<string, GuestCursor>;
}

/** One line of the audit log. Mirrors `backend/modules/share/audit.py`. */
export interface AuditEntry {
  ts: number;
  node_id: string;
  name: string;
  action: string;
  needs: string;
  outcome: 'allowed' | 'denied' | 'asked' | 'failed';
  reason: string;
  detail: Record<string, unknown>;
}

export interface GuestCursor {
  node_id: string;
  name: string;
  /** Fractions of the pane's box, so the two ends need not agree on pixels. */
  x: number;
  y: number;
  instanceId: string;
  ts: number;
}

const EMPTY: ShareState = {
  hosting: null,
  joined: [],
  invites: [],
  error: null,
  mirrors: {},
  audit: [],
  cursors: {},
};

let state: ShareState = EMPTY;
const listeners = new Set<() => void>();

function emit(): void {
  state = { ...state };
  listeners.forEach((l) => l());
}

export function getShareSnapshot(): ShareState {
  return state;
}

export function subscribeShare(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** SDP/ICE arriving from a peer, for the media layer to consume (Phase 3). */
type SignalHandler = (from: string, payload: unknown) => void;
const signalHandlers = new Set<SignalHandler>();

export function onShareSignal(handler: SignalHandler): () => void {
  signalHandlers.add(handler);
  return () => {
    signalHandlers.delete(handler);
  };
}

let started = false;

export function initShare(): void {
  if (started) return;
  started = true;

  subscribeChannel('share', (msg) => {
    const data = msg.data as Record<string, unknown>;
    switch (msg.event) {
      case 'state': {
        const snap = msg.data as SessionOut;
        // Projections are kept across a state refresh: the backend replays them
        // right after this event, but a reconnect should not blank the pane in
        // between.
        // The audit log and cursors survive a state refresh for the same reason
        // projections do: the backend replays them separately, and a reconnect
        // must not blank what the host was reading.
        state = {
          ...snap,
          error: null,
          mirrors: state.mirrors,
          audit: state.audit,
          cursors: state.cursors,
        };
        emit();
        break;
      }
      case 'remote_mirror': {
        const id = String(data.sessionId ?? '');
        const frame = data.frame as MirrorFrame | undefined;
        if (id && frame) {
          state.mirrors = { ...state.mirrors, [id]: frame };
          emit();
        }
        break;
      }
      case 'session': {
        const next = msg.data as ShareSession;
        // Drop a broadcast that arrived out of order. Without this a slow
        // envelope can overwrite a newer participant list, and the pane shows
        // a grant being un-done that was never un-done.
        if (state.hosting && next.id === state.hosting.id && next.revision < state.hosting.revision)
          break;
        state.hosting = next;
        emit();
        break;
      }
      case 'ended':
        state.hosting = null;
        emit();
        break;
      case 'invite': {
        const invite = msg.data as ShareInvite;
        state.invites = [
          invite,
          ...state.invites.filter((i) => i.session_id !== invite.session_id),
        ];
        emit();
        break;
      }
      case 'joined': {
        const remote = msg.data as RemoteSession;
        state.joined = [...state.joined.filter((s) => s.id !== remote.id), remote];
        state.invites = state.invites.filter((i) => i.session_id !== remote.id);
        emit();
        break;
      }
      case 'left': {
        const id = String(data.sessionId ?? '');
        state.joined = state.joined.filter((s) => s.id !== id);
        state.mirrors = Object.fromEntries(
          Object.entries(state.mirrors).filter(([key]) => key !== id),
        );
        emit();
        break;
      }
      case 'remote_session': {
        // The host republished. `yourGrant` is resolved backend-side because a
        // tab does not know this node's id and so cannot pick its own row out of
        // the host's participant list — and its own rung is the one thing a
        // guest cannot work out for itself.
        const id = String(data.id ?? '');
        state.joined = state.joined.map((s) =>
          s.id === id
            ? {
                ...s,
                title: String(data.title ?? s.title),
                grant: (data.yourGrant as GrantLevel | undefined) ?? s.grant,
              }
            : s,
        );
        emit();
        break;
      }
      case 'action': {
        const name = String(data.name ?? '');
        const from = String(data.from ?? '');
        const params = (data.params ?? {}) as Record<string, unknown>;
        if (name === 'cursor.move') {
          // Never a toast: a moving pointer would fire one every frame.
          state.cursors = {
            ...state.cursors,
            [from]: {
              node_id: from,
              name: guestName(from),
              x: Number(params.x ?? 0),
              y: Number(params.y ?? 0),
              instanceId: String(params.instanceId ?? ''),
              ts: Number(data.ts ?? Date.now() / 1000),
            },
          };
          emit();
          break;
        }
        // Everything else is rare and consequential, so it is worth interrupting
        // for — this is a guest doing something on the host's machine.
        toastsStore.add('info', 'Shared session', `${guestName(from)} ran ${name}.`);
        actionHandlers.forEach((h) => h(name, params, from));
        break;
      }
      case 'audit': {
        state.audit = (data.entries as AuditEntry[]) ?? [];
        emit();
        break;
      }
      case 'signal':
        signalHandlers.forEach((h) => h(String(data.from ?? ''), data.payload));
        break;
      case 'error':
        state.error = String(data.message ?? 'something went wrong');
        emit();
        break;
    }
  });

  onSocketOpen(() => sendChannel('share', 'state', {}));
}

/**
 * The display name for a guest's node, or the node id.
 *
 * A name is a **label** the peer supplied, never an identity — the same rule the
 * roster and hassault's invites state. The node id is what the fabric
 * authenticated, so it is what falls back to.
 */
function guestName(nodeId: string): string {
  const found = state.hosting?.participants.find((p) => p.node_id === nodeId);
  return found?.name || nodeId;
}

type ActionHandler = (name: string, params: Record<string, unknown>, from: string) => void;
const actionHandlers = new Set<ActionHandler>();

/** Listen for guest actions the host's browser has to actuate. */
export function onShareAction(handler: ActionHandler): () => void {
  actionHandlers.add(handler);
  return () => actionHandlers.delete(handler);
}

/**
 * Ask the host to do something, as a guest.
 *
 * The `needs` rung is deliberately **not** sent: the host's registry decides what
 * an action requires, keyed by name. A client that nominated its own permission
 * would be picking its own lock, and a host that believed it would have no gate
 * at all.
 */
export function sendShareAction(
  sessionId: string,
  name: string,
  params: Record<string, unknown> = {},
): void {
  sendChannel('share', 'action', { sessionId, name, params });
}

export function requestShareState(): void {
  sendChannel('share', 'state', {});
}

export function startViaChannel(title: string, mode = 'semantic'): void {
  sendChannel('share', 'start', { title, mode });
}

export function stopViaChannel(): void {
  sendChannel('share', 'stop', {});
}

export function inviteViaChannel(personId: string): void {
  sendChannel('share', 'invite', { personId });
}

export function grantViaChannel(personId: string, grant: GrantLevel): void {
  sendChannel('share', 'grant', { personId, grant });
}

export function revokeAllViaChannel(): void {
  sendChannel('share', 'revoke_all', {});
}

export function kickViaChannel(nodeId: string): void {
  sendChannel('share', 'kick', { nodeId });
}

export function joinViaChannel(sessionId: string, hostNode: string): void {
  sendChannel('share', 'join', { sessionId, hostNode });
}

export function leaveViaChannel(sessionId: string): void {
  sendChannel('share', 'leave', { sessionId });
}

export function dismissInviteViaChannel(sessionId: string): void {
  sendChannel('share', 'dismiss_invite', { sessionId });
}

/** Relay one SDP/ICE frame to a peer. The node passes it through untouched. */
export function sendShareSignal(to: string, payload: unknown): void {
  sendChannel('share', 'signal', { to, payload });
}

/**
 * Publish a redacted projection of this workspace to the session's guests.
 *
 * Already redacted by the time it gets here — see `mirror.ts`. Nothing between
 * this call and the guest's screen removes anything, so this function must never
 * be handed a raw `FrameState`.
 */
export function sendMirror(frame: MirrorFrame, summary: MirrorSummary): void {
  sendChannel('share', 'mirror', { frame, summary });
}
