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

describe('stage promotion upgrades tokens and publishes only when speaker', () => {
  it('gates join-time track publishing on session.isSpeaker so listeners do not publish with a listener token', () => {
    expect(source).toContain('if (session.isSpeaker) {');
    expect(source).toContain('await within(\n          client.publish(mixedTrack),');
  });

  it('renews Agora and PubNub tokens and publishes localAudioTrack in upgradeToSpeaker', () => {
    const upgradeIdx = source.indexOf('const upgradeToSpeaker =');
    expect(upgradeIdx).toBeGreaterThan(-1);
    const upgradeSlice = source.slice(upgradeIdx);
    expect(upgradeSlice).toContain('client.renewToken(agoraToken)');
    expect(upgradeSlice).toContain('session.pubnub.setToken(pubnubToken)');
    expect(upgradeSlice).toContain('client.publish(session.localAudioTrack)');
  });

  it('handles PubNub add_speaker, make_moderator, and user_profile_id', () => {
    expect(source).toContain('msg.user_profile_id');
    expect(source).toContain("action === 'add_speaker'");
    expect(source).toContain("action === 'make_moderator'");
    expect(source).toContain("action === 'remove_speaker'");
  });

  it('determines initial speaker state from the room roster rather than pubnub_token length', () => {
    // A listener in any room where `can_post_to_chat=True` gets a 444-byte PAMv3
    // PubNub token (versus 368 bytes when chat is disabled), so checking
    // `pubnub_token.length > 400` falsely marked every listener as a speaker at join
    // time and published under a listener Agora token.
    expect(source).not.toContain('pubnub_token.length > 400');
  });

  it('preserves mute state across toggleMute, upgradeToSpeaker, syncChannelDetails, and agent TTS', () => {
    const toggleIdx = source.indexOf('const toggleMute = async () =>');
    expect(toggleIdx).toBeGreaterThan(-1);
    const toggleSlice = source.slice(toggleIdx, toggleIdx + 2200);
    // Flip local state and gain immediately so the button never gets stuck behind
    // async microphone acquisition or token renewal.
    expect(toggleSlice).toContain('session.patch({ isMuted: nextMuteState })');
    expect(toggleSlice).toContain('upgradeToSpeaker(undefined, { preserveMute: true })');
    expect(toggleSlice).toContain('connectPhysicalMicrophone(5000)');

    // `syncChannelDetails` only promotes when transitioning from listener to speaker,
    // not every 10s whenever `isTrackPublished` is false.
    const syncIdx = source.indexOf('const syncChannelDetails =');
    expect(syncIdx).toBeGreaterThan(-1);
    const syncSlice = source.slice(syncIdx, syncIdx + 600);
    expect(syncSlice).toContain('if (meInRoom.is_speaker && !session.isSpeaker)');
    expect(syncSlice).not.toContain('!session.isTrackPublished');

    // `playAgentAudio` reads `session.state.isMuted` directly and only re-mutes if the
    // operator is still muted when TTS finishes.
    const agentAudioIdx = source.indexOf('const playAgentAudio =');
    const stopMusicIdx = source.indexOf('const stopRoomMusic =');
    expect(agentAudioIdx).toBeGreaterThan(-1);
    expect(stopMusicIdx).toBeGreaterThan(agentAudioIdx);
    const agentAudioSlice = source.slice(agentAudioIdx, stopMusicIdx);
    expect(agentAudioSlice).toContain('const wasMuted = session.state.isMuted');
    expect(agentAudioSlice).toContain('wasMuted &&');
    expect(agentAudioSlice).toContain('session.state.isMuted &&');
  });
});
