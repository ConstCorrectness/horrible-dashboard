import { describe, expect, it } from 'vitest';

import { harmonizeBundledSdp, installWebRtcBundleFix } from '../webrtcBundleFix';

describe('harmonizeBundledSdp', () => {
  it('resolves the Agora Opus BUNDLE fmtp collision and aligns rtcp-fb across m=audio sections', () => {
    const rawSdp = [
      'v=0',
      'o=- 0 0 IN IP4 127.0.0.1',
      's=AgoraGateway',
      't=0 0',
      'a=group:BUNDLE 0 1 2',
      'm=video 9 UDP/TLS/RTP/SAVPF 96',
      'a=mid:0',
      'a=rtpmap:96 VP8/90000',
      'a=rtcp-fb:96 transport-cc',
      'm=audio 9 UDP/TLS/RTP/SAVPF 111',
      'a=mid:1',
      'a=sendonly',
      'a=rtpmap:111 opus/48000/2',
      'a=rtcp-fb:111 transport-cc',
      'a=fmtp:111 minptime=10;useinbandfec=1',
      'm=audio 9 UDP/TLS/RTP/SAVPF 111',
      'a=mid:2',
      'a=sendonly',
      'a=rtpmap:111 opus/48000/2',
      'a=fmtp:111 minptime=10;sprop-stereo=1;stereo=1;useinbandfec=1',
    ].join('\r\n');

    const fixed = harmonizeBundledSdp(rawSdp);
    const fmtpLines = fixed.split('\r\n').filter((l) => l.startsWith('a=fmtp:111'));
    expect(fmtpLines).toEqual([
      'a=fmtp:111 minptime=10;useinbandfec=1',
      'a=fmtp:111 minptime=10;useinbandfec=1',
    ]);

    const fb111Lines = fixed.split('\r\n').filter((l) => l.startsWith('a=rtcp-fb:111'));
    expect(fb111Lines).toEqual([
      'a=rtcp-fb:111 transport-cc',
      'a=rtcp-fb:111 transport-cc',
    ]);
  });

  it('resolves the collision regardless of which m=audio section had sprop-stereo=1;stereo=1 first', () => {
    const rawSdp = [
      'v=0',
      'o=- 0 0 IN IP4 127.0.0.1',
      's=AgoraGateway',
      't=0 0',
      'a=group:BUNDLE 0 1 2',
      'm=video 9 UDP/TLS/RTP/SAVPF 96',
      'a=mid:0',
      'm=audio 9 UDP/TLS/RTP/SAVPF 111',
      'a=mid:1',
      'a=sendonly',
      'a=rtpmap:111 opus/48000/2',
      'a=rtcp-fb:111 transport-cc',
      'a=fmtp:111 minptime=10;sprop-stereo=1;stereo=1;useinbandfec=1',
      'm=audio 9 UDP/TLS/RTP/SAVPF 111',
      'a=mid:2',
      'a=recvonly',
      'a=rtpmap:111 opus/48000/2',
      'a=fmtp:111 minptime=10;useinbandfec=1',
    ].join('\r\n');

    const fixed = harmonizeBundledSdp(rawSdp);
    const fmtpLines = fixed.split('\r\n').filter((l) => l.startsWith('a=fmtp:111'));
    expect(fmtpLines).toEqual([
      'a=fmtp:111 minptime=10;useinbandfec=1',
      'a=fmtp:111 minptime=10;useinbandfec=1',
    ]);
  });

  it('patches RTCPeerConnection.prototype.setRemoteDescription idempotently', async () => {
    const seen: string[] = [];
    class FakePC {
      async setRemoteDescription(desc: RTCSessionDescriptionInit): Promise<void> {
        seen.push(desc.sdp ?? '');
      }
      async setLocalDescription(desc?: RTCLocalSessionDescriptionInit): Promise<void> {
        seen.push(desc?.sdp ?? '');
      }
    }
    const prev = globalThis.RTCPeerConnection;
    globalThis.RTCPeerConnection = FakePC as unknown as typeof RTCPeerConnection;
    try {
      installWebRtcBundleFix();
      const pc = new FakePC();
      await pc.setRemoteDescription({
        type: 'answer',
        sdp: [
          'v=0',
          'a=group:BUNDLE 1 2',
          'm=audio 9 UDP/TLS/RTP/SAVPF 111',
          'a=rtpmap:111 opus/48000/2',
          'a=fmtp:111 minptime=10;useinbandfec=1',
          'm=audio 9 UDP/TLS/RTP/SAVPF 111',
          'a=rtpmap:111 opus/48000/2',
          'a=fmtp:111 minptime=10;sprop-stereo=1;stereo=1;useinbandfec=1',
        ].join('\r\n'),
      });
      expect(seen[0]).not.toContain('sprop-stereo');
    } finally {
      globalThis.RTCPeerConnection = prev;
    }
  });
});
