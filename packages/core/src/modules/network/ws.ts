/**
 * Client for the `/ws` `network` channel: keeps a live peer/presence snapshot the
 * Peers widget renders, and exposes intents (list/connect/pair) that drive the
 * backend `PeerHub`. One shared store, re-synced on every socket (re)connect — the
 * same pattern as the agent capability manifest.
 */
import { onSocketOpen, sendChannel, subscribeChannel } from '../../ws';
import { toastsStore } from '../../toasts';
import type { NodeIdentity, PairResult, PeerInfo, PeerMetrics, PeersSnapshot } from './api';

export interface NetworkState {
  self: NodeIdentity | null;
  peers: Record<string, PeerInfo>;
  lastError: string | null;
}

let state: NetworkState = { self: null, peers: {}, lastError: null };
const listeners = new Set<() => void>();
const pairListeners = new Set<(r: PairResult) => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

export function getNetworkState(): NetworkState {
  return state;
}

export function subscribeNetwork(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Listen for one-off pair_result replies (invite redemption from the widget). */
export function onPairResult(listener: (r: PairResult) => void): () => void {
  pairListeners.add(listener);
  return () => {
    pairListeners.delete(listener);
  };
}

function applySnapshot(snap: PeersSnapshot): void {
  const peers: Record<string, PeerInfo> = {};
  for (const p of snap.peers) peers[p.node_id] = p;
  state = { ...state, self: snap.self, peers };
  emit();
}

function applyPeerUpdate(peer: PeerInfo): void {
  const oldPeer = state.peers[peer.node_id];

  if (oldPeer?.status !== peer.status) {
    if (peer.status === 'connected') {
      toastsStore.add('success', 'Peer Connected', `${peer.node_name} is now connected.`);
    } else if (peer.status === 'disconnected') {
      toastsStore.add(
        'info',
        'Peer Disconnected',
        `${oldPeer?.node_name || peer.node_name} disconnected.`,
      );
    }
  }

  const peers = { ...state.peers };
  if (peer.status === 'disconnected') delete peers[peer.node_id];
  else peers[peer.node_id] = peer;
  state = { ...state, peers };
  emit();
}

let started = false;

/** Subscribe to the `network` channel and (re)request a snapshot on connect. */
export function initNetwork(): void {
  if (started) return;
  started = true;
  subscribeChannel('network', (msg) => {
    if (msg.event === 'peers') applySnapshot(msg.data as PeersSnapshot);
    else if (msg.event === 'peer_update') applyPeerUpdate((msg.data as { peer: PeerInfo }).peer);
    else if (msg.event === 'error') {
      state = { ...state, lastError: (msg.data as { message: string }).message };
      emit();
    } else if (msg.event === 'pair_result') {
      pairListeners.forEach((l) => l(msg.data as PairResult));
      const res = msg.data as PairResult;
      if (res.ok && res.peer) {
        toastsStore.add('success', 'Pairing Successful', `Paired with peer ${res.peer.node_name}.`);
      } else if (res.error) {
        toastsStore.add('error', 'Pairing Failed', res.error);
      }
    }
  });
  onSocketOpen(() => sendChannel('network', 'list_peers', {}));
}

export function requestPeers(): void {
  sendChannel('network', 'list_peers', {});
}

export function connectViaChannel(address: string): void {
  sendChannel('network', 'connect', { address, transport: 'direct' });
}

/**
 * Subscribe to the Peer Monitor's periodic `peer_metrics` push (also delivered
 * once in reply to `requestMetrics`). Returns an unsubscribe function.
 */
export function subscribeMetrics(handler: (metrics: PeerMetrics[]) => void): () => void {
  return subscribeChannel('network', (msg) => {
    if (msg.event === 'peer_metrics') {
      handler((msg.data as { metrics: PeerMetrics[] }).metrics ?? []);
    }
  });
}

/** Ask the backend for an immediate metrics snapshot (e.g. on panel open). */
export function requestMetrics(): void {
  sendChannel('network', 'list_metrics', {});
}

export function redeemViaChannel(invite: string): void {
  sendChannel('network', 'pair_redeem', { invite });
}
