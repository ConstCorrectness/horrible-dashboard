/**
 * REST client for the share module. Mirrors `backend/modules/share/models.py`;
 * the backend stays the source of truth for these shapes.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

/**
 * The grant ladder, weakest first. A participant sits at exactly one rung, and a
 * rung answers every capability question below it.
 *
 * A ladder rather than independent flags on purpose: the interesting failure is
 * granting `terminal` to somebody you meant to give `edit`, and an ordered ladder
 * makes "more than I meant" visible at a glance instead of hiding it in a grid of
 * checkboxes.
 */
export const GRANT_LADDER = ['view', 'cursor', 'edit', 'terminal', 'agent', 'control'] as const;
export type GrantLevel = (typeof GRANT_LADDER)[number];

/** What each rung actually buys, for the tooltip beside it. */
export const GRANT_BLURB: Record<GrantLevel, string> = {
  view: 'Can see shared panes. Cannot change anything.',
  cursor: 'Can also show their pointer and selection.',
  edit: 'Can edit panes that support collaborative editing.',
  terminal: 'Can run commands, subject to your own agent permission rules.',
  agent: 'Can drive your agent, subject to your own agent permission rules.',
  control: 'Full control of the session. Grant only to someone you would hand the keyboard.',
};

export type ShareMode = 'semantic' | 'pixels' | 'both';

export interface Participant {
  person_id: string;
  node_id: string;
  name: string;
  role: 'host' | 'guest';
  grant: GrantLevel;
  joined_at: number;
  following: boolean;
}

export interface ShareSession {
  id: string;
  title: string;
  mode: ShareMode;
  host_node: string;
  host_person: string;
  created_at: number;
  participants: Participant[];
  /** Monotonic. Drop a broadcast that arrived out of order rather than rendering
   *  a participant list that jumps backwards. */
  revision: number;
  /** The public relay link, once one is minted. Empty means fabric-only. */
  link: string;
  /**
   * What the guests can actually see, as counted by this browser when it last
   * published. Null until something has been published. Shown to the host,
   * because a redaction model nobody can audit is a redaction model nobody
   * trusts.
   */
  mirror_panes: number | null;
  mirror_hidden: number | null;
}

export interface RemoteSession {
  id: string;
  title: string;
  host_node: string;
  host_name: string;
  grant: GrantLevel;
  following: boolean;
  joined_at: number;
}

export interface ShareInvite {
  session_id: string;
  title: string;
  host: string;
  host_name: string;
  host_device: string;
  person_id: string;
  ts: number;
  expires_at: number;
}

export interface Invitee {
  name: string;
  username: string;
  person_id: string;
  friend_code: string;
  /** Whether any of their online machines advertised the `share` capability. A
   *  friend on an older build is listed but not offerable, rather than absent
   *  and unexplained. */
  can_share: boolean;
  devices_online: number;
}

export interface SessionOut {
  hosting: ShareSession | null;
  joined: RemoteSession[];
  invites: ShareInvite[];
}

export interface ActionResult {
  ok: boolean;
  error: string | null;
  detail: Record<string, unknown> | null;
}

export function getShareState(): Promise<SessionOut> {
  return apiGet<SessionOut>('/share');
}

export function startSession(title: string, mode: ShareMode = 'semantic'): Promise<ShareSession> {
  return apiPost<ShareSession>('/share/session', { title, mode });
}

export function stopSession(): Promise<ActionResult> {
  return apiDelete<ActionResult>('/share/session');
}

export function getInvitees(): Promise<Invitee[]> {
  return apiGet<Invitee[]>('/share/invitees');
}

export function invitePerson(personId: string): Promise<ActionResult> {
  return apiPost<ActionResult>('/share/invite', { person_id: personId });
}

export function setGrant(personId: string, grant: GrantLevel): Promise<ActionResult> {
  return apiPost<ActionResult>('/share/grant', { person_id: personId, grant });
}

export function revokeAll(): Promise<ActionResult> {
  return apiPost<ActionResult>('/share/revoke-all', {});
}

export function joinSession(sessionId: string, hostNode: string): Promise<ActionResult> {
  return apiPost<ActionResult>('/share/join', { session_id: sessionId, host_node: hostNode });
}

export function leaveSession(sessionId: string): Promise<ActionResult> {
  return apiPost<ActionResult>('/share/leave', { session_id: sessionId, host_node: '' });
}

/**
 * The host's own view of the public link.
 *
 * `ingestUrl` never rides the `/ws` broadcast — it is publish authority, and the
 * broadcast reaches every guest — so it is fetched from this route, which only
 * the host's own browser can call.
 */
export interface ShareLink {
  view_url: string;
  ingest_url: string;
  expires_at: number;
  /** Set when minting failed for a reason the host can act on. */
  error: string;
}

export function getLink(): Promise<ShareLink> {
  return apiGet<ShareLink>('/share/link');
}

export function mintLink(options: { ttlS?: number; passphrase?: string } = {}): Promise<ShareLink> {
  return apiPost<ShareLink>('/share/link', {
    ttl_s: options.ttlS ?? null,
    passphrase: options.passphrase ?? '',
  });
}

export function revokeLink(): Promise<ActionResult> {
  return apiDelete<ActionResult>('/share/link');
}

/**
 * What the relay says about the live link.
 *
 * Four states, and the pane renders all four. `unknown` is **not** a synonym for
 * `gone`: the relay's registry is in one process's memory, so `gone` means every
 * viewer holding the URL is on a dead page and the host should mint a new one,
 * while `unknown` means we could not ask and the link may be perfectly fine.
 * Collapsing them trades one wrong chip for another.
 */
export type RelayState = 'live' | 'idle' | 'gone' | 'unknown';

export interface LinkStatus {
  state: RelayState;
  /** True only for `live`. A convenience for the chip, never the whole answer. */
  live: boolean;
  /** The relay's watcher count — a different number from `StreamState.peers`,
   *  which counts fabric guests. Deliberately not merged with it. */
  viewers: number;
  expires_at: number;
  /** Something the host can act on, or empty when all is well. */
  detail: string;
}

export function getLinkStatus(): Promise<LinkStatus> {
  return apiGet<LinkStatus>('/share/link/status');
}

/**
 * Whether the relay is pushing this stream to RTMP, and where it could.
 *
 * `label` is a name ("Twitch"), never the target URL — that URL embeds the
 * stream key, and this response goes to the browser. `available` lists
 * destination **ids** that have a key stored; the keys themselves stay in
 * `secrets.db` on the node.
 */
export interface RestreamState {
  live: boolean;
  label: string;
  available: string[];
  error: string;
}

export function getRestream(): Promise<RestreamState> {
  return apiGet<RestreamState>('/share/restream');
}

export function startRestream(destination: string): Promise<RestreamState> {
  return apiPost<RestreamState>('/share/restream', { destination });
}

export function stopRestream(): Promise<ActionResult> {
  return apiDelete<ActionResult>('/share/restream');
}
