/**
 * The pane's address must move when the account's membership moves.
 *
 * The regression this guards: `joinClubhouseChannel()` moves the account into the
 * new room -- and out of any previous one -- the moment it returns, but
 * `activeChannel` used to be patched at the *end* of `joinRoom`, after creating
 * the Agora client, `getUserMedia`, publishing the track and subscribing PubNub.
 * A slow or failed step in that stretch left the account in the new room while the
 * pane still addressed the old one, so every send went to a room we had left.
 * Clubhouse rejects that with a bare `400 cannot send message`, which reads as a
 * broken send rather than a wrong address.
 *
 * Ordering inside one long async function is not reachable from a unit test
 * without standing up Agora and a microphone, so this reads the source -- the
 * same approach as `control-padding.test.ts`.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const source = readFileSync(
  fileURLToPath(new URL('../useClubhouseVoice.ts', import.meta.url)),
  'utf8',
);

describe('joinRoom claims the channel before the media work', () => {
  const joinCall = source.indexOf('await joinClubhouseChannel(');
  const claim = source.indexOf('session.patch({ activeChannel: channelName })');

  it('patches activeChannel exactly once, so there is one moment it moves', () => {
    const claims = source.split('session.patch({ activeChannel: channelName })').length - 1;
    expect(claims).toBe(1);
  });

  it('claims it after the upstream join returns', () => {
    expect(joinCall).toBeGreaterThan(-1);
    expect(claim).toBeGreaterThan(joinCall);
  });

  it('tears the previous room down before the upstream join', () => {
    // Switching rooms goes straight through `joinRoom` with no Leave in between, so
    // the teardown here is the only thing that stops the old room's Agora client,
    // PubNub subscription, intervals, recorder and VAD loop from outliving it. The
    // VAD loop is the one that bites: it mutates `session.sttRecorder` /
    // `session.sttChunk`, which belong to the *new* room, and discards its audio on
    // the old room's silence -- so STT silently never picks anything up again.
    //
    // Before the join, because `joinClubhouseChannel` moves the account out of the
    // old room as a side effect: a teardown after it would leave a room Clubhouse
    // had already moved us out of.
    const teardown = source.indexOf('await session.teardown()');
    expect(teardown, 'joinRoom must tear the previous room down').toBeGreaterThan(-1);
    expect(teardown).toBeLessThan(joinCall);
  });

  it('routes joins through the session queue, so two switches cannot interleave', () => {
    expect(source).toMatch(/session\.serialize\(\(\) => joinRoomInner\(/);
  });

  it('claims it before the Agora client and the PubNub subscribe', () => {
    for (const laterStep of ['AgoraRTC.createClient(', 'pubnub.subscribe(', 'session.pingInterval']) {
      const at = source.indexOf(laterStep);
      expect(at, `${laterStep} not found`).toBeGreaterThan(-1);
      expect(claim, `activeChannel must be claimed before ${laterStep}`).toBeLessThan(at);
    }
  });
});
