import { apiUrl } from '../../origin';
import { useCallback, useRef, useSyncExternalStore } from 'react';
import AgoraRTC from 'agora-rtc-sdk-ng';

import PubNub from 'pubnub';

import { usePaneSession } from '../../layout/use-pane-session';
import { mixer } from '../audio/engine';
import { inputConstraints } from '../audio/store';
import { splitForSpeech } from './speechChunks';
import {
  ClubhouseRoomSession,
  createRoomSession,
  disposeRoomSession,
  type ChatComment,
  type FloatingReaction,
  type LiveUserState,
  type SpeakerInvite,
} from './roomSession';

export type { ChatComment, FloatingReaction, LiveUserState, SpeakerInvite };
import {
  joinClubhouseChannel,
  pingClubhouseChannel,
  muteClubhouseChannel,
  setClubhouseHand,
  acceptClubhouseSpeaker,
  getClubhouseStatus,
  getClubhouseChannelChat,
  sendChannelMessage,
  JoinChannelResult,
  ChannelUser,
  ApiChatComment,
} from './api';

const CLUBCARD_AGORA_APP_ID = '938d7e95aeaa4f4ca1f416ab40a498d9';
const CLUBCARD_PUBNUB_SUB_KEY = 'sub-c-a4abea84-9ca3-11ea-8e71-f2b83ac9263d';
const CLUBCARD_PUBNUB_PUB_KEY = 'pub-c-6878d382-5ae6-4494-9099-f930f938868b';

export interface UseClubhouseVoiceProps {
  onLiveUsersChange?: (users: LiveUserState[]) => void;
  onCommentsChange?: (comments: ChatComment[]) => void;
  onSpeakingVolumesChange?: (volumes: Record<number, number>) => void;
  /**
   * One utterance heard in the room. `partial` means the speaker is *still talking* —
   * only the `interject` posture acts on one, and the finished utterance follows.
   */
  onTranscribe?: (
    text: string,
    speakerName?: string,
    speakerId?: number | null,
    partial?: boolean,
  ) => void;
  onBargeIn?: () => void;
  onSpeakerInvite?: (invite: SpeakerInvite) => void;
  onHandRaise?: (userId: number, userName: string) => void;
  /** Speech pipeline failed (missing `voice` extra, backend down, decode error). */
  onVoiceError?: (message: string) => void;
  sttChunkIntervalMs?: number;
  endpointingDelayMs?: number;
  allowBargeIn?: boolean;
  /**
   * Flush a partial transcript once one person has held the floor this long, so the
   * agent can cut in. `0` disables it — which is every posture but `interject`.
   */
  interjectAfterMs?: number;
}

export interface PubNubRoomMessage {
  action?: string;
  // Chat
  text?: string;
  body?: string;
  message?: string;
  // Reaction
  emoji?: string;
  reaction?: string;
  // Sender identity
  user_profile?: {
    name?: string | null;
    photo_url?: string | null;
    user_id?: number | null;
  } | null;
  user?: {
    name?: string | null;
    photo_url?: string | null;
    user_id?: number | null;
  } | null;
  from_name?: string | null;
  from_photo_url?: string | null;
  from_user_id?: number | null;
  // Room event payloads
  user_id?: number | null;
  club_id?: number | null;
  is_speaker?: boolean | null;
  is_moderator?: boolean | null;
  is_muted?: boolean | null;
  raise_hands?: boolean | null;
  // Speaker invite
  moderator_id?: number | null;
  moderator_name?: string | null;
  moderator_photo_url?: string | null;
}

/**
 * The room used when the hook runs outside a pane (no instance id in context —
 * a test, or a component rendered outside the frame). One module-level session
 * rather than one per call, so the behaviour still matches "the room outlives the
 * component"; it simply has no close event to be disposed by.
 */
let orphanSession: ClubhouseRoomSession | null = null;
function fallbackSession(): ClubhouseRoomSession {
  orphanSession ??= createRoomSession();
  return orphanSession;
}

export function useClubhouseVoice(props?: UseClubhouseVoiceProps) {
  /**
   * The pane's room. Created once per pane and disposed only when the pane really
   * closes — NOT on unmount. A tab switch, a split, or a workspace change unmounts
   * this component while the call keeps running; the old unmount cleanup called
   * `leaveClubhouseChannel()` on every one of those, so the whole room watched you
   * leave because you looked at another tab. See `layout/pane-lifetime`.
   *
   * Outside a pane (no instance id in context) there is no session to own the room,
   * so the hook degrades to a local one that lives as long as the module does.
   */
  const paneScoped = usePaneSession<ClubhouseRoomSession>(createRoomSession, disposeRoomSession);
  const session = paneScoped ?? fallbackSession();

  // State lives on the session, not in `useState`: it has to survive the same
  // unmounts the connection does, or a remount renders an empty room on top of a
  // live call — no participants, no chat backlog, mute state reset.
  const state = useSyncExternalStore(session.subscribe.bind(session), session.getState);
  const {
    joined,
    activeChannel,
    isMuted,
    handRaised,
    comments,
    activeReactions,
    error,
    loading,
    liveUsers,
    speakerInvite,
    speakingVolumes,
    voiceError,
    chatDisabledReason,
  } = state;

  const propsRef = useRef(props);
  propsRef.current = props;

  // Callbacks are read through the session so the long-lived connection always
  // calls the *current* mount's handlers rather than the ones from the render that
  // opened the room.
  session.handlers = {
    onTranscribe: props?.onTranscribe,
    onBargeIn: props?.onBargeIn,
    onSpeakerInvite: props?.onSpeakerInvite,
    onHandRaise: props?.onHandRaise,
    onVoiceError: props?.onVoiceError,
  };
  session.chunkIntervalMs = props?.sttChunkIntervalMs || 5000;

  const reportVoiceError = useCallback(
    (message: string) => session.reportVoiceError(message, session.handlers.onVoiceError),
    [session],
  );

  // Helper: update a single user's live state
  const updateLiveUser = useCallback(
    (userId: number, patch: Partial<Omit<LiveUserState, 'userId'>>) => {
      session.update('liveUsers', (prev) => {
        const existing = prev.find((u) => u.userId === userId);
        if (existing) {
          return prev.map((u) => (u.userId === userId ? { ...u, ...patch } : u));
        }
        // New user — add with defaults
        return [...prev, { userId, handRaised: false, isSpeaker: false, isMuted: false, ...patch }];
      });
    },
    [],
  );

  // Helper: remove a user from live state
  const removeLiveUser = useCallback((userId: number) => {
    session.update('liveUsers', (prev) => prev.filter((u) => u.userId !== userId));
  }, []);

  // Seed liveUsers from the initial room user list after joining
  const seedLiveUsers = useCallback((users: ChannelUser[]) => {
    const states: LiveUserState[] = users
      .filter((u) => u.user_id != null)
      .map((u) => ({
        userId: u.user_id!,
        handRaised: false,
        isSpeaker: u.is_speaker ?? false,
        isMuted: false,
      }));
    session.set('liveUsers', states);
  }, []);

  // 1. Join voice room
  const joinRoom = (channelName: string, initialUsers?: ChannelUser[]) =>
    session.serialize(() => joinRoomInner(channelName, initialUsers));

  const joinRoomInner = async (channelName: string, initialUsers?: ChannelUser[]) => {
    // Leave whatever we are in *first*. Switching rooms goes straight through
    // `joinRoom` -- there is no Leave in between -- so without this the previous
    // room's Agora client, PubNub subscription, ping/volume intervals, recorder and
    // VAD loop all keep running. The VAD loop is the damaging one: it mutates
    // `session.sttRecorder`/`session.sttChunk`, which now belong to the room you
    // just joined, and its idle branch fires on the silence of the room you left --
    // discarding the new room's audio every ~1.5s, forever, one extra ghost per
    // switch. The symptom is an STT panel that never picks anything up.
    //
    // Before the join, not after: `joinClubhouseChannel` moves the account out of
    // the old room as a side effect, so a teardown afterwards would be telling
    // Clubhouse to leave a room it had already moved us out of.
    await session.teardown();
    session.patch({ loading: true });
    session.patch({ error: null });
    session.set('comments', []);
    session.set('activeReactions', []);

    // Fetch historical chat
    try {
      const chatRes = await fetch(apiUrl(`/api/clubhouse/channels/${channelName}/chat`));
      if (chatRes.ok) {
        const chatData = await chatRes.json();
        if (chatData.comments && Array.isArray(chatData.comments)) {
          const newComments = chatData.comments
            .map((c: ApiChatComment) => ({
              id: c.time_created || Math.random().toString(),
              userName: c.from_name || c.user_profile?.name || 'Unknown',
              userPhoto: c.from_photo_url || c.user_profile?.photo_url || null,
              text: c.message || c.text || '',
              timestamp: c.time_created ? Date.parse(c.time_created) : Date.now(),
            }))
            .filter((c: ChatComment) => c.text.length > 0);
          session.set('comments', newComments.reverse()); // usually oldest first
        }
      }
    } catch (e) {
      console.error('Failed to fetch historical chat:', e);
    }

    try {
      // Get own profile status first
      const status = await getClubhouseStatus();
      session.myProfile = {
        name: status.name || 'Anonymous',
        photoUrl: status.photo_url,
        userId: status.user_id,
      };

      // a. Request credentials & tokens from our backend
      const chDetails: JoinChannelResult = await joinClubhouseChannel(channelName);
      if (!chDetails.token) {
        throw new Error('Failed to retrieve Agora token from Clubhouse');
      }

      // The upstream join has already moved the account into this room -- and
      // out of any previous one -- so the pane's address must move with it,
      // *before* the Agora and PubNub work below. Setting it at the end of the
      // join instead left a window (a slow `getUserMedia`, a failed Agora join)
      // where the account was in the new room while `activeChannel` still named
      // the old one: every send then went to a room we had left, which Clubhouse
      // rejects with a bare `cannot send message`. `joined` stays below, since
      // that gates the room UI on a working connection rather than on membership.
      session.patch({ activeChannel: channelName });

      // Chat is per-room *and* per-account, and Clubhouse's refusal is a bare
      // "cannot send message", so record its verdict now. Only an explicit
      // `false` counts: an absent field is an older room, not a closed one.
      const roomChatOff =
        chDetails.is_chat_enabled === false || chDetails.is_room_chat_available === false;
      const cannotPost = chDetails.user_capabilities?.can_post_to_chat === false;
      session.patch({
        chatDisabledReason: roomChatOff
          ? 'Chat is turned off in this room'
          : cannotPost
            ? 'You cannot post to chat in this room'
            : null,
      });

      // Initialize Audio Mixer & Routing Destinations FIRST so remote tracks and recorder can bind immediately
      const audioCtx = mixer.getContext();
      session.audioCtx = audioCtx;

      // An AudioContext created without a user gesture starts `suspended` under every
      // autoplay policy. Resuming is a no-op when already running.
      if (audioCtx.state === 'suspended') {
        try {
          await audioCtx.resume();
        } catch (err) {
          console.warn('AudioContext could not be resumed; audio will be silent:', err);
        }
      }

      const dest = audioCtx.createMediaStreamDestination();
      session.agentAudioDest = dest;

      const sttDest = audioCtx.createMediaStreamDestination();
      session.sttDest = sttDest;

      // b. Initialize Agora Client
      const client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
      session.rtcClient = client;

      // Helper to route remote audio stream track to agent's STT
      const connectRemoteAudioTrackToStt = (track: MediaStreamTrack) => {
        try {
          const stream = new MediaStream([track]);
          const source = audioCtx.createMediaStreamSource(stream);
          source.connect(sttDest);
        } catch (e) {
          console.warn('Failed to route remote audio track to STT destination:', e);
        }
      };

      // c. Listen for remote audio updates
      client.on('user-published', async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === 'audio') {
          const remoteAudioTrack = user.audioTrack;
          remoteAudioTrack?.play();

          if (remoteAudioTrack) {
            connectRemoteAudioTrackToStt(remoteAudioTrack.getMediaStreamTrack());
          }
        }
      });

      client.on('user-unpublished', (user) => {
        user.audioTrack?.stop();
      });

      // d. Join the Agora stream
      await client.join(
        CLUBCARD_AGORA_APP_ID,
        channelName,
        chDetails.token,
        chDetails.user_id ?? undefined,
      );

      // Connect any remote audio tracks from users already in the room
      for (const remoteUser of client.remoteUsers) {
        if (remoteUser.hasAudio && remoteUser.audioTrack) {
          connectRemoteAudioTrackToStt(remoteUser.audioTrack.getMediaStreamTrack());
        }
      }

      // Start STT Recorder with VAD
      try {
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        sttDest.stream.getAudioTracks().forEach((track) => {
          const stream = new MediaStream([track]);
          const src = audioCtx.createMediaStreamSource(stream);
          src.connect(analyser);
        });

        let silenceTicks = 0;
        let isSpeaking = false;
        let idleSilenceTicks = 0;
        let hasSpeechInChunk = false;
        let bargeInTicks = 0;
        let activeSpeakerUidDuringSpeech: number | null = null;
        /** When the current unbroken run of speech began, for the interject timer. */
        let speechStartedAt = 0;
        /** One interjection per run of speech — otherwise it fires every tick. */
        let interjectedThisRun = false;
        /**
         * Text transcribed so far during the current run of speech.
         *
         * A partial flush has to *stop* the recorder, because each blob is a complete
         * WebM and a fragment without its header decodes to nothing. That leaves every
         * chunk after the first holding only the tail, so the pieces are concatenated
         * here: it is what lets the finished utterance still be the whole sentence
         * after the agent has already cut in on the first half of it.
         */
        let runText = '';

        const getOptimalMimeType = (): string | undefined => {
          if (typeof MediaRecorder === 'undefined') return undefined;
          const candidates = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/mp4',
          ];
          for (const mime of candidates) {
            if (MediaRecorder.isTypeSupported(mime)) return mime;
          }
          return undefined;
        };

        const startRecordingChunk = (speakerUidForChunk: number | null = null) => {
          if (!session.sttDest) return;
          const mime = getOptimalMimeType();
          const recorder = mime
            ? new MediaRecorder(session.sttDest.stream, { mimeType: mime })
            : new MediaRecorder(session.sttDest.stream);
          session.sttRecorder = recorder;
          // Read at `ondataavailable` time, not now: whether a chunk is a partial is
          // decided when it is flushed, which is always after the recorder was made.
          const chunk: { partial: boolean; discard?: boolean } = { partial: false };
          session.sttChunk = chunk;
          const boundSpeakerId = speakerUidForChunk ?? activeSpeakerUidDuringSpeech;

          recorder.ondataavailable = async (e) => {
            if (chunk.discard) {
              return;
            }
            if (e.data.size > 0 && session.handlers.onTranscribe) {
              const formData = new FormData();
              formData.append(
                'file',
                new File([e.data], 'chunk.webm', { type: e.data.type || 'audio/webm' }),
              );
              try {
                const res = await fetch(apiUrl('/api/agent/stt'), {
                  method: 'POST',
                  body: formData,
                });
                // A non-2xx here is the single most common "the agent doesn't hear
                // me" cause: /api/agent/stt answers 503 until `uv sync --extra voice`
                // has been run.
                if (!res.ok) {
                  let detail = `HTTP ${res.status}`;
                  try {
                    detail = (await res.json()).detail || detail;
                  } catch {
                    /* keep the status */
                  }
                  reportVoiceError(`Speech-to-text unavailable: ${detail}`);
                  return;
                }
                session.clearVoiceError();
                const json = await res.json();
                const piece = json.text && json.text.trim();
                if (session.handlers.onTranscribe && piece) {
                  runText = runText ? `${runText} ${piece}` : piece;
                  const whole = runText;
                  // The run ends with the final chunk; a partial leaves it open so the
                  // next piece appends rather than starting a new utterance.
                  if (!chunk.partial) runText = '';
                  session.handlers.onTranscribe(whole, undefined, boundSpeakerId, chunk.partial);
                }
              } catch (err) {
                console.error('STT failed:', err);
                reportVoiceError(
                  `Speech-to-text failed: ${err instanceof Error ? err.message : String(err)}`,
                );
              }
            }
          };

          recorder.start();
        };

        /**
         * End the current chunk and start the next. `partial` marks the flushed chunk
         * as mid-sentence, which is the only thing that distinguishes an interjection
         * from an ordinary end-of-speech turn by the time it reaches the server.
         */
        const flushChunk = (partial: boolean, speakerUid: number | null) => {
          if (!session.sttRecorder || session.sttRecorder.state === 'inactive') return;
          if (session.sttChunk) session.sttChunk.partial = partial;
          hasSpeechInChunk = false;
          idleSilenceTicks = 0;
          session.sttRecorder.stop();
          startRecordingChunk(speakerUid);
        };

        startRecordingChunk();

        session.vadInterval = setInterval(() => {
          const dataArray = new Uint8Array(analyser.frequencyBinCount);
          analyser.getByteFrequencyData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
          const avgVolume = sum / dataArray.length;

          // Barge-in: Stop agent if it's currently speaking and human is speaking loudly and clearly
          if (session.isAgentSpeaking) {
            if (avgVolume > 22) {
              bargeInTicks++;
              if (bargeInTicks >= 3 && propsRef.current?.allowBargeIn !== false) {
                console.log('BARGE-IN DETECTED! Stopping agent audio.');
                if (session.agentAudioSource) {
                  try {
                    session.agentAudioSource.stop();
                  } catch {
                    /* ignore */
                  }
                }
                if (session.agentTtsAbort) {
                  try {
                    session.agentTtsAbort.abort();
                  } catch {
                    /* ignore */
                  }
                }
                session.isAgentSpeaking = false;
                bargeInTicks = 0;
              }
            } else {
              bargeInTicks = 0;
            }
          } else {
            bargeInTicks = 0;
          }

          if (avgVolume > 12) {
            hasSpeechInChunk = true;
            idleSilenceTicks = 0;

            // Track loudest speaker UID across live volumes
            const vols = session.getState().speakingVolumes || {};
            let highestVol = 0;
            let loudestUid: number | null = null;
            for (const [uidStr, vol] of Object.entries(vols)) {
              const v = Number(vol);
              if (v > highestVol && v > 5) {
                highestVol = v;
                loudestUid = Number(uidStr);
              }
            }
            if (loudestUid != null) {
              activeSpeakerUidDuringSpeech = loudestUid;
            }

            // Human is speaking
            if (!isSpeaking) {
              session.handlers.onBargeIn?.();
              speechStartedAt = Date.now();
              interjectedThisRun = false;
            }
            isSpeaking = true;
            silenceTicks = 0;

            // The agent cutting in
            const interjectAfterMs = propsRef.current?.interjectAfterMs ?? 0;
            if (
              interjectAfterMs > 0 &&
              !interjectedThisRun &&
              !session.isAgentSpeaking &&
              Date.now() - speechStartedAt >= interjectAfterMs
            ) {
              interjectedThisRun = true;
              flushChunk(true, activeSpeakerUidDuringSpeech);
            }
          } else {
            // Volume below threshold
            if (isSpeaking) {
              silenceTicks++;
              const requiredSilenceTicks = Math.max(
                4,
                Math.round((propsRef.current?.endpointingDelayMs || 750) / 50),
              );
              if (silenceTicks >= requiredSilenceTicks) {
                isSpeaking = false;
                silenceTicks = 0;
                const completedSpeakerUid = activeSpeakerUidDuringSpeech;
                activeSpeakerUidDuringSpeech = null;
                // End of speech detected, send chunk!
                flushChunk(false, completedSpeakerUid);
              }
            } else {
              // Idle silence without active speech
              idleSilenceTicks++;
              // Every ~1.5s of unbroken silence without speech in this chunk, rotate and discard
              // to prevent accumulating massive silence buffers that dilute STT RMS energy.
              if (!hasSpeechInChunk && idleSilenceTicks >= 30) {
                idleSilenceTicks = 0;
                if (session.sttRecorder && session.sttRecorder.state !== 'inactive') {
                  if (session.sttChunk) session.sttChunk.discard = true;
                  session.sttRecorder.stop();
                  startRecordingChunk();
                }
              }
            }
          }
        }, 50);
      } catch (err) {
        console.error('Failed to start VAD STT recorder:', err);
      }

      // Get physical mic (Optional, handle missing permissions or timeouts gracefully)
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          throw new Error('MediaDevices API not available (requires secure context).');
        }

        const micStream = await Promise.race([
          // Through the mixer so the chosen microphone is honoured
          navigator.mediaDevices.getUserMedia({ audio: inputConstraints() }),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('Microphone permission timeout')), 3000),
          ),
        ]);

        session.physicalMicStream = micStream;
        const micSource = audioCtx.createMediaStreamSource(micStream);

        const humanGain = audioCtx.createGain();
        humanGain.gain.value = 0; // muted by default for Clubhouse room
        session.humanGain = humanGain;

        micSource.connect(humanGain);
        humanGain.connect(dest); // published to Clubhouse room when unmuted
        micSource.connect(sttDest); // agent's ears always hear operator
      } catch (err) {
        // Continuing as a listener is a legitimate outcome, but it must be *said*.
        // Without `humanGain` there is nothing between a microphone and the
        // published track, so `toggleMute` below flips the label to "Mic Active"
        // and tells Clubhouse we are unmuted while the room hears silence --
        // which reads as a broken microphone rather than a denied permission.
        console.warn('Could not access physical microphone, continuing as listener:', err);
        reportVoiceError(
          `Microphone unavailable — you are connected as a listener: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }

      // Create and publish mixed microphone stream
      const mixedTrack = AgoraRTC.createCustomAudioTrack({
        mediaStreamTrack: dest.stream.getAudioTracks()[0],
      });
      session.localAudioTrack = mixedTrack;
      await client.publish(mixedTrack);
      session.patch({ isMuted: true });

      // e2. Start Agora volume indicator — fires every 200ms with per-user volumes
      client.enableAudioVolumeIndicator();
      client.on('volume-indicator', (volumes) => {
        const map: Record<number, number> = {};
        for (const { uid, level } of volumes) {
          map[uid as number] = level;
        }
        session.set('speakingVolumes', map);
      });
      // Clear stale volumes every 600ms in case no volume event fires
      session.volumeInterval = setInterval(() => {
        session.update('speakingVolumes', (prev) => {
          const now: Record<number, number> = {};
          for (const [uid, vol] of Object.entries(prev)) {
            if (vol > 5) now[Number(uid)] = vol;
          }
          return now;
        });
      }, 600);

      // f. Configure PubNub signaling
      if (chDetails.pubnub_enable && chDetails.pubnub_token) {
        const myUserId = session.myProfile?.userId;
        const myUserIdStr = myUserId
          ? String(myUserId)
          : `anon-${Math.random().toString(36).slice(2, 9)}`;

        // The pubnub_token is a PAMv3 CBOR-encoded access token — use setToken(), not authKey
        const pubnubConfig: ConstructorParameters<typeof PubNub>[0] = {
          subscribeKey: CLUBCARD_PUBNUB_SUB_KEY,
          publishKey: CLUBCARD_PUBNUB_PUB_KEY,
          userId: myUserIdStr,
        };
        if (chDetails.pubnub_origin) {
          (pubnubConfig as Record<string, unknown>).origin = chDetails.pubnub_origin;
        }

        const pubnub = new PubNub(pubnubConfig);
        pubnub.setToken(chDetails.pubnub_token);
        session.pubnub = pubnub;

        pubnub.addListener({
          message: (event) => {
            console.log('PubNub Message Received:', event);
            const msg = event.message as PubNubRoomMessage;
            if (!msg) return;

            const sender = msg.user_profile || msg.user || {};
            const senderId = sender.user_id ?? msg.user_id ?? msg.from_user_id;

            // --- Room signaling events ---
            const action = msg.action;

            if (action === 'join_channel') {
              if (senderId != null) {
                updateLiveUser(senderId, {
                  isSpeaker: msg.is_speaker ?? false,
                  isMuted: false,
                });
              }
            } else if (action === 'leave_channel' || action === 'remove_speaker') {
              if (senderId != null) {
                removeLiveUser(senderId);
              }
            } else if (action === 'raise_hands' || action === 'hand_raised') {
              if (senderId != null) {
                updateLiveUser(senderId, { handRaised: true });
                if (session.handlers.onHandRaise && senderId !== myUserId) {
                  session.handlers.onHandRaise(senderId, sender.name || msg.from_name || 'Someone');
                }
              }
            } else if (action === 'lower_hands' || action === 'unraise_hands') {
              if (senderId != null) {
                updateLiveUser(senderId, { handRaised: false });
              }
            } else if (action === 'make_speaker' || action === 'accept_speaker_invite') {
              if (senderId != null) {
                updateLiveUser(senderId, { isSpeaker: true, handRaised: false });
              }
            } else if (action === 'mute_speaker' || action === 'update_muted') {
              if (senderId != null) {
                updateLiveUser(senderId, { isMuted: msg.is_muted ?? true });
              }
            } else if (action === 'invite_speaker') {
              const targetId = msg.user_id;
              if (targetId != null && Number(targetId) === Number(myUserId)) {
                const invite: SpeakerInvite = {
                  moderatorId: msg.moderator_id ?? senderId ?? 0,
                  moderatorName: msg.moderator_name || sender.name || 'A moderator',
                  moderatorPhoto: msg.moderator_photo_url || sender.photo_url || null,
                };
                session.patch({ speakerInvite: invite });
                if (session.handlers.onSpeakerInvite) {
                  session.handlers.onSpeakerInvite(invite);
                }
              }
            }

            // --- Chat messages ---
            if (
              action === 'chat_message' ||
              action === 'chat' ||
              action === 'post_to_chat' ||
              action === 'new_channel_message' ||
              (!action && (msg.text || msg.body || msg.message))
            ) {
              const text = msg.text || msg.body || msg.message;
              if (text && typeof text === 'string') {
                session.update('comments', (prev) => [
                  ...prev,
                  {
                    id: String(event.timetoken || Math.random()),
                    userName: sender.name || msg.from_name || 'Anonymous',
                    userPhoto: sender.photo_url || msg.from_photo_url || null,
                    text,
                    timestamp: Math.floor((Number(event.timetoken) || Date.now() * 10000) / 10000),
                  },
                ]);
              }
            }

            // --- Emoji reactions ---
            if (action === 'react' || (!action && (msg.emoji || msg.reaction))) {
              const emoji = msg.emoji || msg.reaction;
              if (emoji && typeof emoji === 'string') {
                if (senderId != null && myUserId != null && Number(senderId) === Number(myUserId))
                  return;
                const reactionId = String(event.timetoken || Math.random());
                const x = 15 + Math.random() * 70;
                const y = 80 + Math.random() * 10;
                session.update('activeReactions', (prev) => [
                  ...prev,
                  { id: reactionId, emoji, x, y },
                ]);
                setTimeout(() => {
                  session.update('activeReactions', (prev) =>
                    prev.filter((r) => r.id !== reactionId),
                  );
                }, 3000);
              }
            }
          },
        });

        const channelsToSubscribe = [
          `channel_all.${channelName}`,
          `channel_user.${channelName}.${myUserId}`,
          `users.${myUserId}`,
        ];
        pubnub.subscribe({ channels: channelsToSubscribe });
      }

      // g. Heartbeat ping loop (every 30s)
      void pingClubhouseChannel(channelName).catch((err) => {
        console.error('Initial heartbeat ping failed:', err);
      });
      session.pingInterval = setInterval(async () => {
        try {
          await pingClubhouseChannel(channelName);
        } catch (err) {
          console.error('Heartbeat ping failed:', err);
        }
      }, 30000);

      session.patch({ joined: true });
      session.patch({ handRaised: false });
      session.set('comments', []);
      session.set('activeReactions', []);
      session.patch({ speakerInvite: null });
      session.set('speakingVolumes', {});

      if (initialUsers) {
        seedLiveUsers(initialUsers);
      }

      try {
        const chatRes = await getClubhouseChannelChat(channelName);
        if (chatRes.comments && chatRes.comments.length > 0) {
          const mappedComments: ChatComment[] = chatRes.comments
            .map((msg) => ({
              id: String(msg.message_id || msg.time_created || Math.random()),
              userName: String(msg.user_profile?.name || msg.from_name || 'Anonymous'),
              userPhoto: msg.user_profile?.photo_url || msg.from_photo_url || null,
              text: String(msg.message || msg.text || msg.body || ''),
              timestamp: msg.time_created ? new Date(msg.time_created).getTime() : Date.now(),
            }))
            .filter((c: ChatComment) => c.text);
          // Only take last 50 messages to prevent huge lists
          session.set('comments', mappedComments.slice(-50));
        }
      } catch (err) {
        console.warn('Failed to fetch initial chat history:', err);
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      session.patch({ error: errMsg });
      console.error('Join room failed:', e);
      if (session.rtcClient || session.localAudioTrack) {
        await leaveRoom(channelName);
      }
    } finally {
      session.patch({ loading: false });
    }
  };

  // 2. Leave voice room & cleanup
  /**
   * The user's explicit Leave. Delegates to the session's teardown, which is the
   * same path `pane-lifetime` runs on a real close — one implementation, so the two
   * cannot drift.
   *
   * The channel argument callers still pass is accepted and ignored: the session
   * knows which room it is in. Taking it from the caller is how the old code left
   * twice — the unmount cleanup fired again with a stale closure over the channel it
   * had already left.
   */
  const leaveRoom = async (...ignoredChannelName: unknown[]) => {
    void ignoredChannelName;
    // Through the same queue as `joinRoom`: a Leave clicked while a switch is still
    // building would otherwise tear down the half-built connection and let the rest
    // of the build install itself afterwards, leaving the very orphans both paths
    // exist to prevent.
    await session.serialize(async () => {
      session.patch({ loading: true });
      await session.teardown();
    });
  };

  // 3. Mute/Unmute microphone
  const toggleMute = async () => {
    if (!activeChannel) return;
    const nextMuteState = !isMuted;
    // No `humanGain` means the microphone never opened, so there is nothing to
    // unmute. Reporting `isMuted: false` anyway is the silent failure: the button
    // reads "Mic Active" and Clubhouse shows us unmuted to the room while not one
    // sample reaches the published track.
    if (!session.humanGain) {
      reportVoiceError('No microphone is connected to this room — nothing to unmute.');
      return;
    }
    try {
      session.humanGain.gain.value = nextMuteState ? 0 : 1;
      session.patch({ isMuted: nextMuteState });
      await muteClubhouseChannel(activeChannel, nextMuteState);
    } catch (err) {
      console.error('Failed to toggle mic state:', err);
    }
  };

  // 4. Raise/Lower hand
  const raiseHand = async (raised: boolean) => {
    if (!activeChannel) return;
    session.patch({ loading: true });
    try {
      await setClubhouseHand(activeChannel, raised);
      session.patch({ handRaised: raised });
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      session.patch({ error: errMsg });
      console.error('Failed to update hand raise state:', err);
    } finally {
      session.patch({ loading: false });
    }
  };

  // 5. Accept speaker invitation
  const acceptSpeakerInvite = async () => {
    if (!activeChannel) return;
    session.patch({ loading: true });
    try {
      await acceptClubhouseSpeaker(activeChannel);
      session.patch({ handRaised: false });
      session.patch({ speakerInvite: null });
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      session.patch({ error: errMsg });
      console.error('Failed to accept speaker invite:', err);
    } finally {
      session.patch({ loading: false });
    }
  };

  // 6. Dismiss speaker invite
  const dismissSpeakerInvite = () => session.patch({ speakerInvite: null });

  // 7. Post a comment chat message to the room
  const sendComment = async (text: string) => {
    if (!session.pubnub || !activeChannel || !text) return;

    // Clubhouse chat messages have a 280-character limit
    const MAX_LEN = 270;
    const chunks: string[] = [];
    let current = text;
    while (current.length > 0) {
      if (current.length <= MAX_LEN) {
        chunks.push(current);
        break;
      }
      let splitIdx = current.lastIndexOf(' ', MAX_LEN);
      if (splitIdx === -1) splitIdx = MAX_LEN;
      chunks.push(current.substring(0, splitIdx));
      current = current.substring(splitIdx).trim();
    }

    try {
      for (const chunk of chunks) {
        await sendChannelMessage(activeChannel, chunk);
        // Add a tiny delay between chunks so they appear in order
        await new Promise((r) => setTimeout(r, 300));
      }
    } catch (err2) {
      console.error('Failed to publish comment:', err2);
      throw err2;
    }
  };

  // 8. Send an emoji reaction
  const sendReaction = async (emoji: string) => {
    if (!session.pubnub || !activeChannel) return;
    const profile = session.myProfile;
    const reactionId = 'my-react-' + Math.random().toString(36).slice(2, 9);
    const x = 15 + Math.random() * 70;
    const y = 80 + Math.random() * 10;

    session.update('activeReactions', (prev) => [...prev, { id: reactionId, emoji, x, y }]);
    setTimeout(() => {
      session.update('activeReactions', (prev) => prev.filter((r) => r.id !== reactionId));
    }, 3000);

    const payload = {
      action: 'react',
      emoji,
      user_profile: {
        name: profile?.name || 'Anonymous',
        photo_url: profile?.photoUrl || null,
        user_id: profile?.userId || null,
      },
      timestamp: Date.now(),
    };
    try {
      await session.pubnub.publish({
        channel: `channel_all.${activeChannel}`,
        message: payload,
      });
    } catch (err2) {
      console.error('Failed to publish reaction:', err2);
    }
  };

  // Play Agent Audio through the mixer
  const stopAgentAudio = useCallback(() => {
    if (session.isAgentSpeaking) {
      console.log('Interrupting agent audio manually.');
      if (session.agentAudioSource) {
        try {
          session.agentAudioSource.stop();
        } catch {
          /* ignore */
        }
      }
      if (session.agentTtsAbort) {
        try {
          session.agentTtsAbort.abort();
        } catch {
          /* ignore */
        }
      }
      session.isAgentSpeaking = false;
    }
  }, []);

  /**
   * Speak a reply into the room, starting as soon as the *first* chunk is ready.
   *
   * The old implementation awaited `res.arrayBuffer()` for the whole utterance and
   * then decoded it, so nothing played until every sentence had been synthesized and
   * transferred — a cost paid in full regardless of how fast the synthesizer is.
   * Measured against the current Edge TTS backend, a 69-word reply took ~690 ms warm
   * (~2.5 s cold) before any sound, while first audio was ready in ~275 ms and stayed
   * flat in output length.
   *
   * So the reply is split (`splitForSpeech`) and each chunk is fetched **one ahead**
   * of the one playing: the listener waits only for the first, and every later
   * synthesis hides behind the audio already playing. Barge-in then stops at a chunk
   * boundary and drops the queue, instead of killing one monolithic buffer.
   */
  const playAgentAudio = useCallback(
    async (
      text: string,
      voiceOptions?: { voice?: string; rate?: string; pitch?: string; volume?: string },
    ) => {
      const ctx = session.audioCtx;
      if (!ctx) return;
      const chunks = splitForSpeech(text);
      if (chunks.length === 0) return;

      const abort = new AbortController();
      session.agentTtsAbort = abort;
      session.isAgentSpeaking = true;

      const voice = voiceOptions?.voice || 'en-US-ChristopherNeural';
      const rate = voiceOptions?.rate || '+0%';
      const pitch = voiceOptions?.pitch || '+0Hz';
      const volume = voiceOptions?.volume || '+0%';

      /** Fetch + decode one chunk. Returns null if it was aborted or unavailable. */
      const render = async (chunk: string): Promise<AudioBuffer | null> => {
        const url = apiUrl(
          `/api/agent/tts?text=${encodeURIComponent(chunk)}&voice=${encodeURIComponent(voice)}&rate=${encodeURIComponent(rate)}&pitch=${encodeURIComponent(pitch)}&volume=${encodeURIComponent(volume)}`,
        );
        const res = await fetch(url, { signal: abort.signal });
        if (!res.ok) {
          let detail = `HTTP ${res.status}`;
          try {
            detail = (await res.json()).detail || detail;
          } catch {
            /* keep the status */
          }
          reportVoiceError(`Text-to-speech unavailable: ${detail}`);
          return null;
        }
        // `decodeAudioData` detaches the buffer, so each chunk is decoded exactly once.
        return ctx.decodeAudioData(await res.arrayBuffer());
      };

      // Unmuted once around the whole utterance, not per chunk: toggling the channel
      // between sentences would clip the start of each one and spam the API.
      const wasMuted = isMuted;
      if (wasMuted && activeChannel) {
        try {
          await muteClubhouseChannel(activeChannel, false);
        } catch (e) {
          console.error('Failed to unmute channel for agent TTS:', e);
        }
      }

      try {
        // One chunk in flight ahead of the one playing. More lookahead would not
        // start the reply any sooner and only widens what a barge-in wastes.
        let pending: Promise<AudioBuffer | null> | null = render(chunks[0]);
        for (let i = 0; i < chunks.length; i++) {
          const buffer = await pending;
          if (abort.signal.aborted) break;
          pending = i + 1 < chunks.length ? render(chunks[i + 1]) : null;
          if (!buffer) break;

          const source = ctx.createBufferSource();
          source.buffer = buffer;
          if (session.agentAudioDest) {
            source.connect(session.agentAudioDest);
          }
          source.connect(ctx.destination); // so the operator hears it too
          session.agentAudioSource = source;

          await new Promise<void>((resolve) => {
            // Fires on a natural end *and* on `stop()` from a barge-in, so the loop
            // advances or unwinds on both paths.
            source.onended = () => resolve();
            source.start();
          });
          if (abort.signal.aborted) break;
        }
      } catch (e) {
        if (!(e instanceof DOMException && e.name === 'AbortError')) {
          console.error('Failed to play agent audio:', e);
        }
      } finally {
        session.isAgentSpeaking = false;
        session.agentAudioSource = null;
        if (session.agentTtsAbort === abort) session.agentTtsAbort = null;
        if (wasMuted && activeChannel) {
          try {
            await muteClubhouseChannel(activeChannel, true);
          } catch (e) {
            console.error('Failed to restore mute state after agent TTS:', e);
          }
        }
      }
    },
    [isMuted, activeChannel, reportVoiceError, session],
  );

  const previewTtsVoice = useCallback(
    async (options?: {
      voice?: string;
      rate?: string;
      pitch?: string;
      volume?: string;
      text?: string;
    }) => {
      try {
        const AudioContextClass =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const ctx = session.audioCtx || new AudioContextClass();
        if (ctx.state === 'suspended') await ctx.resume();

        const voice = options?.voice || 'en-US-ChristopherNeural';
        const rate = options?.rate || '+0%';
        const pitch = options?.pitch || '+0Hz';
        const volume = options?.volume || '+0%';
        const text = options?.text || 'Hello! I am ready to join the room and chat.';

        const url = apiUrl(
          `/api/agent/tts?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(voice)}&rate=${encodeURIComponent(rate)}&pitch=${encodeURIComponent(pitch)}&volume=${encodeURIComponent(volume)}`,
        );
        const res = await fetch(url);
        if (!res.ok) {
          throw new Error(`TTS preview failed (${res.status})`);
        }
        const buffer = await ctx.decodeAudioData(await res.arrayBuffer());
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        source.start();
      } catch (e) {
        console.error('Failed to preview TTS voice:', e);
        reportVoiceError(`Voice preview failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [session, reportVoiceError],
  );

  const getNetworkInsights = useCallback((): MediaNetworkInsights => {
    let rtt = 0;
    let sendBps = 0;
    let recvBps = 0;
    let sendBytes = 0;
    let recvBytes = 0;
    let bw = 0;
    const connState = session.rtcClient
      ? session.rtcClient.connectionState || 'CONNECTED'
      : joined
        ? 'CONNECTED'
        : 'DISCONNECTED';

    if (session.rtcClient && typeof session.rtcClient.getRTCStats === 'function') {
      try {
        const stats = session.rtcClient.getRTCStats();
        rtt = stats.RTT ?? 0;
        sendBps = Math.round((stats.SendBitrate ?? 0) / 1000);
        recvBps = Math.round((stats.RecvBitrate ?? 0) / 1000);
        sendBytes = stats.SendBytes ?? 0;
        recvBytes = stats.RecvBytes ?? 0;
        bw = Math.round((stats.OutgoingAvailableBandwidth ?? 0) / 1000);
      } catch {
        // The RTC client reports no stats until the peer connection is up, and
        // throws rather than returning empty. The zeros initialised above are the
        // honest reading for "not connected yet"; a thrown stats poll must not take
        // the whole voice session down with it.
      }
    }

    return {
      webrtcState: connState,
      rttMs: rtt,
      sendBitrateKbps: sendBps,
      recvBitrateKbps: recvBps,
      sendBytes,
      recvBytes,
      outgoingBandwidthKbps: bw,
      codec: 'Opus (48 kHz, 2-channel, 20ms frame)',
      sampleRateHz: session.audioCtx?.sampleRate || 48000,
      audioChannels: 2,
      transportProtocol: 'UDP / DTLS 1.2 / SRTP (Agora SD-RTN)',
      rtcDomains: ['agora.io', 'sd-rtn.com', 'webrtc.clubhouse.com', 'edge.agora.io'],
      pubnubOrigin: 'pubsub.pubnub.com',
      pubnubProtocol: 'WSS (WebSocket Secure) / TLS 1.3',
      pubnubChannels: activeChannel
        ? [
            `channel_users:${activeChannel}`,
            `channel_actions:${activeChannel}`,
            `channel_messages:${activeChannel}`,
          ]
        : [],
      heartbeatIntervalS: 30,
      apiGateway: 'https://api.clubhouse.com/api/v2',
      mediaCdn: 'https://clubhouse-prod.s3.amazonaws.com (AWS S3)',
      backendBridge: `${window.location.origin}/api/clubhouse`,
      sttEndpoint: '/api/agent/stt (Fast Whisper / VAD)',
      llmEndpoint: 'http://localhost:1234/v1 (LM Studio OpenAI API)',
      ttsEngine: 'Web Speech Synthesis / Kokoro TTS',
    };
  }, [session, joined, activeChannel]);

  return {
    joined,
    activeChannel,
    isMuted,
    handRaised,
    comments,
    activeReactions,
    liveUsers,
    speakerInvite,
    speakingVolumes,
    chatDisabledReason,
    playAgentAudio,
    previewTtsVoice,
    stopAgentAudio,
    loading,
    error,
    voiceError,
    joinRoom,
    leaveRoom,
    toggleMute,
    raiseHand,
    acceptSpeakerInvite,
    dismissSpeakerInvite,
    sendComment,
    sendReaction,
    seedLiveUsers,
    getNetworkInsights,
  };
}

export interface MediaNetworkInsights {
  webrtcState: string;
  rttMs: number;
  sendBitrateKbps: number;
  recvBitrateKbps: number;
  sendBytes: number;
  recvBytes: number;
  outgoingBandwidthKbps: number;
  codec: string;
  sampleRateHz: number;
  audioChannels: number;
  transportProtocol: string;
  rtcDomains: string[];
  pubnubOrigin: string;
  pubnubProtocol: string;
  pubnubChannels: string[];
  heartbeatIntervalS: number;
  apiGateway: string;
  mediaCdn: string;
  backendBridge: string;
  sttEndpoint: string;
  llmEndpoint: string;
  ttsEngine: string;
}
