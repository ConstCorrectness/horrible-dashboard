import { apiDelete, apiGet, apiPost } from '../../api';

/** A node's public identity (this node, or a peer's). */
export interface NodeIdentity {
  node_id: string;
  public_key: string;
  node_name: string;
  capabilities: string[];
}

export type PeerStatus = 'connected' | 'connecting' | 'disconnected' | 'blocked';

export interface PeerInfo {
  node_id: string;
  node_name: string;
  public_key: string;
  transport: 'direct' | 'relay' | 'lan';
  address: string | null;
  status: PeerStatus;
  trusted: boolean;
  last_seen: number | null;
  capabilities: string[];
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
  transport: 'direct' | 'relay' | 'lan';
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
