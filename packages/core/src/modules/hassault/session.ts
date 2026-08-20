/**
 * The `hassault` `/ws` channel, as a small stateful object the pane can drive.
 *
 * Split from `net.ts` on purpose: everything in `net.ts` is pure and unit-tested
 * headless, and importing the shell's socket module would open a real WebSocket
 * at import time, which is exactly what breaks a node test run.
 */
import type { MatchInvite } from './api';
import { clearInviteNotification } from './invite-notify';
import { subscribeChannel, sendChannel } from '../../ws';
import {
  PingTracker,
  Predictor,
  SnapshotBuffer,
  type Command,
  type Fx,
  type MoveState,
  type NoiseEvent,
  type SelfState,
  type ShotFx,
  type Snapshot,
} from './net';

export interface MatchPeer {
  id: string;
  name: string;
  team: number;
  rtt: number;
  stale: boolean;
  hp: number;
  alive: boolean;
  kills: number;
  deaths: number;
  bot: boolean;
  /** Crouch animation, 0..1 — the avatar is drawn to this height. */
  crouch: number;
}

/** One line of the kill feed, already phrased. */
export interface KillNote {
  id: number;
  text: string;
  /** Whether we did it or it was done to us — worth colouring differently. */
  mine: boolean;
  ts: number;
}

/** How long a kill stays on the feed. */
const KILL_TTL_MS = 8000;

export interface SessionState {
  status: 'idle' | 'joining' | 'joined' | 'error';
  room: string;
  map: string;
  playerId: string;
  peers: MatchPeer[];
  error: string;
  rtt: number;
  /** Our own health, ammo and hitmarkers — never sent to anyone else. */
  you: SelfState | null;
  /** Kills per team, indexed by team number. */
  scores: number[];
  killfeed: KillNote[];
  /**
   * The node hosting this match, empty when it is our own.
   *
   * Only a label for the UI: a remote match is driven through exactly the same
   * events, because our backend proxies for us and hands the browser the same
   * `welcome` and `snapshot` frames it would produce locally.
   */
  host: string;
  /** Whether this match is being adjudicated by the game server. A rated result
   * is the only kind anything comparative may ever be built on — see
   * `backend/games_server/hassault_rooms.py`. */
  ranked: boolean;
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
    you: null,
    scores: [0, 0],
    killfeed: [],
    host: '',
    ranked: false,
    invites: [],
  };

  /** Fires on any change worth re-rendering the surrounding UI for. */
  onChange: (state: SessionState) => void = () => {};
  /**
   * The most recent authoritative word on us, consumed by the render loop.
   *
   * Carries the private movement block alongside the public row: position comes
   * from the row, momentum from `you.move`, and reconciliation needs both.
   */
  pendingCorrection: {
    row: Snapshot['players'][number];
    move: MoveState | null;
    ack: number;
  } | null = null;
  /**
   * Shots to draw, drained by the render loop.
   *
   * Kept here rather than emitted through `onChange` because tracers are a
   * render-loop concern at 60 fps and React has no business in that path.
   */
  pendingShots: ShotFx[] = [];
  /**
   * Noises this player can hear, drained by the render loop.
   *
   * Server-filtered to what is actually audible from where we are — see
   * `backend/modules/hassault/noise.py`. Our *own* noises are deliberately absent
   * and synthesized locally instead: they need no round trip, and a footstep that
   * arrives 50 ms after the step does not sound like a footstep.
   */
  pendingNoise: NoiseEvent[] = [];

  private unsubscribe: (() => void) | null = null;
  private outbox: Command[] = [];
  private lastSend = 0;
  private lastPing = 0;
  private killSeq = 0;

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
   * Join a match.
   *
   * Three destinations, one call. With `host` set it is a friend's match on
   * their node; with `ranked` it is a **rated** match simulated by the game
   * server. Both are proxied by our backend, and everything after this point is
   * identical either way — which is the point: the client speaks one protocol
   * and the node decides where the room is.
   */
  join(map: string, name: string, room?: string, host?: string, ranked?: boolean): void {
    this.connect();
    const invites = this.state.invites;
    this.reset('joining');
    // Invitations outlive a join: the one being accepted may fail, and the
    // others are still valid offers.
    this.state.invites = invites;
    this.state.map = map;
    this.state.host = host ?? '';
    this.state.ranked = ranked ?? false;
    this.emit();
    sendChannel('hassault', 'join', { map, name, room, host, ranked });
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
    const invite = this.state.invites.find((i) => i.room === room);
    this.state.invites = this.state.invites.filter((i) => i.room !== room);
    if (invite) clearInviteNotification(invite.host, invite.room);
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

  /**
   * Add bots to the match we are hosting.
   *
   * Host-only, and the backend enforces it: a guest's socket is bound to a
   * remote match, and there is no fabric message for reshaping someone else's
   * roster from a pane they cannot see.
   */
  addBots(count = 1, skill = 'normal'): void {
    if (this.state.status !== 'joined' || this.state.host) return;
    sendChannel('hassault', 'add_bot', { count, skill });
  }

  /** Remove bots, newest first. Defaults to one — this is a button, not the
   * agent tool, where omitting the count means "all of them". */
  removeBots(count = 1): void {
    if (this.state.status !== 'joined' || this.state.host) return;
    sendChannel('hassault', 'remove_bot', { count });
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
        this.state.scores = Array.isArray(data.scores) ? (data.scores as number[]) : [0, 0];
        // The invitation has been taken up; leaving it on screen would invite
        // the same room twice. It is cleared from the notification surfaces too —
        // the toast, the bell and any OS notification are showing the same
        // invite, and joining is an answer to all of them.
        for (const taken of this.state.invites.filter((i) => i.room === this.state.room)) {
          clearInviteNotification(taken.host, taken.room);
        }
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
        if (mine) {
          this.pendingCorrection = {
            row: mine,
            move: snapshot.you?.move ?? null,
            ack: snapshot.ack,
          };
        }

        // Drained here rather than through `onChange`: the audio synth and the
        // direction ring are render-loop concerns at 60 fps, exactly like tracers.
        for (const heard of snapshot.you?.noise ?? []) {
          if (this.pendingNoise.length < 32) this.pendingNoise.push(heard);
        }

        for (const fx of snapshot.fx ?? []) this.absorb(fx);
        const peers = this.peersFrom(snapshot.players);
        const you = snapshot.you ?? null;
        const feedChanged = this.pruneKillfeed();
        if (feedChanged || this.peersChanged(peers) || this.youChanged(you)) {
          this.state.peers = peers;
          this.state.you = you;
          if (Array.isArray(snapshot.scores)) this.state.scores = snapshot.scores;
          this.emit();
        } else {
          // Still adopt it — the render loop reads `you.alive` every frame even
          // when nothing in it is worth re-rendering the HUD for.
          this.state.you = you;
        }
        break;
      }
      case 'roster': {
        // Bots joined or left. Membership itself arrives with the next snapshot;
        // this only exists so the pane reacts within a frame rather than 50 ms.
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

  /** Fold one batched effect into the state the UI reads. */
  private absorb(fx: Fx): void {
    if (fx.kind === 'shot') {
      // Bounded: a stall must not leave the render loop a thousand tracers to
      // draw the instant it resumes.
      if (this.pendingShots.length < 64) this.pendingShots.push(fx);
      return;
    }
    if (fx.kind !== 'kill') return;
    const me = this.state.playerId;
    const mine = fx.killer === me || fx.victim === me;
    this.killSeq += 1;
    this.state.killfeed = [
      {
        id: this.killSeq,
        text: `${fx.killerName} ${fx.head ? '⌖' : '·'} ${fx.victimName}`,
        mine,
        ts: Date.now(),
      },
      ...this.state.killfeed,
    ].slice(0, 5);
  }

  /** Drop expired kill notes. Returns whether anything went. */
  private pruneKillfeed(): boolean {
    if (this.state.killfeed.length === 0) return false;
    const cutoff = Date.now() - KILL_TTL_MS;
    const kept = this.state.killfeed.filter((k) => k.ts > cutoff);
    if (kept.length === this.state.killfeed.length) return false;
    this.state.killfeed = kept;
    return true;
  }

  private peersFrom(rows: unknown): MatchPeer[] {
    if (!Array.isArray(rows)) return [];
    return rows.map((r) => ({
      id: String(r.id),
      name: String(r.name),
      team: Number(r.team) || 0,
      rtt: Number(r.rtt) || 0,
      stale: Boolean(r.stale),
      hp: Number(r.hp ?? 0),
      alive: r.alive !== false,
      kills: Number(r.kills) || 0,
      deaths: Number(r.deaths) || 0,
      bot: Boolean(r.bot),
      crouch: Number(r.crouch) || 0,
    }));
  }

  /** Whether the roster changed in a way the UI would show. Positions change
   * every snapshot; names and membership almost never do. */
  private peersChanged(next: MatchPeer[]): boolean {
    const prev = this.state.peers;
    if (prev.length !== next.length) return true;
    return next.some((p, i) => {
      const q = prev[i];
      return (
        !q ||
        q.id !== p.id ||
        q.stale !== p.stale ||
        q.alive !== p.alive ||
        q.kills !== p.kills ||
        q.deaths !== p.deaths ||
        Math.abs(q.hp - p.hp) > 0 ||
        Math.abs(q.rtt - p.rtt) > 5
      );
    });
  }

  /**
   * Whether our own state changed enough to redraw the HUD.
   *
   * Deliberately not a deep equality: `reloadIn` and `respawnIn` tick every
   * snapshot, and comparing them would re-render React twenty times a second
   * forever. The countdown is compared at whole seconds, which is the only
   * resolution anything shows it at.
   */
  private youChanged(next: SelfState | null): boolean {
    const prev = this.state.you;
    if (!prev || !next) return prev !== next;
    if (next.hits.length > 0) return true;
    return (
      prev.hp !== next.hp ||
      prev.alive !== next.alive ||
      prev.weapon !== next.weapon ||
      prev.ammo !== next.ammo ||
      prev.reserve !== next.reserve ||
      prev.reloading !== next.reloading ||
      prev.protected !== next.protected ||
      prev.kills !== next.kills ||
      prev.deaths !== next.deaths ||
      Math.ceil(prev.respawnIn) !== Math.ceil(next.respawnIn)
    );
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
      you: null,
      scores: [0, 0],
      killfeed: [],
      host: '',
      ranked: false,
      invites: this.state.invites,
    };
    this.predictor.reset();
    this.snapshots.clear();
    this.ping.reset();
    this.outbox = [];
    this.pendingCorrection = null;
    this.pendingShots = [];
    this.pendingNoise = [];
  }

  private emit(): void {
    this.onChange({
      ...this.state,
      peers: [...this.state.peers],
      invites: [...this.state.invites],
      killfeed: [...this.state.killfeed],
    });
  }
}
