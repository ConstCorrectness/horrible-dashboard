/**
 * The `hassault` `/ws` channel, as a small stateful object the pane can drive.
 *
 * Split from `net.ts` on purpose: everything in `net.ts` is pure and unit-tested
 * headless, and importing the shell's socket module would open a real WebSocket
 * at import time, which is exactly what breaks a node test run.
 */
import type { MatchInvite } from './api';
import { subscribeChannel, sendChannel } from '../../ws';
import { PingTracker, Predictor, SnapshotBuffer, type Command, type Snapshot } from './net';

export interface MatchPeer {
  id: string;
  name: string;
  team: number;
  rtt: number;
  stale: boolean;
}

export interface SessionState {
  status: 'idle' | 'joining' | 'joined' | 'error';
  room: string;
  map: string;
  playerId: string;
  peers: MatchPeer[];
  error: string;
  rtt: number;
  /**
   * The node hosting this match, empty when it is our own.
   *
   * Only a label for the UI: a remote match is driven through exactly the same
   * events, because our backend proxies for us and hands the browser the same
   * `welcome` and `snapshot` frames it would produce locally.
   */
  host: string;
  /** Invitations from friends, newest first. */
  invites: MatchInvite[];
}

const PING_INTERVAL_MS = 1000;
/**
 * Input send rate. Half the frame rate at 60 fps, which halves the message count
 * for identical information — every command still reaches the server, batched.
 */
const SEND_INTERVAL_MS = 33;

export class MatchSession {
  readonly predictor = new Predictor();
  readonly snapshots = new SnapshotBuffer();
  readonly ping = new PingTracker();

  state: SessionState = {
    status: 'idle',
    room: '',
    map: '',
    playerId: '',
    peers: [],
    error: '',
    rtt: 0,
    host: '',
    invites: [],
  };

  /** Fires on any change worth re-rendering the surrounding UI for. */
  onChange: (state: SessionState) => void = () => {};
  /** The most recent authoritative row for us, consumed by the render loop. */
  pendingCorrection: { row: Snapshot['players'][number]; ack: number } | null = null;

  private unsubscribe: (() => void) | null = null;
  private outbox: Command[] = [];
  private lastSend = 0;
  private lastPing = 0;

  connect(): void {
    if (this.unsubscribe) return;
    this.unsubscribe = subscribeChannel('hassault', (msg) => this.receive(msg));
  }

  disconnect(): void {
    if (this.state.status === 'joined') sendChannel('hassault', 'leave');
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.reset('idle');
  }

  /**
   * Join a match. With `host` set, it is a friend's match on their node and our
   * backend proxies for us; everything after this point is identical either way.
   */
  join(map: string, name: string, room?: string, host?: string): void {
    this.connect();
    const invites = this.state.invites;
    this.reset('joining');
    // Invitations outlive a join: the one being accepted may fail, and the
    // others are still valid offers.
    this.state.invites = invites;
    this.state.map = map;
    this.state.host = host ?? '';
    this.emit();
    sendChannel('hassault', 'join', { map, name, room, host });
  }

  /** Invite a friend to the match we are hosting. */
  invite(who: string): void {
    if (this.state.status !== 'joined' || this.state.host) return;
    sendChannel('hassault', 'invite', { who, room: this.state.room });
  }

  /** Ask the backend for invitations that arrived before this pane was open. */
  refreshInvites(): void {
    this.connect();
    sendChannel('hassault', 'invites');
  }

  dismissInvite(room: string): void {
    this.state.invites = this.state.invites.filter((i) => i.room !== room);
    this.emit();
  }

  leave(): void {
    if (this.state.status !== 'joined') return;
    sendChannel('hassault', 'leave');
    this.reset('idle');
    this.emit();
  }

  respawn(): void {
    if (this.state.status === 'joined') sendChannel('hassault', 'respawn');
  }

  /** Queue a locally-predicted command for the next send. */
  queue(command: Command): void {
    if (this.state.status === 'joined') this.outbox.push(command);
  }

  /**
   * Flush queued input and keep the ping going. Called once per rendered frame;
   * the rate limiting lives here rather than at the call site.
   */
  pump(now: number): void {
    if (this.state.status !== 'joined') return;
    if (this.outbox.length > 0 && now - this.lastSend >= SEND_INTERVAL_MS) {
      sendChannel('hassault', 'input', { commands: this.outbox, rtt: this.ping.rtt });
      this.outbox = [];
      this.lastSend = now;
    }
    if (now - this.lastPing >= PING_INTERVAL_MS) {
      sendChannel('hassault', 'ping', { t: Math.round(now) });
      this.lastPing = now;
    }
  }

  private receive(msg: { event?: string; data?: unknown }): void {
    const data = (msg.data ?? {}) as Record<string, unknown>;
    switch (msg.event) {
      case 'welcome': {
        this.state.status = 'joined';
        this.state.room = String(data.room ?? '');
        this.state.map = String(data.map ?? '');
        this.state.playerId = String(data.playerId ?? '');
        this.state.host = String(data.host ?? this.state.host ?? '');
        this.state.peers = this.peersFrom(data.players);
        // The invitation has been taken up; leaving it on screen would invite
        // the same room twice.
        this.state.invites = this.state.invites.filter((i) => i.room !== this.state.room);
        this.emit();
        break;
      }
      case 'invite': {
        const invite = data as unknown as MatchInvite;
        if (!invite.room) return;
        this.state.invites = [
          invite,
          ...this.state.invites.filter((i) => i.room !== invite.room),
        ].slice(0, 5);
        this.emit();
        break;
      }
      case 'invites': {
        const list = Array.isArray(data.invites) ? (data.invites as MatchInvite[]) : [];
        this.state.invites = list.slice(0, 5);
        this.emit();
        break;
      }
      case 'invite_sent': {
        this.state.error = '';
        this.emit();
        break;
      }
      case 'snapshot': {
        const snapshot = data as unknown as Snapshot;
        if (!Array.isArray(snapshot.players)) return;
        this.snapshots.push(snapshot, performance.now());
        const mine = snapshot.players.find((p) => p.id === this.state.playerId);
        // Handed to the render loop rather than applied here: reconciliation
        // needs the World, which lives with the renderer, and a socket callback
        // is the wrong place to be stepping physics.
        if (mine) this.pendingCorrection = { row: mine, ack: snapshot.ack };
        const peers = this.peersFrom(snapshot.players);
        if (this.peersChanged(peers)) {
          this.state.peers = peers;
          this.emit();
        }
        break;
      }
      case 'joined':
      case 'left': {
        // Membership is re-derived from the next snapshot; these events only
        // matter for reacting immediately rather than up to 50 ms later.
        if (msg.event === 'left') {
          const gone = String(data.playerId ?? '');
          this.state.peers = this.state.peers.filter((p) => p.id !== gone);
          this.emit();
        }
        break;
      }
      case 'pong': {
        const sent = Number(data.t);
        if (Number.isFinite(sent)) {
          this.ping.record(performance.now() - sent);
          this.state.rtt = this.ping.rtt;
          this.emit();
        }
        break;
      }
      case 'error': {
        this.state.status = 'error';
        this.state.error = String(data.message ?? 'match error');
        this.emit();
        break;
      }
      default:
        break;
    }
  }

  private peersFrom(rows: unknown): MatchPeer[] {
    if (!Array.isArray(rows)) return [];
    return rows.map((r) => ({
      id: String(r.id),
      name: String(r.name),
      team: Number(r.team) || 0,
      rtt: Number(r.rtt) || 0,
      stale: Boolean(r.stale),
    }));
  }

  /** Whether the roster changed in a way the UI would show. Positions change
   * every snapshot; names and membership almost never do. */
  private peersChanged(next: MatchPeer[]): boolean {
    const prev = this.state.peers;
    if (prev.length !== next.length) return true;
    return next.some((p, i) => {
      const q = prev[i];
      return !q || q.id !== p.id || q.stale !== p.stale || Math.abs(q.rtt - p.rtt) > 5;
    });
  }

  private reset(status: SessionState['status']): void {
    this.state = {
      status,
      room: '',
      map: this.state.map,
      playerId: '',
      peers: [],
      error: '',
      rtt: 0,
      host: '',
      invites: this.state.invites,
    };
    this.predictor.reset();
    this.snapshots.clear();
    this.ping.reset();
    this.outbox = [];
    this.pendingCorrection = null;
  }

  private emit(): void {
    this.onChange({
      ...this.state,
      peers: [...this.state.peers],
      invites: [...this.state.invites],
    });
  }
}
