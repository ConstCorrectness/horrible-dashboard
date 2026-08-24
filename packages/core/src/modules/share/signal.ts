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

/** One frame of the SDP/ICE exchange. */
export type SignalFrame =
  | { kind: 'offer'; sessionId: string; sdp: string }
  | { kind: 'answer'; sessionId: string; sdp: string }
  | { kind: 'ice'; sessionId: string; candidate: RTCIceCandidateInit }
  /** The host stopped capturing. Distinct from a dropped connection: a guest
   *  should be told "they stopped sharing", not left watching a frozen frame. */
  | { kind: 'bye'; sessionId: string };

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
      return typeof p.sdp === 'string' && p.sdp ? { kind: p.kind, sessionId, sdp: p.sdp } : null;
    case 'ice': {
      const candidate = p.candidate;
      if (typeof candidate !== 'object' || candidate === null) return null;
      return { kind: 'ice', sessionId, candidate: candidate as RTCIceCandidateInit };
    }
    case 'bye':
      return { kind: 'bye', sessionId };
    default:
      return null;
  }
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
