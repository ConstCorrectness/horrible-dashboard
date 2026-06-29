/**
 * Client for the `/ws` `commons` channel: keeps the commons connection + directory /
 * search snapshot the Commons widget renders, and exposes the browse/search intents
 * that drive the backend `CommonsClient`. Profiles are published + signed server-side
 * (the node key never leaves the backend). See modules/network commons.py and
 * docs/modules/commons.mdx.
 */
import { onSocketOpen, sendChannel, subscribeChannel } from '../../ws';
import { toastsStore } from '../../toasts';

export interface CommonsProfile {
  node_id: string;
  public_key: string;
  display_name: string;
  headline: string;
  bio: string | null;
  avatar_url: string | null;
  tags: string[];
  seeking: string | null;
  agent_capabilities: string[];
  links: { label: string; url: string }[];
  visibility: string;
  status?: string; // present on directory entries (connected/disconnected)
  vouchers?: string[]; // node_ids that have vouched for this profile
  trust_tier?: 'blocked' | 'known' | 'vouched' | 'unknown'; // viewer-relative, node-side
  sig: string | null;
}

export interface CommonsCandidate {
  profile: CommonsProfile;
  score: number;
}

export interface CommonsRequest {
  request_id: string;
  from: CommonsProfile;
  note: string;
}

export interface CommonsState {
  connected: boolean;
  url: string | null;
  self: { node_id: string; node_name: string } | null;
  myProfile: CommonsProfile | null;
  directory: CommonsProfile[];
  results: CommonsCandidate[];
  requests: CommonsRequest[];
}

let state: CommonsState = {
  connected: false,
  url: null,
  self: null,
  myProfile: null,
  directory: [],
  results: [],
  requests: [],
};
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((l) => l());
}

export function getCommonsState(): CommonsState {
  return state;
}

export function subscribeCommons(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

let started = false;

/** Subscribe to the `commons` channel and request a snapshot on (re)connect. */
export function initCommons(): void {
  if (started) return;
  started = true;
  subscribeChannel('commons', (msg) => {
    const d = (msg.data ?? {}) as Record<string, unknown>;
    if (msg.event === 'state') {
      state = {
        connected: Boolean(d.connected),
        url: (d.url as string | null) ?? null,
        self: (d.self as CommonsState['self']) ?? null,
        myProfile: (d.my_profile as CommonsProfile) ?? null,
        directory: (d.directory as CommonsProfile[]) ?? [],
        results: (d.results as CommonsCandidate[]) ?? [],
        requests: (d.requests as CommonsRequest[]) ?? [],
      };
      emit();
    } else if (msg.event === 'directory') {
      state = { ...state, directory: (d.profiles as CommonsProfile[]) ?? [] };
      emit();
    } else if (msg.event === 'candidates') {
      state = { ...state, results: (d.results as CommonsCandidate[]) ?? [] };
      emit();
    } else if (msg.event === 'requests') {
      state = { ...state, requests: (d.requests as CommonsRequest[]) ?? [] };
      emit();
    } else if (msg.event === 'met') {
      const peer = (d.peer ?? {}) as { node_name?: string; node_id?: string };
      toastsStore.add(
        'success',
        'Commons',
        `Connected to ${peer.node_name ?? peer.node_id ?? 'peer'}.`,
      );
    } else if (msg.event === 'declined') {
      toastsStore.add('info', 'Commons', 'Your request to meet was declined.');
    } else if (msg.event === 'error') {
      toastsStore.add('error', 'Commons', String(d.message ?? 'commons error'));
    }
  });
  onSocketOpen(() => sendChannel('commons', 'state', {}));
}

export function commonsConnect(url?: string): void {
  sendChannel('commons', 'connect', url ? { url } : {});
}

export function commonsSearch(query: string, limit = 10): void {
  sendChannel('commons', 'search', { query, limit });
}

export function commonsRefresh(): void {
  sendChannel('commons', 'directory', {});
}

export function commonsPublish(): void {
  sendChannel('commons', 'publish', {});
}

/** Ask another node's human to meet (gated by their explicit consent). */
export function commonsRequest(nodeId: string, note = ''): void {
  sendChannel('commons', 'request', { nodeId, note });
}

/** Accept or decline an inbound meet request. */
export function commonsRespond(requestId: string, accept: boolean): void {
  sendChannel('commons', 'respond', { requestId, accept });
}

/** Block a node — auto-declines its requests and the peer fabric refuses it too. */
export function commonsBlock(nodeId: string): void {
  sendChannel('commons', 'block', { nodeId });
}

export function commonsUnblock(nodeId: string): void {
  sendChannel('commons', 'unblock', { nodeId });
}

/** Publish a signed attestation that you trust a node (raises it to `vouched`). */
export function commonsVouch(nodeId: string): void {
  sendChannel('commons', 'vouch', { nodeId });
}

/** Report a node to the index (recorded for moderation; not auto-acted). */
export function commonsReport(nodeId: string, reason = ''): void {
  sendChannel('commons', 'report', { nodeId, reason });
}

/** Update this node's profile fields and republish (signed backend-side). */
export function commonsSetProfile(fields: {
  headline?: string;
  bio?: string;
  tags?: string;
  seeking?: string;
  visibility?: string;
}): void {
  sendChannel('commons', 'set_profile', fields);
}
