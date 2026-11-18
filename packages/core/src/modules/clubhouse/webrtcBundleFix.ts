/**
 * Workaround for an `agora-rtc-sdk-ng` SDP generation bug that triggers a fatal
 * RFC 8843 BUNDLE collision in Chromium 130+ / Electron 44 (`sdp_offer_answer.cc`).
 *
 * Why it happens:
 * - Agora's `RemoteSDP` (`Nse`) bundles multiple `m=audio` (and `m=video`) sections
 *   into a single `a=group:BUNDLE 0 1 2 ...` SDP.
 * - On `mid:1` (`m=audio`), `updateRemoteRTPCapabilities` either leaves payload type
 *   111 (`audio/opus`) as `a=fmtp:111 minptime=10;useinbandfec=1` (when
 *   `_isUseLocalCodecs` is true) or runs `Lee()`, which injects
 *   `sprop-stereo=1;stereo=1`.
 * - When a second `m=audio` section (`mid:2`) is added to the same SDP — either via
 *   `createOrRecycleSendMedia` (subscribing to a speaker, which runs `Lee()` and
 *   injects `sprop-stereo=1;stereo=1`) or `createOrRecycleRecvMedia` (publishing a
 *   mono/default local track, which runs `vee()` and omits `sprop-stereo=1;stereo=1`,
 *   and `Iee()`, which strips `a=rtcp-fb:111 transport-cc` when `twcc === false`) —
 *   `mid:1` and `mid:2` end up with different `a=fmtp:111` parameters and different
 *   `a=rtcp-fb:111` lines inside the same BUNDLE group.
 * - Chromium's `ValidateBundleSettings` (`third_party/webrtc/pc/sdp_offer_answer.cc:578`)
 *   rejects `setRemoteDescription` / `setLocalDescription` with:
 *   `INVALID_PARAMETER: A BUNDLE group contains a codec collision between {payload_type: 111...}`
 *   and logs `Warning: Inconsistent congestion control feedback types, ignoring all.`
 *
 * `harmonizeBundledSdp` normalizes `a=fmtp:<pt>` (intersecting parameters so every
 * `m=` section in the BUNDLE group shares the exact same parameter set for a given
 * payload type) and synchronizes `a=rtcp-fb:<pt>` lines across bundled `m=` sections.
 */

function parseFmtpParams(raw: string): Map<string, string> {
  const map = new Map<string, string>();
  for (const part of raw.split(';')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) {
      map.set(trimmed, '');
    } else {
      map.set(trimmed.slice(0, eq).trim(), trimmed.slice(eq + 1).trim());
    }
  }
  return map;
}

function serializeFmtpParams(map: Map<string, string>): string {
  const parts: string[] = [];
  for (const [k, v] of map.entries()) {
    parts.push(v === '' ? k : `${k}=${v}`);
  }
  return parts.join(';');
}

/**
 * Harmonize `a=fmtp:<pt>` and `a=rtcp-fb:<pt>` lines across all `m=` sections in a
 * BUNDLE SDP so Chromium's `ValidateBundleSettings` never rejects a payload type
 * collision or drops `transport-cc` feedback.
 */
export function harmonizeBundledSdp(sdp: string): string {
  if (!sdp.includes('a=group:BUNDLE')) return sdp;

  const eol = sdp.includes('\r\n') ? '\r\n' : '\n';
  const lines = sdp.split(/\r?\n/);

  // Split into session header (index 0) and media sections (indices 1..N).
  const sections: string[][] = [[]];
  for (const line of lines) {
    if (line.startsWith('m=')) {
      sections.push([line]);
    } else {
      sections[sections.length - 1].push(line);
    }
  }
  if (sections.length <= 2) return sdp;

  // Map payloadType -> intersected fmtp params across all m= sections that define it,
  // and unioned rtcp-fb tokens across all m= sections that define that payloadType.
  const fmtpByPt = new Map<string, Map<string, string>>();
  const rtcpFbByPt = new Map<string, string[]>();

  for (let i = 1; i < sections.length; i++) {
    const sec = sections[i];
    for (const line of sec) {
      const fmtpMatch = /^a=fmtp:(\d+)\s+(.+)$/.exec(line);
      if (fmtpMatch) {
        const pt = fmtpMatch[1];
        const params = parseFmtpParams(fmtpMatch[2]);
        const existing = fmtpByPt.get(pt);
        if (!existing) {
          fmtpByPt.set(pt, params);
        } else {
          // Keep only key=value pairs that agree across every m= section in the BUNDLE
          // group (removes the asymmetric `sprop-stereo=1;stereo=1` injected by Agora's
          // `Lee()` on only some m=audio sections).
          for (const [k, v] of Array.from(existing.entries())) {
            if (params.get(k) !== v) {
              existing.delete(k);
            }
          }
        }
        continue;
      }

      const fbMatch = /^a=rtcp-fb:(\d+)\s+(.+)$/.exec(line);
      if (fbMatch) {
        const pt = fbMatch[1];
        const fb = fbMatch[2].trim();
        let list = rtcpFbByPt.get(pt);
        if (!list) {
          list = [];
          rtcpFbByPt.set(pt, list);
        }
        if (!list.includes(fb)) {
          list.push(fb);
        }
      }
    }
  }

  // Rewrite each media section so every section declaring `a=rtpmap:<pt>` has the
  // canonical `a=fmtp:<pt>` and the full set of `a=rtcp-fb:<pt>` lines.
  for (let i = 1; i < sections.length; i++) {
    const sec = sections[i];
    const declaredPts = new Set<string>();
    for (const line of sec) {
      const rtpmapMatch = /^a=rtpmap:(\d+)\s+/.exec(line);
      if (rtpmapMatch) declaredPts.add(rtpmapMatch[1]);
    }
    if (declaredPts.size === 0) continue;

    const nextSec: string[] = [];
    const emittedFbForPt = new Set<string>();

    for (const line of sec) {
      const rtpmapMatch = /^a=rtpmap:(\d+)\s+/.exec(line);
      if (rtpmapMatch) {
        const pt = rtpmapMatch[1];
        nextSec.push(line);
        const fbs = rtcpFbByPt.get(pt);
        if (fbs && !emittedFbForPt.has(pt)) {
          emittedFbForPt.add(pt);
          for (const fb of fbs) {
            nextSec.push(`a=rtcp-fb:${pt} ${fb}`);
          }
        }
        continue;
      }

      const fbMatch = /^a=rtcp-fb:(\d+)\s+/.exec(line);
      if (fbMatch && declaredPts.has(fbMatch[1])) {
        // Already emitted immediately after `a=rtpmap:<pt>` in canonical order.
        continue;
      }

      const fmtpMatch = /^a=fmtp:(\d+)\s+/.exec(line);
      if (fmtpMatch) {
        const pt = fmtpMatch[1];
        const canonical = fmtpByPt.get(pt);
        if (canonical && canonical.size > 0) {
          nextSec.push(`a=fmtp:${pt} ${serializeFmtpParams(canonical)}`);
          continue;
        }
      }

      nextSec.push(line);
    }

    sections[i] = nextSec;
  }

  return sections.map((sec) => sec.join(eol)).join(eol);
}

let installed = false;

/**
 * Patch `RTCPeerConnection.prototype.setRemoteDescription` and `setLocalDescription`
 * once so Agora's bundled SDPs are harmonized before Chromium validates them.
 */
export function installWebRtcBundleFix(): void {
  if (installed || typeof RTCPeerConnection === 'undefined') return;
  const proto = RTCPeerConnection.prototype as RTCPeerConnection & {
    __horribleBundleFixInstalled?: boolean;
  };
  if (!proto || proto.__horribleBundleFixInstalled) {
    installed = true;
    return;
  }

  const origSetRemote = proto.setRemoteDescription as (
    this: RTCPeerConnection,
    description: RTCSessionDescriptionInit,
  ) => Promise<void>;
  if (typeof origSetRemote === 'function') {
    proto.setRemoteDescription = function (
      this: RTCPeerConnection,
      description: RTCSessionDescriptionInit,
    ): Promise<void> {
      if (description && typeof description.sdp === 'string') {
        description = {
          type: description.type,
          sdp: harmonizeBundledSdp(description.sdp),
        };
      }
      return origSetRemote.call(this, description);
    } as typeof proto.setRemoteDescription;
  }

  const origSetLocal = proto.setLocalDescription as (
    this: RTCPeerConnection,
    description?: RTCLocalSessionDescriptionInit,
  ) => Promise<void>;
  if (typeof origSetLocal === 'function') {
    proto.setLocalDescription = function (
      this: RTCPeerConnection,
      description?: RTCLocalSessionDescriptionInit,
    ): Promise<void> {
      if (description && typeof description.sdp === 'string') {
        description = {
          ...description,
          sdp: harmonizeBundledSdp(description.sdp),
        };
      }
      return origSetLocal.call(this, description);
    } as typeof proto.setLocalDescription;
  }

  proto.__horribleBundleFixInstalled = true;
  installed = true;
}
