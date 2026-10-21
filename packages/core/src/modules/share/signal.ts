/**
 * The signalling vocabulary, and the pure half of the WebRTC handshake.
 *
 * Split from `rtc.ts` for the reason `hassault/net.ts` is split from
 * `session.ts`: the parts worth testing must not import anything that touches a
 * socket or an `RTCPeerConnection`, or they can only be exercised in a browser.
 * Everything here is data and decisions; `rtc.ts` is the wiring.
 *
 * These frames ride the fabric's `share_signal` envelope, which the nodes relay
 * **without inspecting** — see `backend/modules/share/fabric.py`. That is the
 * point of the split: the media path is browser to browser, and the fabric's job
 * is only to carry an offer to a machine that has already been authenticated and
 * trusted. Nothing here is a security boundary; the gate ran when the guest
 * joined, and it decides who is in `participants` at all.
 */

/**
 * One frame of the SDP/ICE exchange.
 *
 * `epoch` names the offer a frame belongs to. Every offer is a fresh
 * `RTCPeerConnection` on both ends, so the moment the host can re-offer — after a
 * failure, or because a guest asked — an answer to the *previous* offer can still
 * be in flight, and applying it to the new connection pairs it with the wrong
 * DTLS fingerprint: a connection that looks negotiated and never carries a frame.
 * Optional on the wire, so a peer on an older build that never sends one is
 * matched against whatever connection is current, which is what it always got.
 */
export type SignalFrame =
  | { kind: 'offer'; sessionId: string; sdp: string; epoch?: number }
  | { kind: 'answer'; sessionId: string; sdp: string; epoch?: number }
  | { kind: 'ice'; sessionId: string; candidate: RTCIceCandidateInit; epoch?: number }
  /** The host stopped capturing. Distinct from a dropped connection: a guest
   *  should be told "they stopped sharing", not left watching a frozen frame. */
  | { kind: 'bye'; sessionId: string }
  /**
   * Guest → host: "I hold no connection for you; offer me one."
   *
   * The host's offer is one-shot, and a guest can be without a listener when it
   * lands — a reload, or a join whose `joined` event raced the offer — after
   * which the host's connection waits in `have-local-offer` for an answer nobody
   * is going to send. A host that is not sharing ignores it; sharing starts with
   * an offer to every guest anyway.
   */
  | { kind: 'ready'; sessionId: string };

/** A finite, non-negative integer epoch, or nothing. A malformed one is dropped
 *  rather than failing the frame: the frame is still valid without it. */
function epochOf(value: unknown): { epoch?: number } {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? { epoch: value } : {};
}

/**
 * Validate an inbound signal.
 *
 * Returns `null` for anything malformed rather than throwing. A peer on a newer
 * build may send a frame kind this one has never heard of, and the right
 * response to that is to ignore it, not to tear down a working session.
 */
export function parseSignal(payload: unknown): SignalFrame | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const p = payload as Record<string, unknown>;
  const sessionId = typeof p.sessionId === 'string' ? p.sessionId : '';
  if (!sessionId) return null;

  switch (p.kind) {
    case 'offer':
    case 'answer':
      return typeof p.sdp === 'string' && p.sdp
        ? { kind: p.kind, sessionId, sdp: p.sdp, ...epochOf(p.epoch) }
        : null;
    case 'ice': {
      const candidate = p.candidate;
      if (typeof candidate !== 'object' || candidate === null) return null;
      return {
        kind: 'ice',
        sessionId,
        candidate: candidate as RTCIceCandidateInit,
        ...epochOf(p.epoch),
      };
    }
    case 'bye':
      return { kind: 'bye', sessionId };
    case 'ready':
      return { kind: 'ready', sessionId };
    default:
      return null;
  }
}

/**
 * Whether a frame tagged `epoch` belongs to the connection at `current`.
 *
 * An untagged frame matches — see {@link SignalFrame}. A connection that does
 * not exist yet (`current` null) matches nothing.
 */
export function sameEpoch(epoch: number | undefined, current: number | null): boolean {
  if (current === null) return false;
  return epoch === undefined || epoch === current;
}

/**
 * How long a peer connection may sit in `disconnected` before the host gives up
 * on it and offers a fresh one.
 *
 * `disconnected` is often a blip — a Wi-Fi roam, a laptop lid — that ICE recovers
 * from on its own within a couple of seconds, and tearing down there would drop a
 * picture that was about to come back. But it is not guaranteed to progress to
 * `failed` either, so waiting for that would leave a guest on a frozen frame.
 */
export const DISCONNECT_GRACE_MS = 5000;

/**
 * How long an offer may go unanswered before the host offers again.
 *
 * The other half of the dropped-offer problem `ready` addresses: `ready` covers a
 * guest that knows it has nothing, this covers an offer lost in transit to a
 * guest that is still listening.
 */
export const ANSWER_TIMEOUT_MS = 15_000;

/** The shortest gap between two `ready`s the host will honour from one guest. A
 *  guest stuck in a loop must not be able to make the host churn connections. */
export const READY_MIN_INTERVAL_MS = 1500;

/**
 * The wait before re-offering to a guest after attempt number `attempt` (0-based)
 * failed: 1s, 2s, 4s, 8s, 16s, then every 30s.
 *
 * Doubling because the common causes — a guest on a flaky network, a laptop
 * asleep — either clear in seconds or not for a long time, and a fixed short
 * retry would spend the long case renegotiating at a machine that is not there.
 * Never gives up: a guest who is still in the session should get the picture
 * back when they wake up, without anyone pressing anything.
 */
export function reofferDelay(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt));
  return n < 5 ? 1000 * 2 ** n : 30_000;
}

/** ICE server config, as the browser's `RTCConfiguration` wants it. */
export interface IceConfig {
  iceServers: RTCIceServer[];
}

/** The settings this reads. Passed in so the builder stays pure. */
export interface IceSettings {
  stunServer?: string;
  turnUrl?: string;
  turnUsername?: string;
  turnCredential?: string;
}

/**
 * Build the ICE configuration.
 *
 * Deliberately the same semantics as `_ice_servers` in
 * `backend/modules/network/transport/webrtc.py`: the STUN setting is a bare
 * `host:port` and gets the `stun:` scheme added, while the TURN setting is a
 * full URL and is passed through. Two readers of one pair of settings that
 * disagreed about whether a scheme was included would fail as "ICE just does not
 * connect", with nothing in any log to say why.
 *
 * A TURN entry with no credentials is dropped rather than sent: browsers reject
 * the whole `RTCConfiguration` on a malformed server entry, so one
 * half-configured TURN would take STUN down with it and break the case that was
 * working.
 */
export function buildIceConfig(settings: IceSettings): IceConfig {
  const iceServers: RTCIceServer[] = [];

  const stun = (settings.stunServer ?? '').trim();
  if (stun) iceServers.push({ urls: [`stun:${stun}`] });

  const turnUrl = (settings.turnUrl ?? '').trim();
  const username = (settings.turnUsername ?? '').trim();
  const credential = (settings.turnCredential ?? '').trim();
  if (turnUrl && username && credential) {
    iceServers.push({ urls: [turnUrl], username, credential });
  }

  return { iceServers };
}

/**
 * Whether a TURN relay is configured but unusable.
 *
 * Worth surfacing rather than silently dropping: TURN is the thing that makes a
 * symmetric NAT work, so "I set up TURN and it still fails" is exactly the
 * situation where a missing username has to be visible.
 */
export function turnIsIncomplete(settings: IceSettings): boolean {
  const turnUrl = (settings.turnUrl ?? '').trim();
  if (!turnUrl) return false;
  return !(settings.turnUsername ?? '').trim() || !(settings.turnCredential ?? '').trim();
}
