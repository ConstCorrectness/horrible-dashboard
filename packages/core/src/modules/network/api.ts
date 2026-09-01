import { apiDelete, apiGet, apiPost } from '../../api';

/** A node's public identity (this node, or a peer's). */
export interface NodeIdentity {
  node_id: string;
  public_key: string;
  node_name: string;
  capabilities: string[];
}

export type PeerStatus = 'connected' | 'connecting' | 'disconnected' | 'blocked';

/**
 * How a link was established. `webrtc` was missing here while the backend has
 * emitted it since the WebRTC transport landed, so any narrowing on this union
 * silently excluded those peers.
 */
export type PeerTransport = 'direct' | 'relay' | 'lan' | 'webrtc';

/**
 * One thing a peer offers, with live detail: `hassault` plus how many matches are
 * open, `inference` plus the accelerator and which model is loaded.
 *
 * Additive to `capabilities`, never a replacement — that flat list is what the
 * Android client sends, what older nodes send, and what is signed into a commons
 * profile. A peer that sends no `caps` has this synthesized from it backend-side,
 * so this array is always populated for a connected peer.
 */
export interface PeerCapability {
  id: string;
  version: number;
  attrs: Record<string, unknown>;
}

export interface PeerInfo {
  node_id: string;
  node_name: string;
  public_key: string;
  transport: PeerTransport;
  address: string | null;
  status: PeerStatus;
  trusted: boolean;
  last_seen: number | null;
  capabilities: string[];
  caps: PeerCapability[];
}

export interface PeersSnapshot {
  self: NodeIdentity;
  peers: PeerInfo[];
}

export interface InviteResponse {
  invite: string;
  token: string;
  expires: number;
}

export interface PairResult {
  ok: boolean;
  peer?: PeerInfo | null;
  error?: string | null;
}

/** Live link health for one peer, sampled by the backend Peer Monitor. */
export interface PeerMetrics {
  node_id: string;
  node_name: string;
  transport: PeerTransport;
  status: PeerStatus;
  rtt_ms: number | null;
  bytes_in: number;
  bytes_out: number;
  msgs_in: number;
  msgs_out: number;
  last_seen: number | null;
}

export interface AskPeerResult {
  ok: boolean;
  answer?: string | null;
  error?: string | null;
}

/** One phase of the message path, as percentiles. Never a mean — the distribution
 * is bimodal whenever something is blocking the pump, and a mean is precisely the
 * statistic that hides that. */
export interface BenchPhase {
  phase: string;
  msgType: string;
  count: number;
  p50Ms: number;
  p90Ms: number;
  p99Ms: number;
  maxMs: number;
}

export interface BenchResult {
  mode: string;
  transport: string;
  nodeId: string | null;
  payloadBytes: number;
  iterations: number;
  durationS: number;
  errors: number;
  rtt: BenchPhase | null;
  phases: BenchPhase[];
  /** Round trip minus the local phases this node can account for. Named for the
   * weaker claim on purpose: the peer's own sign/serialize/verify are still
   * inside it, so this is a remainder, not a measured wire time. */
  wireResidualMs: number | null;
  victim: BenchPhase | null;
  bytesSent: number;
  note: string;
}

/** A lease this node granted to a peer. */
export interface GrantedLease {
  leaseId: string;
  holder: string;
  service: string;
  model: string | null;
  grantedAt: number;
  expiresAt: number;
  bytesUsed: number;
}

/** A lease this node holds on a peer, and the local port its tunnel listens on. */
export interface BorrowedLease {
  leaseId: string;
  nodeId: string;
  service: string;
  model: string | null;
  expiresAt: number;
  endpoint: string;
}

export interface LeaseSnapshot {
  granted: GrantedLease[];
  borrowed: BorrowedLease[];
  /** Travels with the list because the two are read together: an empty `granted`
   * means something different on a node with lending switched off than on an idle
   * one. */
  lending?: { enabled: boolean; policy: string };
}

export function getNetworkIdentity(): Promise<NodeIdentity> {
  return apiGet<NodeIdentity>('/network/identity');
}

export function getPeers(): Promise<PeersSnapshot> {
  return apiGet<PeersSnapshot>('/network/peers');
}

export function createInvite(): Promise<InviteResponse> {
  return apiPost<InviteResponse>('/network/invite', {});
}

export function redeemInvite(invite: string): Promise<PairResult> {
  return apiPost<PairResult>('/network/pair', { invite });
}

export function connectPeer(address: string): Promise<PairResult> {
  return apiPost<PairResult>('/network/connect', { address, transport: 'direct' });
}

export function disconnectPeer(nodeId: string): Promise<{ ok: boolean }> {
  return apiDelete<{ ok: boolean }>(`/network/peers/${nodeId}`);
}

/** Ask a connected peer's agent a question; the remote turn is gated read-only. */
export function askPeer(peerId: string, prompt: string): Promise<AskPeerResult> {
  return apiPost<AskPeerResult>('/network/ask-peer', { peer_id: peerId, prompt });
}

/**
 * Time the link to a peer, or (`mode: 'local'`) this machine's own crypto floor.
 * The backend refuses a second concurrent run with a 409 — two benches would each
 * measure the other's traffic.
 */
export function runBench(
  body: { node_id?: string; mode?: 'echo' | 'sweep' | 'local'; count?: number },
): Promise<{ results: BenchResult[] }> {
  return apiPost<{ results: BenchResult[] }>('/network/bench', body);
}

export function getLeases(): Promise<LeaseSnapshot> {
  return apiGet<LeaseSnapshot>('/network/leases');
}

/** End a lease in either direction: give back one borrowed, reclaim one lent. */
export function endLease(leaseId: string): Promise<{ ok: boolean; released?: string }> {
  return apiDelete<{ ok: boolean; released?: string }>(`/network/leases/${leaseId}`);
}
