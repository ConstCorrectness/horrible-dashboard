import { apiUrl } from '../../origin';
import { useCallback, useRef, useSyncExternalStore } from 'react';
import AgoraRTC from 'agora-rtc-sdk-ng';

import PubNub from 'pubnub';

import { usePaneSession } from '../../layout/use-pane-session';
import { mixer } from '../audio/engine';
import {
  CLUBHOUSE_DJ_BUS_ID,
  busHasSource,
  ensureClubhouseDjBus,
  getState as getMixerStoreState,
  openMicrophone,
  subscribeMixer,
} from '../audio/store';
import { earsActions, type ObservedContextState } from './earsWatchdog';
import { MAX_FLOATERS, parseRoomReaction } from './reactions';
import { Downsampler, UtteranceBuffer, encodeWav, startPcmCapture } from './liveCapture';
import { splitForSpeech } from './speechChunks';
import { displayVolumes, sameVolumes } from './volumes';
import { installWebRtcBundleFix } from './webrtcBundleFix';
import {
  clubhouseSession,
  leaveClubhouseRoom,
  type ChatComment,
  type FloatingReaction,
  type LiveUserState,
  type SpeakerInvite,
} from './roomSession';

export type { ChatComment, FloatingReaction, LiveUserState, SpeakerInvite };
import {
  joinClubhouseChannel,
  pingClubhouseChannel,
  setClubhouseHand,
  sendClubhouseReaction,
  sendClubhouseGif,
  acceptClubhouseSpeaker,
  getClubhouseStatus,
  getClubhouseChannelChat,
  type BecomeSpeakerResult,
  type Channel,
  type JoinChannelResult,
  type ChannelUser,
} from './api';

const CLUBCARD_AGORA_APP_ID = '938d7e95aeaa4f4ca1f416ab40a498d9';

/**
 * The level (mean of `getByteFrequencyData`, 0-255) above which the VAD calls it
 * speech, and the higher one at which a human talking over the agent cuts it off.
 *
 * Named rather than inline because the pane now *draws* this line on its input
 * meter: a threshold the operator cannot see is the difference between "the room is
 * quiet" and "the room is loud enough for me but not for the switch", and those look
 * identical from a transcript panel that never fills in.
 */
const SPEECH_LEVEL = 12;
const BARGE_IN_LEVEL = 22;

/**
 * Utterance length caps. Whisper sees 30 s at a time, and a room where nobody pauses
 * for 750 ms would otherwise transcribe nothing until somebody does. Past the soft
 * cap the utterance is cut at the next gap between words; at the hard cap, wherever
 * it is.
 */
const SOFT_MAX_UTTERANCE_MS = 15_000;
const HARD_MAX_UTTERANCE_MS = 25_000;
/** Too short to caption: Whisper invents words for a syllable. */
const MIN_LIVE_CAPTION_MS = 700;
/** A running context whose tap has delivered nothing for this long is a dead tap. */
const CAPTURE_STALL_MS = 3_000;

/** Mixer strip the agent's room music is monitored through. */
const MUSIC_STRIP = 'clubhouse-music';
/** Room music starts below full scale: it is background to a conversation. */
const MUSIC_VOLUME = 0.6;
/** Fraction of its level the music keeps while the agent is speaking over it. */
const MUSIC_DUCK = 0.25;
/**
 * Upper bounds on the join's network steps. Agora retries a failing gateway or ICE
 * negotiation internally for minutes, and a join awaiting it forever sat on
 * "Connecting…" with no error at all — indistinguishable from a slow room.
 */
const AGORA_JOIN_TIMEOUT_MS = 20_000;
const AGORA_PUBLISH_TIMEOUT_MS = 10_000;
const AUDIO_RESUME_TIMEOUT_MS = 2_000;

/** Reject with a message naming `step` if `promise` has not settled within `ms`. */
function within<T>(promise: Promise<T>, ms: number, step: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`${step} did not respond within ${Math.round(ms / 1000)}s`)),
      ms,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

const CLUBCARD_PUBNUB_SUB_KEY = 'sub-c-a4abea84-9ca3-11ea-8e71-f2b83ac9263d';
const CLUBCARD_PUBNUB_PUB_KEY = 'pub-c-6878d382-5ae6-4494-9099-f930f938868b';

export interface UseClubhouseVoiceProps {
  onLiveUsersChange?: (users: LiveUserState[]) => void;
  onCommentsChange?: (comments: ChatComment[]) => void;
  onSpeakingVolumesChange?: (volumes: Record<number, number>) => void;
  /** One finished utterance heard in the room. */
  onTranscribe?: (text: string, speakerName?: string, speakerId?: number | null) => void;
  /**
   * A person started talking. Fired for every run of speech, whether or not the agent
   * is speaking: the pane uses it to cut the agent off, and to stop a queued reply
   * going out over someone mid-sentence.
   */
  onBargeIn?: () => void;
  onSpeakerInvite?: (invite: SpeakerInvite) => void;
  onHandRaise?: (userId: number, userName: string) => void;
  /** Speech pipeline failed (missing `voice` extra, backend down, decode error). */
  onVoiceError?: (message: string) => void;
  /** Called when the current room music track ends so a queued DJ playlist can advance. */
  onMusicEnded?: () => void;
  /** How often the live caption re-transcribes the utterance in progress. */
  liveCaptionMs?: number;
  /** How long a pause has to last before it ends someone's sentence. */
  endpointingDelayMs?: number;
  /** Whether a person talking stops the agent that is speaking. */
  allowBargeIn?: boolean;
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
    username?: string | null;
    photo_url?: string | null;
    user_id?: number | null;
    is_speaker?: boolean | null;
    is_moderator?: boolean | null;
  } | null;
  user?: {
    name?: string | null;
    username?: string | null;
    photo_url?: string | null;
    user_id?: number | null;
    is_speaker?: boolean | null;
    is_moderator?: boolean | null;
  } | null;
  from_name?: string | null;
  from_photo_url?: string | null;
  from_user_id?: number | null;
  // Room event payloads
  user_id?: number | null;
  user_profile_id?: number | null;
  to_user_id?: number | null;
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

/** `create` for the pane-close hook below: the pane needs a lifetime, not a value. */
const PANE_MARK = () => true;
/** `dispose` for it: a real close of any Clubhouse pane leaves the room. */
const LEAVE_ON_CLOSE = () => void leaveClubhouseRoom();

export function useClubhouseVoice(props?: UseClubhouseVoiceProps) {
  /**
   * The app's room — one for the whole app, because one account is in at most
   * one room (see `clubhouseSession`). It is never torn down on unmount: a tab
   * switch, a split or a desktop switch unmounts this component while the call
   * keeps running, and the old unmount cleanup made the whole room watch you leave
   * because you looked at another tab. See `layout/pane-lifetime`.
   *
   * A real **close** of any Clubhouse pane does leave. It used to dispose only that
   * pane's own session, and a second Clubhouse pane on another desktop had its own,
   * so closing the window in front of you could leave the call playing with nothing
   * on screen that owned it. A desktop board tile has no close, so it never leaves
   * — the shell's room indicator is the way out from there.
   */
  usePaneSession(PANE_MARK, LEAVE_ON_CLOSE);
  const session = clubhouseSession();

  // State lives on the session, not in `useState`: it has to survive the same
  // unmounts the connection does, or a remount renders an empty room on top of a
  // live call — no participants, no chat backlog, mute state reset.
  // A stable subscribe: a fresh `bind` every render made React unsubscribe and
  // resubscribe to the session on every render of the room pane.
  const subscribe = useCallback((listener: () => void) => session.subscribe(listener), [session]);
  const state = useSyncExternalStore(subscribe, session.getState);
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
    liveCaption,
    reactionOptions,
    handraiseQueueSetting,
    isHandraiseEnabled,
    music,
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
    onMusicEnded: props?.onMusicEnded,
  };

  const reportVoiceError = useCallback(
    (message: string) => session.reportVoiceError(message, session.handlers.onVoiceError),
    [session],
  );

  /**
   * Put one reaction on screen for three seconds, keeping the overlay bounded. When the
   * reaction is aimed at someone (`targetUserId`), anchor the floater on that person's
   * tile — the "target" the room shows — by reading the tile's position relative to the
   * overlay; a room-wide reaction keeps the old random low-screen drift.
   */
  const floatReaction = (emoji: string, gifUrl?: string, idHint = '', targetUserId?: number) => {
    const id = `${idHint || 'r'}-${Math.random().toString(36).slice(2, 9)}`;
    let x = 15 + Math.random() * 70;
    let y = 80 + Math.random() * 10;
    if (targetUserId != null && typeof document !== 'undefined') {
      const tile = document.querySelector(`[data-user-id="${targetUserId}"]`);
      const overlay = document.querySelector('.ch-floating-reactions-overlay');
      if (tile && overlay) {
        const t = tile.getBoundingClientRect();
        const o = overlay.getBoundingClientRect();
        if (o.width > 0 && o.height > 0) {
          // A little jitter so repeated reactions on one tile don't stack exactly.
          x = ((t.left + t.width / 2 - o.left) / o.width) * 100 + (Math.random() * 8 - 4);
          y = ((t.top + t.height / 2 - o.top) / o.height) * 100 + (Math.random() * 6 - 3);
        }
      }
    }
    session.update('activeReactions', (prev) =>
      [...prev, { id, emoji, gifUrl, x, y }].slice(-MAX_FLOATERS),
    );
    setTimeout(() => {
      session.update('activeReactions', (prev) => prev.filter((r) => r.id !== id));
    }, 3000);
  };

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

  const upsertRoomDetailsUser = useCallback(
    (userId: number, patch: Partial<ChannelUser>) => {
      const details = session.roomDetails;
      if (!details) return;
      const existingIdx = details.users.findIndex((u) => Number(u.user_id) === Number(userId));
      let nextUsers: ChannelUser[];
      if (existingIdx >= 0) {
        nextUsers = details.users.map((u, idx) =>
          idx === existingIdx ? { ...u, ...patch, user_id: userId } : u,
        );
      } else {
        const isMe =
          session.myProfile?.userId != null &&
          Number(session.myProfile.userId) === Number(userId);
        const fallbackName = isMe ? (session.myProfile?.name ?? 'Me') : null;
        const fallbackPhoto = isMe ? (session.myProfile?.photoUrl ?? null) : null;
        nextUsers = [
          ...details.users,
          {
            user_id: userId,
            name: patch.name ?? fallbackName,
            username: patch.username ?? null,
            photo_url: patch.photo_url ?? fallbackPhoto,
            is_speaker: patch.is_speaker ?? false,
            is_moderator: patch.is_moderator ?? false,
          },
        ];
      }
      session.roomDetails = {
        ...details,
        users: nextUsers,
        num_speakers: nextUsers.filter((u) => u.is_speaker).length,
        num_all: Math.max(details.num_all ?? 0, nextUsers.length),
      };
    },
    [session],
  );

  const removeRoomDetailsUser = useCallback(
    (userId: number) => {
      const details = session.roomDetails;
      if (!details) return;
      const nextUsers = details.users.filter((u) => Number(u.user_id) !== Number(userId));
      if (nextUsers.length === details.users.length) return;
      session.roomDetails = {
        ...details,
        users: nextUsers,
        num_speakers: nextUsers.filter((u) => u.is_speaker).length,
        num_all: Math.max(0, (details.num_all ?? nextUsers.length + 1) - 1),
      };
    },
    [session],
  );

  // Seed or sync liveUsers from a room user list, preserving live mute/hand-raise
  // flags and recent speaker promotions.
  const seedLiveUsers = useCallback(
    (users: ChannelUser[]) => {
      const myId = session.myProfile?.userId ?? null;
      const recentPromotion = session.isSpeaker && Date.now() - session.promotedAt < 15_000;
      session.update('liveUsers', (prev) => {
        const prevById = new Map(prev.map((u) => [u.userId, u]));
        const seen = new Set<number>();
        const next: LiveUserState[] = [];
        for (const u of users) {
          if (u.user_id == null) continue;
          seen.add(u.user_id);
          const existing = prevById.get(u.user_id);
          const isMe = myId != null && Number(u.user_id) === Number(myId);
          const isSpeaker =
            isMe && recentPromotion ? true : (u.is_speaker ?? existing?.isSpeaker ?? false);
          next.push({
            userId: u.user_id,
            handRaised: isSpeaker ? false : (existing?.handRaised ?? false),
            isSpeaker,
            isMuted: isMe ? session.state.isMuted : (existing?.isMuted ?? false),
            ...(u.is_moderator != null
              ? { isModerator: u.is_moderator }
              : existing?.isModerator != null
                ? { isModerator: existing.isModerator }
                : {}),
            ...(u.name ? { name: u.name } : existing?.name ? { name: existing.name } : {}),
            ...(u.username !== undefined
              ? { username: u.username }
              : existing?.username !== undefined
                ? { username: existing.username }
                : {}),
            ...(u.photo_url !== undefined
              ? { photoUrl: u.photo_url }
              : existing?.photoUrl !== undefined
                ? { photoUrl: existing.photoUrl }
                : {}),
          });
        }
        if (myId != null && !seen.has(myId)) {
          const existing = prevById.get(myId);
          next.push({
            userId: myId,
            handRaised: session.isSpeaker
              ? false
              : (existing?.handRaised ?? session.state.handRaised),
            isSpeaker: session.isSpeaker || (existing?.isSpeaker ?? false),
            isMuted: session.state.isMuted,
            ...(existing?.isModerator != null ? { isModerator: existing.isModerator } : {}),
            name: session.myProfile?.name || existing?.name || 'Me',
            photoUrl: session.myProfile?.photoUrl ?? existing?.photoUrl ?? null,
          });
        }
        return next;
      });
    },
    [session],
  );

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

    // No chat backlog here: `get_channel_messages` only answers for a room the
    // account is in, so a fetch before `joinClubhouseChannel` was a guaranteed
    // 400 on every join. The backlog is loaded after the join, below.

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

      const joinedUserId = chDetails.user_id ?? status.user_id;
      if (joinedUserId != null && session.myProfile) {
        session.myProfile.userId = joinedUserId;
      }
      const joinedRoster =
        chDetails.users && chDetails.users.length > 0 ? chDetails.users : (initialUsers ?? []);
      const amISpeakerAtJoin = Boolean(
        joinedUserId != null &&
          joinedRoster.some((u) => Number(u.user_id) === Number(joinedUserId) && u.is_speaker),
      );
      session.isSpeaker = amISpeakerAtJoin;
      session.isTrackPublished = false;
      session.promotedAt = amISpeakerAtJoin ? Date.now() : 0;

      if (chDetails.users && chDetails.users.length > 0) {
        session.roomDetails = {
          channel: channelName,
          topic: chDetails.topic ?? session.roomDetails?.topic ?? null,
          num_speakers:
            chDetails.num_speakers ?? chDetails.users.filter((u) => u.is_speaker).length,
          num_all: chDetails.num_all ?? chDetails.users.length,
          club: chDetails.club ?? session.roomDetails?.club ?? null,
          users: chDetails.users,
        };
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

      // The room's own emoji. Not a constant: a room can serve its own set.
      session.patch({
        reactionOptions: chDetails.emoji_reaction_options ?? [],
        handraiseQueueSetting: chDetails.handraise_queue_setting ?? null,
        isHandraiseEnabled: chDetails.is_handraise_enabled ?? null,
      });

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
        // Bounded: under a strict autoplay policy `resume()` stays pending until a
        // gesture rather than rejecting, and the room is still worth joining silent.
        try {
          await within(audioCtx.resume(), AUDIO_RESUME_TIMEOUT_MS, 'Resuming audio');
        } catch (err) {
          console.warn('AudioContext could not be resumed; audio will be silent:', err);
        }
      }

      const dest = audioCtx.createMediaStreamDestination();
      session.agentAudioDest = dest;

      // Wire the Audio Mixer's `CLUBHOUSE_DJ` bus directly into `agentAudioDest` so any
      // dashboard audio strip routed to `CLUBHOUSE_DJ` (while also routed to `A1` for
      // Bluetooth headphones) broadcasts straight to the Clubhouse stage.
      const djBusGain = audioCtx.createGain();
      djBusGain.gain.value = 1;
      djBusGain.connect(dest);
      session.djBusGain = djBusGain;
      let wiredDjBusNode: AudioNode | null = null;
      const syncClubhouseDjBus = () => {
        if (session.audioCtx !== audioCtx || session.djBusGain !== djBusGain) return;
        const busNode = mixer.busGainNode(CLUBHOUSE_DJ_BUS_ID);
        if (busNode && busNode !== wiredDjBusNode) {
          try {
            busNode.connect(djBusGain);
            wiredDjBusNode = busNode;
          } catch {
            /* ignore cross-context or already connected */
          }
        }
        void syncAgoraTrackMute();
      };
      void ensureClubhouseDjBus().then(() => {
        syncClubhouseDjBus();
      });
      session.djBusUnsubscribe?.();
      session.djBusUnsubscribe = subscribeMixer(syncClubhouseDjBus);

      // The agent's ears: one bus every remote speaker and the operator's mic feed,
      // tapped for PCM (see `liveCapture`) and watched by the VAD's analyser.
      const sttBus = audioCtx.createGain();
      session.sttBus = sttBus;

      // b. Initialize Agora Client
      installWebRtcBundleFix();
      const client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
      session.rtcClient = client;

      // Helper to route remote audio stream track to agent's STT
      /*
       * Tracks already wired into `sttBus`, by track id.
       *
       * Two paths reach here for the same speaker — the join-time sweep above and the
       * `user-published` event — and without this a track connected twice is summed
       * twice into the destination. That inflates the level the VAD decides on *and*
       * the track count the pane now reports, so the meter built to diagnose silence
       * would itself start lying.
       */
      const wiredTracks = new Set<string>();
      const connectRemoteAudioTrackToStt = (track: MediaStreamTrack) => {
        if (wiredTracks.has(track.id)) return;
        wiredTracks.add(track.id);
        try {
          const stream = new MediaStream([track]);
          const source = audioCtx.createMediaStreamSource(stream);
          source.connect(sttBus);
          // Counted so the pane can say "nobody's audio is reaching me" rather than
          // "listening…". Zero here with people plainly talking is a different bug
          // from a level that never crosses the threshold, and they are
          // indistinguishable from the transcript panel alone.
          session.earsTracks++;
        } catch (e) {
          // Not wired after all, so let a later attempt try again.
          wiredTracks.delete(track.id);
          console.warn('Failed to route remote audio track to STT destination:', e);
        }
      };

      // c. Listen for remote audio updates
      client.on('user-published', async (user, mediaType) => {
        if (mediaType === 'audio' && typeof user.uid === 'number') {
          updateLiveUser(user.uid, { isSpeaker: true, isMuted: false });
          upsertRoomDetailsUser(user.uid, { is_speaker: true });
        }
        await client.subscribe(user, mediaType);
        if (mediaType === 'audio') {
          const remoteAudioTrack = user.audioTrack;
          remoteAudioTrack?.play();

          if (remoteAudioTrack) {
            connectRemoteAudioTrackToStt(remoteAudioTrack.getMediaStreamTrack());
          }
        }
      });

      client.on('user-unpublished', (user, mediaType) => {
        if (mediaType === 'audio') {
          user.audioTrack?.stop();
          if (typeof user.uid === 'number') {
            updateLiveUser(user.uid, { isMuted: true });
          }
        }
      });

      client.on('user-info-updated', (uid, msg) => {
        if (typeof uid !== 'number') return;
        if (msg === 'mute-audio') {
          updateLiveUser(uid, { isMuted: true });
        } else if (msg === 'unmute-audio') {
          updateLiveUser(uid, { isMuted: false });
        }
      });

      const refreshAgoraTokens = async () => {
        if (session.state.activeChannel !== channelName || session.rtcClient !== client) return;
        try {
          const fresh = await joinClubhouseChannel(channelName);
          if (session.state.activeChannel !== channelName || session.rtcClient !== client) return;
          if (fresh.pubnub_token && session.pubnub) {
            session.pubnub.setToken(fresh.pubnub_token);
          }
          if (fresh.token) {
            await client.renewToken(fresh.token);
          }
        } catch (err) {
          console.warn('[ClubhouseVoice] Could not renew expiring room token:', err);
        }
      };
      client.on('token-privilege-will-expire', () => void refreshAgoraTokens());
      client.on('token-privilege-did-expire', () => void refreshAgoraTokens());

      // d. Join the Agora stream
      await within(
        client.join(
          CLUBCARD_AGORA_APP_ID,
          channelName,
          chDetails.token,
          chDetails.user_id ?? undefined,
        ),
        AGORA_JOIN_TIMEOUT_MS,
        'The Agora voice connection',
      );

      /*
       * Wire up everyone who was already talking when we walked in.
       *
       * This has to **subscribe** first. `remoteUser.audioTrack` is only populated
       * by `client.subscribe()`, so straight after `join()` it is undefined for every
       * user in the room — the old version of this loop tested `remoteUser.audioTrack`
       * and therefore connected nothing, ever. It read like a safety net while being
       * a no-op, which left the agent's hearing depending entirely on `user-published`
       * firing after the join.
       *
       * That is the difference between joining an empty room (someone publishes later,
       * the event fires, it works) and switching into a room where eight people are
       * already mid-conversation. Symptom: a transcript panel that never fills in.
       *
       * Failures are per-user: one speaker we cannot subscribe to must not cost us
       * the other seven.
       */
      await Promise.all(
        client.remoteUsers.map(async (remoteUser) => {
          if (!remoteUser.hasAudio) return;
          try {
            await client.subscribe(remoteUser, 'audio');
            const track = remoteUser.audioTrack;
            if (!track) return;
            track.play();
            connectRemoteAudioTrackToStt(track.getMediaStreamTrack());
          } catch (err) {
            console.warn(`Could not subscribe to ${remoteUser.uid}'s audio:`, err);
          }
        }),
      );

      // Start the ears: PCM tap + VAD
      try {
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        sttBus.connect(analyser);

        const utterance = new UtteranceBuffer();
        session.sttUtterance = utterance;
        /** Whether this tap still belongs to the room that built it. */
        const current = () => session.sttUtterance === utterance;

        let silenceTicks = 0;
        let isSpeaking = false;
        let bargeInTicks = 0;
        let activeSpeakerUidDuringSpeech: number | null = null;
        let lastLiveAt = 0;
        /** One live caption at the server at a time, so a slow model sets the pace. */
        let liveInFlight = false;
        /** Which utterance the caption on screen belongs to. */
        let captionFor = -1;
        let building = false;

        const buildCapture = async () => {
          if (building) return;
          building = true;
          try {
            session.sttCapture?.stop();
            session.sttCapture = null;
            const downsampler = new Downsampler(audioCtx.sampleRate);
            const capture = await startPcmCapture(audioCtx, sttBus, (frames) => {
              session.sttLastFrameAt = Date.now();
              utterance.push(downsampler.process(frames));
            });
            // A teardown or a room switch got there while the worklet was loading.
            if (!current()) {
              capture.stop();
              return;
            }
            session.sttCapture = capture;
            session.sttLastFrameAt = Date.now();
          } finally {
            building = false;
          }
        };
        session.restartEars = () => void buildCapture();

        /** Post one stretch of audio. `null` when it failed — and the failure is reported. */
        const transcribe = async (audio: Float32Array): Promise<string | null> => {
          const formData = new FormData();
          formData.append(
            'file',
            new File([encodeWav(audio)], 'utterance.wav', { type: 'audio/wav' }),
          );
          try {
            const res = await fetch(apiUrl('/api/agent/stt'), {
              method: 'POST',
              body: formData,
            });
            // A non-2xx here is the single most common "the agent doesn't hear me"
            // cause: /api/agent/stt answers 503 until `uv sync --extra voice` has run.
            if (!res.ok) {
              let detail = `HTTP ${res.status}`;
              try {
                detail = (await res.json()).detail || detail;
              } catch {
                /* keep the status */
              }
              reportVoiceError(`Speech-to-text unavailable: ${detail}`);
              return null;
            }
            const json = await res.json();
            // A 200 is not a success: transcription failures answer 200 with an
            // empty `text` and an `error`. Reported, or a deaf agent is
            // indistinguishable from a quiet room.
            if (json.error) {
              reportVoiceError(`Speech-to-text failed: ${json.error}`);
              return null;
            }
            session.clearVoiceError();
            const text = String(json.text ?? '').trim();
            if (text) session.lastHeardAt = Date.now();
            return text;
          } catch (err) {
            console.error('STT failed:', err);
            reportVoiceError(
              `Speech-to-text failed: ${err instanceof Error ? err.message : String(err)}`,
            );
            return null;
          }
        };

        const showCaption = (id: number, text: string, speakerId: number | null) => {
          captionFor = id;
          session.patch({ liveCaption: { text, speakerId } });
        };

        /** The utterance so far, for the caption only — never sent to the agent. */
        const sendLive = (speakerUid: number | null) => {
          liveInFlight = true;
          lastLiveAt = Date.now();
          const id = utterance.id;
          void transcribe(utterance.snapshot()).then((text) => {
            liveInFlight = false;
            // The sentence ended (or the next began) while this was at the server;
            // its final transcript is the one to show now.
            if (!text || !current() || utterance.id !== id || !utterance.active) return;
            showCaption(id, text, speakerUid);
          });
        };

        /**
         * End the utterance and transcribe all of it. Queued behind the previous
         * one, so the agent hears sentences in the order they were spoken.
         */
        const finishUtterance = (speakerUid: number | null) => {
          if (!utterance.active) return;
          const id = utterance.id;
          const heard = utterance.hasSpeech;
          const audio = utterance.end();
          // Only the silent tail of a sentence already cut at its length cap.
          if (!heard) return;
          session.sttFinals = session.sttFinals.then(async () => {
            const text = await transcribe(audio);
            if (!current()) return;
            if (captionFor === id) {
              captionFor = -1;
              session.patch({ liveCaption: null });
            }
            if (text) session.handlers.onTranscribe?.(text, undefined, speakerUid);
          });
        };

        await buildCapture();
        if (!session.sttCapture) {
          reportVoiceError('Could not start the agent’s ears: no audio capture is available.');
        }

        session.vadInterval = setInterval(() => {
          const dataArray = new Uint8Array(analyser.frequencyBinCount);
          analyser.getByteFrequencyData(dataArray);
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
          const avgVolume = sum / dataArray.length;
          // The VAD's own decision variable, exposed for the pane's meter. Written
          // as a plain field at 50 Hz; the pane pulls it on its own timer.
          session.earsLevel = avgVolume;
          const now = Date.now();

          // Barge-in: Stop agent if it's currently speaking and human is speaking loudly and clearly
          if (session.isAgentSpeaking) {
            if (avgVolume > BARGE_IN_LEVEL) {
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

          if (avgVolume > SPEECH_LEVEL) {
            // Track loudest speaker UID across live volumes
            const vols = session.rawVolumes;
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
            }
            isSpeaking = true;
            silenceTicks = 0;
            if (!utterance.active) utterance.begin();
            utterance.hasSpeech = true;

            // Nobody has paused in a long time. Cut here, or Whisper's 30 s window
            // drops the rest — and nothing reaches the agent until a pause that, in
            // a busy room, may never come.
            if (utterance.durationMs >= HARD_MAX_UTTERANCE_MS) {
              finishUtterance(activeSpeakerUidDuringSpeech);
              utterance.begin();
              utterance.hasSpeech = true;
            }
          } else if (isSpeaking) {
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
              // End of speech detected: transcribe the whole utterance.
              finishUtterance(completedSpeakerUid);
            } else if (utterance.durationMs >= SOFT_MAX_UTTERANCE_MS) {
              // A long turn: cut at this gap between words rather than mid-word at
              // the hard cap.
              finishUtterance(activeSpeakerUidDuringSpeech);
              utterance.begin();
            }
          }

          // The live caption: the utterance so far, about once a second.
          if (
            utterance.active &&
            utterance.hasSpeech &&
            !liveInFlight &&
            utterance.durationMs >= MIN_LIVE_CAPTION_MS &&
            now - lastLiveAt >= (propsRef.current?.liveCaptionMs ?? 1000)
          ) {
            sendLive(activeSpeakerUidDuringSpeech);
          }
        }, 50);

        /**
         * The ears watchdog.
         *
         * Everything upstream of the agent is a live browser resource that can die
         * quietly and stay dead: a capture node whose processor crashed, an
         * `AudioContext` the OS suspended on a device change or a backgrounded tab.
         * None of them raise anything a person can see. The VAD loop keeps ticking
         * over an analyser reading zeros, the pane keeps saying "listening", and the
         * agent is simply deaf from then on — which is what "it stops working" means
         * in practice.
         *
         * A tap is judged dead when the context is running and no frames have
         * arrived for a few seconds: a healthy one delivers every ~40 ms, silence
         * included.
         *
         * It is *not* folded into the 50 ms VAD loop: that loop is one of the things
         * being watched, and a watchdog that dies with its subject is decoration.
         */
        session.earsInterval = setInterval(() => {
          const ctx = session.audioCtx;
          if (!session.sttBus || building) return;
          const stalled =
            ctx?.state === 'running' && Date.now() - session.sttLastFrameAt > CAPTURE_STALL_MS;
          const actions = earsActions({
            contextState: (ctx?.state as ObservedContextState) ?? null,
            recorderState: !session.sttCapture ? null : stalled ? 'inactive' : 'recording',
          });

          if (actions.resumeContext) {
            void ctx?.resume().catch(() => {
              /* the restart below is what actually matters; a failed resume
                 surfaces as the repeat-restart warning */
            });
          }
          if (actions.restartRecorder) {
            session.earsRestarts++;
            // Said once, and only on the second restart: a single one is routine (a
            // device change) and toasting it would train people to ignore the
            // warning on the day it means something.
            if (session.earsRestarts === 2) {
              reportVoiceError(
                'The agent stopped hearing the room; restarting its microphone feed.',
              );
            }
            void buildCapture();
          }
        }, 2000);
      } catch (err) {
        console.error('Failed to start the STT capture:', err);
        reportVoiceError(
          `Could not start the agent’s ears: ${err instanceof Error ? err.message : String(err)}`,
        );
      }

      // Always create `humanGain` on the audio context so `toggleMute` can gate the
      // operator's microphone branch even if `openMicrophone()` times out at join time
      // and is opened lazily when the operator clicks Unmute.
      const humanGain = audioCtx.createGain();
      humanGain.gain.value = 0; // muted by default for Clubhouse room
      humanGain.connect(dest); // published to Clubhouse room when unmuted
      session.humanGain = humanGain;

      // Get physical mic (Optional, handle missing permissions or timeouts gracefully)
      try {
        const micStream = await Promise.race([
          // Through the mixer so the chosen microphone is honoured, with a
          // fallback to the default and a failure that names what it could see.
          openMicrophone(),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('Microphone permission timeout')), 3000),
          ),
        ]);

        session.physicalMicStream = micStream;
        const micSource = audioCtx.createMediaStreamSource(micStream);
        session.micSource = micSource;
        micSource.connect(humanGain);
        micSource.connect(sttBus); // agent's ears always hear operator
      } catch (err) {
        console.warn('Could not access physical microphone, continuing as listener:', err);
        reportVoiceError(
          `Microphone unavailable — you are connected as a listener: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }

      // Create mixed microphone stream. Only publish immediately if we joined as a
      // speaker: publishing at join time with a listener-only Agora token fails at
      // the Agora SFU (or poisons `localTrackMap` under an unauthorized ticket so
      // later speaker audio is dropped). When promoted from audience to stage,
      // `upgradeToSpeaker()` renews the Agora token and publishes `localAudioTrack`.
      const mixedTrack = AgoraRTC.createCustomAudioTrack({
        mediaStreamTrack: dest.stream.getAudioTracks()[0],
      });
      session.localAudioTrack = mixedTrack;
      if (session.isSpeaker) {
        await within(
          client.publish(mixedTrack),
          AGORA_PUBLISH_TIMEOUT_MS,
          'Publishing your microphone track',
        );
        session.isTrackPublished = true;
        await mixedTrack.setMuted(true).catch(() => {});
      }
      session.patch({ isMuted: true });

      // e2. Start Agora volume indicator — fires every 200ms with per-user volumes.
      //
      // Raw levels go on the session for the VAD loop to pull (it attributes speech
      // to the loudest uid, which wants the real numbers). Only what the stage can
      // show goes through state: every `patch` re-renders the whole room pane, and
      // publishing raw levels did that five times a second whether or not anything
      // on screen changed.
      client.enableAudioVolumeIndicator();
      client.on('volume-indicator', (volumes) => {
        const raw: Record<number, number> = {};
        for (const { uid, level } of volumes) {
          raw[uid as number] = level;
        }
        session.rawVolumes = raw;
        const shown = displayVolumes(raw);
        if (!sameVolumes(shown, session.getState().speakingVolumes)) {
          session.set('speakingVolumes', shown);
        }
      });
      // Clear stale volumes every 600ms in case no volume event fires. Only when
      // something actually expired: a fresh object every tick was a re-render
      // every tick of a room where nobody was talking.
      session.volumeInterval = setInterval(() => {
        session.update('speakingVolumes', (prev) => {
          const now: Record<number, number> = {};
          for (const [uid, vol] of Object.entries(prev)) {
            if (vol > 5) now[Number(uid)] = vol;
          }
          return sameVolumes(now, prev) ? prev : now;
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
            // Stringified: devtools collapses a logged object to `{…}`, and a copy-paste of
            // the console then loses exactly the fields being looked for.
            console.log('PubNub Message Received:', JSON.stringify(event.message));
            const msg = event.message as PubNubRoomMessage;
            if (!msg) return;

            // `action_user_profile` is who a reaction event came from; without it our own
            // echo is not recognised and every reaction floats twice.
            const sender =
              msg.user_profile ||
              msg.user ||
              (msg as { action_user_profile?: typeof msg.user }).action_user_profile ||
              {};
            const rawSenderId =
              sender.user_id ?? msg.user_id ?? msg.user_profile_id ?? msg.from_user_id;
            const senderId = rawSenderId != null ? Number(rawSenderId) : undefined;

            // --- Room signaling events ---
            const action = msg.action;

            if (action === 'join_channel') {
              if (senderId != null) {
                const isSpk = sender.is_speaker ?? msg.is_speaker ?? false;
                const isMod = sender.is_moderator ?? msg.is_moderator ?? undefined;
                const isMeJoining = myUserId != null && Number(senderId) === Number(myUserId);
                upsertRoomDetailsUser(senderId, {
                  is_speaker: isSpk,
                  ...(isMod != null ? { is_moderator: isMod } : {}),
                  ...(sender.name ? { name: sender.name } : {}),
                  ...(sender.username !== undefined ? { username: sender.username } : {}),
                  ...(sender.photo_url !== undefined ? { photo_url: sender.photo_url } : {}),
                });
                updateLiveUser(senderId, {
                  isSpeaker: isSpk,
                  isMuted: isMeJoining ? session.state.isMuted : false,
                  ...(isMod != null ? { isModerator: isMod } : {}),
                  ...(sender.name ? { name: sender.name } : {}),
                  ...(sender.username !== undefined ? { username: sender.username } : {}),
                  ...(sender.photo_url !== undefined ? { photoUrl: sender.photo_url } : {}),
                });
              }
            } else if (action === 'leave_channel') {
              if (senderId != null) {
                removeRoomDetailsUser(senderId);
                removeLiveUser(senderId);
              }
            } else if (action === 'remove_speaker') {
              // Demote from speaker stage to audience (never remove from the room).
              if (senderId != null) {
                if (myUserId != null && Number(senderId) === Number(myUserId)) {
                  void demoteFromSpeaker();
                } else {
                  upsertRoomDetailsUser(senderId, { is_speaker: false, is_moderator: false });
                  updateLiveUser(senderId, {
                    isSpeaker: false,
                    isModerator: false,
                    handRaised: false,
                    isMuted: true,
                  });
                }
              }
            } else if (action === 'raise_hands' || action === 'hand_raised') {
              if (senderId != null) {
                updateLiveUser(senderId, {
                  handRaised: true,
                  ...(sender.name ? { name: sender.name } : {}),
                  ...(sender.photo_url !== undefined ? { photoUrl: sender.photo_url } : {}),
                });
                if (session.handlers.onHandRaise && senderId !== myUserId) {
                  session.handlers.onHandRaise(
                    senderId,
                    sender.name || msg.from_name || 'Someone',
                  );
                }
              }
            } else if (action === 'lower_hands' || action === 'unraise_hands') {
              if (senderId != null) {
                updateLiveUser(senderId, { handRaised: false });
              }
            } else if (
              action === 'add_speaker' ||
              action === 'make_speaker' ||
              action === 'accept_speaker_invite'
            ) {
              if (senderId != null) {
                upsertRoomDetailsUser(senderId, {
                  is_speaker: true,
                  ...(sender.is_moderator != null ? { is_moderator: sender.is_moderator } : {}),
                  ...(sender.name ? { name: sender.name } : {}),
                  ...(sender.username !== undefined ? { username: sender.username } : {}),
                  ...(sender.photo_url !== undefined ? { photo_url: sender.photo_url } : {}),
                });
                updateLiveUser(senderId, {
                  isSpeaker: true,
                  handRaised: false,
                  ...(sender.is_moderator != null ? { isModerator: sender.is_moderator } : {}),
                  ...(sender.name ? { name: sender.name } : {}),
                  ...(sender.username !== undefined ? { username: sender.username } : {}),
                  ...(sender.photo_url !== undefined ? { photoUrl: sender.photo_url } : {}),
                });
                if (myUserId != null && Number(senderId) === Number(myUserId)) {
                  void upgradeToSpeaker();
                }
              }
            } else if (action === 'make_moderator') {
              if (senderId != null) {
                upsertRoomDetailsUser(senderId, { is_speaker: true, is_moderator: true });
                updateLiveUser(senderId, {
                  isSpeaker: true,
                  isModerator: true,
                  handRaised: false,
                });
                if (myUserId != null && Number(senderId) === Number(myUserId)) {
                  void upgradeToSpeaker();
                }
              }
            } else if (action === 'lose_moderator') {
              if (senderId != null) {
                upsertRoomDetailsUser(senderId, { is_moderator: false });
                updateLiveUser(senderId, { isModerator: false });
              }
            } else if (action === 'reject_speaker_invite') {
              if (senderId != null) {
                upsertRoomDetailsUser(senderId, { is_speaker: false });
                updateLiveUser(senderId, { isSpeaker: false, handRaised: false });
              }
            } else if (action === 'mute') {
              if (senderId != null) {
                updateLiveUser(senderId, { isMuted: true });
              }
            } else if (action === 'unmute') {
              if (senderId != null) {
                updateLiveUser(senderId, { isMuted: false });
              }
            } else if (action === 'mute_speaker' || action === 'update_muted') {
              const mutedVal = msg.is_muted ?? true;
              const rawTargetId =
                msg.user_id ?? msg.to_user_id ?? sender.user_id ?? msg.user_profile_id;
              const targetId = rawTargetId != null ? Number(rawTargetId) : undefined;
              const isSelfMute =
                (targetId != null && myUserId != null && Number(targetId) === Number(myUserId)) ||
                (action === 'mute_speaker' &&
                  targetId == null &&
                  Boolean(
                    event.channel?.startsWith('channel_user.') ||
                      event.channel?.startsWith('users.'),
                  ));
              if (isSelfMute) {
                if (!session.isAgentSpeaking) {
                  if (session.humanGain) {
                    session.humanGain.gain.value = mutedVal ? 0 : 1;
                  }
                  session.patch({ isMuted: mutedVal });
                  if (myUserId != null) {
                    updateLiveUser(Number(myUserId), { isMuted: mutedVal });
                  }
                  if (session.localAudioTrack && session.isTrackPublished && !session.music) {
                    void session.localAudioTrack.setMuted(mutedVal).catch(() => {});
                  }
                }
              } else if (targetId != null) {
                updateLiveUser(targetId, { isMuted: mutedVal });
              }
            } else if (action === 'uninvite_speaker') {
              const targetId = msg.user_id ?? msg.to_user_id;
              const isTargetedAtMe =
                targetId != null
                  ? myUserId != null && Number(targetId) === Number(myUserId)
                  : Boolean(
                      event.channel?.startsWith('channel_user.') ||
                        event.channel?.startsWith('users.') ||
                        myUserId != null,
                    );
              if (isTargetedAtMe) {
                session.patch({ speakerInvite: null });
              }
            } else if (action === 'invite_speaker') {
              // Clubhouse delivers `invite_speaker` on `channel_user.<channel>.<myUserId>`
              // with `{ action: "invite_speaker", channel, from_name, from_user_id, from_photo_url }`
              // and no `user_id` field (the recipient is implied by the private channel).
              const targetId = msg.user_id ?? msg.to_user_id;
              const isTargetedAtMe =
                targetId != null
                  ? myUserId != null && Number(targetId) === Number(myUserId)
                  : Boolean(
                      event.channel?.startsWith('channel_user.') ||
                        event.channel?.startsWith('users.') ||
                        myUserId != null,
                    );
              if (isTargetedAtMe) {
                const invite: SpeakerInvite = {
                  moderatorId: Number(msg.from_user_id ?? msg.moderator_id ?? senderId ?? 0),
                  moderatorName:
                    msg.from_name || msg.moderator_name || sender.name || 'A moderator',
                  moderatorPhoto:
                    msg.from_photo_url || msg.moderator_photo_url || sender.photo_url || null,
                };
                session.patch({ speakerInvite: invite });
                if (session.handlers.onSpeakerInvite) {
                  session.handlers.onSpeakerInvite(invite);
                }
              }
            } else if (action === 'end_channel' || action === 'remove_from_channel') {
              const isTargetedAtMe =
                action === 'end_channel' ||
                senderId == null ||
                (myUserId != null && Number(senderId) === Number(myUserId)) ||
                Boolean(
                  event.channel?.startsWith('channel_user.') ||
                    event.channel?.startsWith('users.'),
                );
              if (isTargetedAtMe && session.state.activeChannel === channelName) {
                void leaveRoom().then(() =>
                  session.patch({
                    error: 'This room has ended, or a moderator removed you from it.',
                  }),
                );
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
                    userId: senderId ?? null,
                    username: sender.username ?? null,
                    userPhoto: sender.photo_url || msg.from_photo_url || null,
                    text,
                    timestamp: Math.floor((Number(event.timetoken) || Date.now() * 10000) / 10000),
                  },
                ]);
              }
            }

            // --- Reactions: emoji and GIF ---
            // Temporary capture to learn a reaction's real shape — especially whether
            // one can be aimed at a single person. A room-wide reaction arrives on
            // `channel_all.<room>`; one targeted at a user should arrive on that user's
            // own `channel_user.<room>.<userId>` channel, and/or carry a recipient id in
            // the payload. So log, for any reaction-ish message: the PubNub channel it
            // came in on, the full payload, and every field that could name a target or
            // a sender. Open Clubdeck and send reactions (tap a person vs. the room) to
            // populate this; remove once the field is identified.
            const reactionish =
              /react|reaction|gif/i.test(action ?? '') ||
              'emoji' in msg ||
              'reaction' in msg ||
              'gif' in msg ||
              'gif_url' in msg;
            if (reactionish) {
              const rec = msg as Record<string, unknown>;
              const idish = Object.fromEntries(
                Object.entries(rec).filter(([k]) =>
                  /user_id|from_|to_|target|recipient|receiver|action_user|for_user|dest/i.test(k),
                ),
              );
              console.log(
                '[reaction-shape]',
                JSON.stringify({
                  onChannel: event.channel, // channel_all.<room> = broadcast; channel_user.<room>.<id> = targeted
                  myUserId,
                  senderId: senderId ?? null,
                  action: action ?? null,
                  idFields: idish, // any key that could be a target/recipient/sender
                  full: event.message,
                }),
              );
            }
            const reaction = parseRoomReaction(msg as Record<string, unknown>);
            if (reaction) {
              // Our own are floated when the send succeeds; Clubhouse echoes them back.
              if (senderId != null && myUserId != null && Number(senderId) === Number(myUserId))
                return;
              floatReaction(
                reaction.emoji ?? '',
                reaction.gifUrl,
                String(event.timetoken || ''),
                reaction.targetUserId,
              );
            } else if (/gif/i.test(action ?? '') || 'gif' in msg) {
              // A GIF we could not show: its host is not on the allowlist, or its
              // URL is somewhere unexpected. Said once per message so the shape is
              // there to read the next time someone adds a provider.
              console.warn('Clubhouse GIF reaction not rendered:', event.message);
            }
          },
        });

        const channelsToSubscribe = [
          `channel_all.${channelName}`,
          `channel_user.${channelName}.${myUserId}`,
          `users.${myUserId}`,
          ...(session.isSpeaker ? [`channel_speakers.${channelName}`] : []),
        ];
        pubnub.subscribe({ channels: channelsToSubscribe });
      }

      // g. Heartbeat ping loop (every 30s). The ping is also how Clubhouse says
      // the account is out of the room -- it ended, or a moderator removed us --
      // by answering `should_leave: true`. Ignoring that left a pane still
      // showing the room while every send and hand-raise was refused as
      // addressed to a room we are not in. A late answer for a room we have
      // since switched away from must not tear down the new one.
      const onPing = (res: { should_leave?: boolean | null }) => {
        if (!res.should_leave || session.state.activeChannel !== channelName) return;
        void leaveRoom().then(() =>
          session.patch({ error: 'This room has ended, or a moderator removed you from it.' }),
        );
      };
      void pingClubhouseChannel(channelName)
        .then(onPing)
        .catch((err) => {
          console.error('Initial heartbeat ping failed:', err);
        });
      session.pingInterval = setInterval(async () => {
        try {
          onPing(await pingClubhouseChannel(channelName));
        } catch (err) {
          console.error('Heartbeat ping failed:', err);
        }
      }, 30000);

      session.patch({ joined: true });
      session.patch({ handRaised: false });
      session.set('comments', []);
      session.set('activeReactions', []);
      session.patch({ speakerInvite: null });
      session.rawVolumes = {};
      session.set('speakingVolumes', {});

      if (joinedRoster.length > 0) {
        seedLiveUsers(joinedRoster);
      } else if (joinedUserId != null) {
        seedLiveUsers([
          {
            user_id: joinedUserId,
            name: session.myProfile?.name ?? 'Me',
            username: null,
            photo_url: session.myProfile?.photoUrl ?? null,
            is_speaker: session.isSpeaker,
            is_moderator: false,
          },
        ]);
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
      console.error('Join room failed:', e);
      // `session.teardown()` directly, never `leaveRoom()`. This runs *inside* the
      // join's `serialize` task, and `leaveRoom` queues behind that same task, so
      // awaiting it here waited on itself: the join never settled, the pane sat on
      // "Connecting…" with no error, and every later join and leave in the pane
      // queued behind it. Teardown also resets the state, so the error goes after it.
      // `activeChannel` is set once the upstream join succeeds, so this also tells
      // Clubhouse we left instead of leaving the account half-joined.
      if (session.state.activeChannel || session.rtcClient || session.localAudioTrack) {
        await session.teardown();
      }
      session.patch({ error: errMsg });
    } finally {
      session.patch({ loading: false });
    }
  };

  /**
   * Upgrade the live room session from audience listener to active stage speaker.
   *
   * Called when:
   * 1. `acceptSpeakerInvite()` succeeds (`/become_speaker` returns new speaker tokens),
   * 2. PubNub delivers `add_speaker`, `accept_speaker_invite`, or `make_moderator` for
   *    our user (e.g. a moderator accepted our raised hand directly), or
   * 3. `/get_channel` polling observes that Clubhouse promoted us to `is_speaker: true`.
   *
   * Immediately moves the user onto the stage in `liveUsers` and `roomDetails` (so
   * the UI switches to `🎙️ Unmute Mic` without waiting for a poll), renews the PubNub
   * and Agora tokens to speaker-grade credentials, and publishes `localAudioTrack`.
   */
  const connectPhysicalMicrophone = useCallback(
    async (timeoutMs = 5000): Promise<boolean> => {
      if (
        session.physicalMicStream &&
        session.physicalMicStream.getAudioTracks().some((t) => t.readyState === 'live')
      ) {
        return true;
      }
      const audioCtx = session.audioCtx;
      const dest = session.agentAudioDest;
      if (!audioCtx || !dest) return false;

      if (!session.humanGain) {
        const humanGain = audioCtx.createGain();
        humanGain.gain.value = session.state.isMuted ? 0 : 1;
        humanGain.connect(dest);
        session.humanGain = humanGain;
      }

      try {
        const micStream = await Promise.race([
          openMicrophone(),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('Microphone permission timeout')), timeoutMs),
          ),
        ]);
        if (session.audioCtx !== audioCtx) {
          micStream.getTracks().forEach((t) => t.stop());
          return false;
        }
        session.micSource?.disconnect();
        session.physicalMicStream?.getTracks().forEach((t) => t.stop());
        session.physicalMicStream = micStream;
        const micSource = audioCtx.createMediaStreamSource(micStream);
        session.micSource = micSource;
        micSource.connect(session.humanGain);
        if (session.sttBus) {
          micSource.connect(session.sttBus);
        }
        return true;
      } catch (err) {
        console.warn('[ClubhouseVoice] Could not open physical microphone:', err);
        return false;
      }
    },
    [session],
  );

  /**
   * Synchronize the published Agora track's native mute state (`agora_native_mute`).
   *
   * Clubhouse has no `/unmute_speaker` HTTP route (404), and `/mute_speaker` is a
   * one-way moderator remote-mute command: calling `/mute_speaker` with `is_muted: false`
   * ignores the boolean and echoes `{ action: "mute_speaker" }` back on PubNub ~120ms
   * later, snapping `isMuted` right back to `true`. Self-mute/unmute in Clubhouse rooms
   * is signaled natively via Agora's `localAudioTrack.setMuted(boolean)`.
   */
  const syncAgoraTrackMute = useCallback(async () => {
    const track = session.localAudioTrack;
    if (!track || !session.isTrackPublished) return;
    const djBusActive = busHasSource(getMixerStoreState(), CLUBHOUSE_DJ_BUS_ID);
    const shouldMute =
      session.state.isMuted &&
      !session.isAgentSpeaking &&
      session.music === null &&
      !djBusActive;
    try {
      await track.setMuted(shouldMute);
    } catch (err) {
      console.warn('[ClubhouseVoice] localAudioTrack.setMuted failed:', err);
    }
  }, [session]);

  const upgradeToSpeaker = useCallback(
    (
      providedTokens?: Partial<BecomeSpeakerResult | JoinChannelResult>,
      options?: { preserveMute?: boolean },
    ): Promise<void> => {
      const channel = session.state.activeChannel;
      if (!channel) return Promise.resolve();

      const wasAlreadySpeaker = session.isSpeaker;
      const nextMuted =
        wasAlreadySpeaker || options?.preserveMute ? session.state.isMuted : true;
      const myId = session.myProfile?.userId ?? null;
      session.isSpeaker = true;
      if (!wasAlreadySpeaker || session.promotedAt === 0) {
        session.promotedAt = Date.now();
      }
      if (session.humanGain) {
        session.humanGain.gain.value = nextMuted ? 0 : 1;
      }
      if (myId != null) {
        upsertRoomDetailsUser(myId, { is_speaker: true });
        updateLiveUser(myId, {
          isSpeaker: true,
          handRaised: false,
          isMuted: nextMuted,
          name: session.myProfile?.name || 'Me',
          photoUrl: session.myProfile?.photoUrl ?? null,
        });
      }
      session.patch({
        handRaised: false,
        speakerInvite: null,
        isMuted: nextMuted,
        error: null,
      });

      const runUpgrade = async () => {
        let agoraToken = providedTokens?.token ?? null;
        let pubnubToken = providedTokens?.pubnub_token ?? null;

        if ((!agoraToken || !pubnubToken) && (!wasAlreadySpeaker || !session.isTrackPublished)) {
          try {
            const fresh = await joinClubhouseChannel(channel);
            if (session.state.activeChannel !== channel) return;
            agoraToken = agoraToken || fresh.token || null;
            pubnubToken = pubnubToken || fresh.pubnub_token || null;
            if (fresh.users && fresh.users.length > 0) {
              session.roomDetails = {
                channel,
                topic: fresh.topic ?? session.roomDetails?.topic ?? null,
                num_speakers:
                  fresh.num_speakers ?? fresh.users.filter((u) => u.is_speaker).length,
                num_all: fresh.num_all ?? fresh.users.length,
                club: fresh.club ?? session.roomDetails?.club ?? null,
                users: fresh.users,
              };
              seedLiveUsers(fresh.users);
            }
          } catch (err) {
            console.warn('[ClubhouseVoice] Could not fetch fresh speaker tokens:', err);
          }
        }

        if (session.state.activeChannel !== channel) return;

        if (pubnubToken && session.pubnub) {
          try {
            session.pubnub.setToken(pubnubToken);
            session.pubnub.subscribe({
              channels: [`channel_speakers.${channel}`],
            });
          } catch (err) {
            console.warn('[ClubhouseVoice] Could not update PubNub speaker token:', err);
          }
        }

        const client = session.rtcClient;
        if (client) {
          if (agoraToken) {
            try {
              await within(
                client.renewToken(agoraToken),
                AGORA_JOIN_TIMEOUT_MS,
                'Renewing Agora speaker token',
              );
            } catch (err) {
              console.warn('[ClubhouseVoice] Agora renewToken failed:', err);
            }
          }

          if (session.state.activeChannel !== channel || session.rtcClient !== client) return;

          installWebRtcBundleFix();
          try {
            if (session.isTrackPublished && session.localAudioTrack && agoraToken) {
              try {
                await client.unpublish(session.localAudioTrack);
              } catch {
                /* ignore if not currently published on gateway */
              }
              session.isTrackPublished = false;
            }

            if (!session.localAudioTrack && session.agentAudioDest) {
              const track = session.agentAudioDest.stream.getAudioTracks()[0];
              if (track) {
                session.localAudioTrack = AgoraRTC.createCustomAudioTrack({
                  mediaStreamTrack: track,
                });
              }
            }

            if (session.localAudioTrack && !session.isTrackPublished) {
              await within(
                client.publish(session.localAudioTrack),
                AGORA_PUBLISH_TIMEOUT_MS,
                'Publishing your microphone track',
              );
              session.isTrackPublished = true;
            }
          } catch (pubErr) {
            console.error('[ClubhouseVoice] Publishing speaker track after upgrade failed:', pubErr);
            reportVoiceError(
              `Joined stage, but publishing your microphone track failed: ${
                pubErr instanceof Error ? pubErr.message : String(pubErr)
              }`,
            );
          }
        }

        if (session.humanGain) {
          session.humanGain.gain.value = session.state.isMuted ? 0 : 1;
        }
        await syncAgoraTrackMute();
      };

      const prev = session.speakerUpgradePromise ?? Promise.resolve();
      const next = prev.then(runUpgrade, runUpgrade).finally(() => {
        if (session.speakerUpgradePromise === next) {
          session.speakerUpgradePromise = null;
        }
      });
      session.speakerUpgradePromise = next;
      return next;
    },
    [
      reportVoiceError,
      seedLiveUsers,
      session,
      syncAgoraTrackMute,
      updateLiveUser,
      upsertRoomDetailsUser,
    ],
  );

  const demoteFromSpeaker = useCallback(async (): Promise<void> => {
    const myId = session.myProfile?.userId ?? null;
    session.isSpeaker = false;
    session.promotedAt = 0;
    if (session.humanGain) {
      session.humanGain.gain.value = 0;
    }
    if (myId != null) {
      upsertRoomDetailsUser(myId, { is_speaker: false, is_moderator: false });
      updateLiveUser(myId, {
        isSpeaker: false,
        isModerator: false,
        handRaised: false,
        isMuted: true,
      });
    }
    session.patch({
      isMuted: true,
      handRaised: false,
      speakerInvite: null,
    });
    if (session.rtcClient && session.localAudioTrack && session.isTrackPublished) {
      try {
        await session.rtcClient.unpublish(session.localAudioTrack);
      } catch {
        /* best effort */
      }
      session.isTrackPublished = false;
    }
  }, [session, updateLiveUser, upsertRoomDetailsUser]);

  /**
   * Synchronize a polled `/get_channel` snapshot with `liveUsers` and detect any
   * stage promotion or demotion whose PubNub event was missed.
   */
  const syncChannelDetails = useCallback(
    (details: Channel) => {
      session.roomDetails = details;
      seedLiveUsers(details.users ?? []);
      const myId = session.myProfile?.userId ?? null;
      if (myId != null) {
        const meInRoom = details.users?.find((u) => Number(u.user_id) === Number(myId));
        if (meInRoom) {
          if (meInRoom.is_speaker && !session.isSpeaker) {
            void upgradeToSpeaker();
          } else if (
            meInRoom.is_speaker === false &&
            session.isSpeaker &&
            Date.now() - session.promotedAt >= 15_000
          ) {
            void demoteFromSpeaker();
          }
        }
      }
      if (
        details.handraise_queue_setting !== undefined ||
        details.is_handraise_enabled !== undefined
      ) {
        session.patch({
          ...(details.handraise_queue_setting !== undefined
            ? { handraiseQueueSetting: details.handraise_queue_setting }
            : {}),
          ...(details.is_handraise_enabled !== undefined
            ? { isHandraiseEnabled: details.is_handraise_enabled }
            : {}),
        });
      }
    },
    [demoteFromSpeaker, seedLiveUsers, session, upgradeToSpeaker],
  );

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
    const channel = session.state.activeChannel;
    if (!channel) return;
    const nextMuteState = !session.state.isMuted;

    if (!nextMuteState && session.audioCtx?.state === 'suspended') {
      void session.audioCtx.resume().catch(() => {});
    }
    if (!session.humanGain && session.audioCtx && session.agentAudioDest) {
      const humanGain = session.audioCtx.createGain();
      humanGain.gain.value = 0;
      humanGain.connect(session.agentAudioDest);
      session.humanGain = humanGain;
    }

    // Flip local state and gain immediately so the button responds on every click
    // and never gets stuck behind async mic acquisition or token renewal.
    if (session.humanGain) {
      session.humanGain.gain.value = nextMuteState ? 0 : 1;
    }
    session.patch({ isMuted: nextMuteState });
    if (session.myProfile?.userId != null) {
      updateLiveUser(session.myProfile.userId, { isMuted: nextMuteState });
    }

    if (!nextMuteState) {
      if (session.audioCtx?.state === 'suspended') {
        await within(session.audioCtx.resume(), AUDIO_RESUME_TIMEOUT_MS, 'Resuming audio').catch(
          () => {},
        );
      }
      const hasLiveMic =
        Boolean(session.physicalMicStream) &&
        session.physicalMicStream!.getAudioTracks().some((t) => t.readyState === 'live');
      if ((!hasLiveMic || !session.micSource) && session.audioCtx && session.agentAudioDest) {
        const connected = await connectPhysicalMicrophone(5000);
        if (!connected) {
          const msg =
            'Physical microphone could not be opened — unmuted on stage for agent/room audio only.';
          session.reportedVoiceErrors.delete(msg);
          reportVoiceError(msg);
        }
      }

      if (session.speakerUpgradePromise) {
        await session.speakerUpgradePromise;
      } else if (session.isSpeaker && !session.isTrackPublished) {
        await upgradeToSpeaker(undefined, { preserveMute: true });
      }
      if (session.humanGain) {
        session.humanGain.gain.value = session.state.isMuted ? 0 : 1;
      }
    }

    await syncAgoraTrackMute();
  };

  // 4. Raise/Lower hand
  const raiseHand = async (raised: boolean): Promise<'raised' | 'lowered' | 'queued' | 'upgraded' | 'failed'> => {
    const channel = session.state.activeChannel;
    if (!channel) return 'failed';
    session.patch({ error: null });
    const isOpenStage =
      session.state.handraiseQueueSetting === 0 ||
      (session.state.isHandraiseEnabled === false && session.state.handraiseQueueSetting !== 1);
    if (raised && isOpenStage) {
      // Open Stage rooms (`handraise_queue_setting: 0`) have `is_handraise_enabled: false`
      // and step onto stage (or into the host's auto-mod queue) via `/become_speaker`.
      return acceptSpeakerInvite();
    }
    try {
      await setClubhouseHand(channel, raised);
      session.patch({ handRaised: raised });
      if (session.myProfile?.userId != null) {
        updateLiveUser(session.myProfile.userId, { handRaised: raised });
      }
      return raised ? 'raised' : 'lowered';
    } catch (err) {
      if (!raised && isOpenStage) {
        // Lowering a queued Open Stage request locally even if `/audience_reply` is off.
        session.patch({ handRaised: false });
        if (session.myProfile?.userId != null) {
          updateLiveUser(session.myProfile.userId, { handRaised: false });
        }
        return 'lowered';
      }
      const errMsg = err instanceof Error ? err.message : String(err);
      session.patch({ error: errMsg });
      console.error('Failed to update hand raise state:', err);
      return 'failed';
    }
  };

  // 5. Accept speaker invitation (or join / request an Open Stage)
  const acceptSpeakerInvite = async (): Promise<'upgraded' | 'queued' | 'failed'> => {
    const channel = session.state.activeChannel;
    if (!channel) return 'failed';
    session.patch({ error: null });
    try {
      const res = await acceptClubhouseSpeaker(channel);
      if (res.queued) {
        // Clubhouse routed `/become_speaker` to the host's queue (`channel_routed_to_queue`),
        // e.g. when `user_capabilities.auto_mod.must_request_stage` is true. Reflect the
        // pending request without firing a redundant `/join_channel` probe or error banner.
        session.patch({ handRaised: true, speakerInvite: null, error: null });
        if (session.myProfile?.userId != null) {
          updateLiveUser(session.myProfile.userId, { handRaised: true });
        }
        return 'queued';
      }
      await upgradeToSpeaker(res);
      return 'upgraded';
    } catch (err) {
      const rawMsg = err instanceof Error ? err.message : String(err);
      if (
        rawMsg.includes('channel_routed_to_queue') ||
        rawMsg.includes('Your request was sent to the host')
      ) {
        session.patch({ handRaised: true, speakerInvite: null, error: null });
        if (session.myProfile?.userId != null) {
          updateLiveUser(session.myProfile.userId, { handRaised: true });
        }
        return 'queued';
      }
      // When a moderator accepts a raised hand from the handraise queue, Clubhouse
      // promotes the account to speaker on the server immediately (sending
      // `add_speaker` on PubNub) without waiting for `/become_speaker`. Calling
      // `/become_speaker` afterwards returns `400 {"success":false,"error_message":""}`
      // because the account is *already* on stage. Probe `/join_channel` to check
      // if we are already a speaker; if so, complete the stage upgrade instead of
      // surfacing a false error.
      try {
        const fresh = await joinClubhouseChannel(channel);
        const myId = fresh.user_id ?? session.myProfile?.userId ?? null;
        const alreadyOnStage = Boolean(
          session.isSpeaker ||
            (myId != null &&
              fresh.users?.some((u) => Number(u.user_id) === Number(myId) && u.is_speaker)),
        );
        if (alreadyOnStage) {
          await upgradeToSpeaker(fresh);
          return 'upgraded';
        }
      } catch {
        /* fall through to original error */
      }
      const errMsg = rawMsg.includes('"error_message":""')
        ? 'You need a moderator invitation (or an accepted hand raise) to join the stage.'
        : rawMsg;
      session.patch({ error: errMsg });
      console.error('Failed to accept speaker invite:', err);
      return 'failed';
    }
  };

  // 6. Dismiss speaker invite
  const dismissSpeakerInvite = () => session.patch({ speakerInvite: null });

  // 7. Post a comment chat message to the room
  /**
   * Through the room's `ChatSender`: messages are spaced, a 429 is retried instead of
   * lost, and with `dedupe` (the agent) a line already posted is skipped rather than
   * refused by Clubhouse. Resolves `'duplicate'` / `'dropped'` without sending;
   * rejects with Clubhouse's own error once retries are spent.
   */
  const sendComment = async (
    text: string,
    opts?: { dedupe?: boolean },
  ): Promise<'sent' | 'duplicate' | 'dropped'> => {
    if (!session.pubnub || !activeChannel || !text) return 'dropped';
    const channel = activeChannel;
    if (opts?.dedupe && session.chatSender.wasPosted(channel, text)) return 'duplicate';

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
        // The sender spaces these; a fixed 300 ms never cleared Clubhouse's limit.
        const result = await session.chatSender.post(channel, chunk);
        if (result === 'dropped') return 'dropped';
      }
      // A reply sent as several chunks is remembered whole, so the repeat check
      // can see the line it was.
      if (chunks.length > 1) session.chatSender.remember(channel, text);
      return 'sent';
    } catch (err2) {
      console.error('Failed to publish comment:', err2);
      throw err2;
    }
  };

  // 8. Send an emoji reaction
  /**
   * Through Clubhouse's own `/emoji_reaction`. This used to float the emoji locally
   * and publish an invented `react` message straight onto the PubNub channel, which
   * Clubhouse never saw: the sender watched their reaction rise and nobody else in
   * the room got it. Floated on success, so a refusal shows nothing here either —
   * it throws, and the pane says why.
   */
  const sendReaction = async (emoji: string) => {
    if (!activeChannel) return;
    await sendClubhouseReaction(activeChannel, emoji);
    floatReaction(emoji);
  };

  const sendGif = async (giphyId: string, targetUserId?: number) => {
    if (!activeChannel) return;
    await sendClubhouseGif(activeChannel, giphyId, targetUserId);
    // Float our own locally; Clubhouse echoes it back on the room channel (and the
    // echo is suppressed because its sender id is us).
    floatReaction('', `https://media.giphy.com/media/${giphyId}/giphy.gif`, '', targetUserId);
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
      // Duck the room music under the agent's voice, or nobody hears the reply.
      if (session.music) session.music.gain.gain.value = session.music.volume * MUSIC_DUCK;

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

      // Read from `session.state` rather than render-time closures so a long-running
      // turn queue (`drainAgentQueue`) always sees the live mute state and only
      // restores the channel mute if the operator did not unmute while the agent spoke.
      const channel = session.state.activeChannel;
      const wasMuted = session.state.isMuted;
      if (session.speakerUpgradePromise) {
        await session.speakerUpgradePromise;
      } else if (session.isSpeaker && !session.isTrackPublished) {
        await upgradeToSpeaker(undefined, { preserveMute: true });
      }
      if (wasMuted && channel) {
        await syncAgoraTrackMute();
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
        if (session.music) session.music.gain.gain.value = session.music.volume;
        // Not while music is playing (music restores the mute when it stops), and not
        // if the operator unmuted their own microphone while the agent was speaking.
        if (
          wasMuted &&
          session.state.isMuted &&
          channel &&
          session.state.activeChannel === channel &&
          !session.music
        ) {
          await syncAgoraTrackMute();
        }
      }
    },
    [reportVoiceError, session, syncAgoraTrackMute, upgradeToSpeaker],
  );

  /** Stop the room music, restoring the channel's mute if the music opened it. */
  const stopRoomMusic = useCallback(async () => {
    const music = session.music;
    if (!music) return;
    const channel = session.state.activeChannel;
    session.stopMusic();
    // Not mid-sentence (the agent restores the mute when it finishes) and not if
    // the operator unmuted their own microphone while the song was playing.
    if (music.wasMuted && session.state.isMuted && channel && !session.isAgentSpeaking) {
      await syncAgoraTrackMute();
    }
  }, [session, syncAgoraTrackMute]);

  /**
   * Play a song *into the room*.
   *
   * It goes where the agent's voice goes — `agentAudioDest`, the published track —
   * and through a `clubhouse-music` mixer strip so the operator hears it routed like
   * every other sound in the app (never a private context; see the audio module). A
   * gain node in front of both is what lets the agent's voice duck it.
   */
  const playRoomMusic = useCallback(
    async (url: string, title: string) => {
      const ctx = session.audioCtx;
      const dest = session.agentAudioDest;
      if (!ctx || !dest) {
        reportVoiceError('Join a room before playing music into it.');
        return;
      }
      const previous = session.music;
      const channel = session.state.activeChannel;
      // A replaced song keeps the original mute state: the channel is already open.
      const wasMuted = previous ? previous.wasMuted : session.state.isMuted;
      session.stopMusic();

      if (session.speakerUpgradePromise) {
        await session.speakerUpgradePromise;
      } else if (session.isSpeaker && !session.isTrackPublished) {
        await upgradeToSpeaker(undefined, { preserveMute: true });
      }

      const el = new Audio();
      el.crossOrigin = 'anonymous';
      el.src = url;
      const source = ctx.createMediaElementSource(el);
      const gain = ctx.createGain();
      gain.gain.value = session.isAgentSpeaking ? MUSIC_VOLUME * MUSIC_DUCK : MUSIC_VOLUME;
      source.connect(gain);
      gain.connect(dest);
      mixer.declareStrip({ id: MUSIC_STRIP, label: 'Clubhouse_DJ Music', icon: '🎧' });
      const strip = mixer.connectStrip(MUSIC_STRIP);
      gain.connect(strip.input);
      session.music = {
        el,
        stream: null,
        source,
        gain,
        strip,
        volume: MUSIC_VOLUME,
        wasMuted,
        mode: 'file',
        monitor: true,
      };
      session.patch({
        music: {
          title,
          paused: false,
          volume: MUSIC_VOLUME,
          mode: 'file',
          monitor: true,
        },
      });
      el.onended = () => {
        if (session.handlers.onMusicEnded) {
          session.handlers.onMusicEnded();
        } else {
          void stopRoomMusic();
        }
      };

      // Like the agent's voice: a muted channel publishes nothing, so open it for the
      // song. The human microphone stays behind its own gain either way.
      if (channel) {
        await syncAgoraTrackMute();
      }
      try {
        if (ctx.state === 'suspended') await ctx.resume();
        await el.play();
      } catch (e) {
        if (e instanceof DOMException && e.name === 'NotAllowedError') {
          // Autoplay needs one user click in the window: keep the track loaded and
          // ready on the DJ deck so pressing Resume starts it immediately.
          session.patch({
            music: {
              title,
              paused: true,
              volume: MUSIC_VOLUME,
              mode: 'file',
              monitor: true,
            },
          });
          return;
        }
        reportVoiceError(`Couldn't play "${title}": ${e instanceof Error ? e.message : String(e)}`);
        await stopRoomMusic();
      }
    },
    [session, reportVoiceError, stopRoomMusic, syncAgoraTrackMute, upgradeToSpeaker],
  );

  const controlRoomMusic = useCallback(
    (action: 'pause' | 'resume' | 'volume' | 'step', value?: number) => {
      const music = session.music;
      const current = session.state.music;
      if (!music || !current) return;
      if (action === 'pause') {
        if (music.el) {
          music.el.pause();
        } else {
          music.gain.gain.value = 0;
        }
        session.patch({ music: { ...current, paused: true } });
      } else if (action === 'resume') {
        if (music.el) {
          void music.el.play().catch(() => {});
        } else {
          music.gain.gain.value = session.isAgentSpeaking ? music.volume * MUSIC_DUCK : music.volume;
        }
        session.patch({ music: { ...current, paused: false } });
      } else if (value != null && Number.isFinite(value)) {
        const next = action === 'step' ? music.volume + value : value;
        music.volume = Math.min(1, Math.max(0, next));
        if (!session.isAgentSpeaking && !current.paused) {
          music.gain.gain.value = music.volume;
        }
        session.patch({ music: { ...current, volume: music.volume } });
      }
    },
    [session],
  );

  /** Toggle whether the active Clubhouse_DJ source is also monitored locally to headphones. */
  const setDjMonitor = useCallback(
    (enabled: boolean) => {
      const music = session.music;
      const current = session.state.music;
      if (!music || !current) return;
      if (enabled && !music.monitor) {
        mixer.declareStrip({ id: MUSIC_STRIP, label: 'Clubhouse_DJ Music', icon: '🎧' });
        const strip = music.strip ?? mixer.connectStrip(MUSIC_STRIP);
        try {
          music.gain.connect(strip.input);
        } catch {
          /* already connected */
        }
        music.strip = strip;
        music.monitor = true;
      } else if (!enabled && music.monitor) {
        if (music.strip) {
          try {
            music.gain.disconnect(music.strip.input);
          } catch {
            /* ignore */
          }
          music.strip.release();
          music.strip = null;
        }
        music.monitor = false;
      }
      session.patch({ music: { ...current, monitor: enabled } });
    },
    [session],
  );

  /**
   * Capture system, tab, or application audio (Spotify, YouTube, browser tab, desktop)
   * and route it into the Clubhouse room stage (`agentAudioDest`) and optional headphone monitor.
   */
  const startDjTabCapture = useCallback(
    async (options?: { monitor?: boolean }) => {
      const ctx = session.audioCtx;
      const dest = session.agentAudioDest;
      if (!ctx || !dest) {
        reportVoiceError('Join a room before starting Clubhouse_DJ audio capture.');
        return;
      }
      if (!navigator.mediaDevices?.getDisplayMedia) {
        reportVoiceError('Tab/system audio capture is not supported in this environment.');
        return;
      }
      try {
        const displayStream = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: {
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          } as MediaTrackConstraints,
        });
        // Clubhouse is audio-only: stop any video track right away so no camera/screen frames run.
        for (const vt of displayStream.getVideoTracks()) {
          vt.stop();
        }
        const audioTracks = displayStream.getAudioTracks();
        if (audioTracks.length === 0) {
          reportVoiceError(
            'No audio track was shared — make sure "Share tab/system audio" is checked in the picker.',
          );
          return;
        }

        const previous = session.music;
        const wasMuted = previous ? previous.wasMuted : session.state.isMuted;
        session.stopMusic();

        if (session.speakerUpgradePromise) {
          await session.speakerUpgradePromise;
        } else if (session.isSpeaker && !session.isTrackPublished) {
          await upgradeToSpeaker(undefined, { preserveMute: true });
        }

        if (ctx.state === 'suspended') await ctx.resume();
        const audioOnlyStream = new MediaStream(audioTracks);
        const source = ctx.createMediaStreamSource(audioOnlyStream);
        const gain = ctx.createGain();
        const vol = 0.85;
        gain.gain.value = session.isAgentSpeaking ? vol * MUSIC_DUCK : vol;
        source.connect(gain);
        gain.connect(dest);

        const shouldMonitor = options?.monitor ?? false;
        mixer.declareStrip({ id: MUSIC_STRIP, label: 'Clubhouse_DJ Music', icon: '🎧' });
        let strip = null;
        if (shouldMonitor) {
          strip = mixer.connectStrip(MUSIC_STRIP);
          gain.connect(strip.input);
        }

        const trackLabel = audioTracks[0].label || 'Tab / System Audio';
        const title = `🖥️ ${trackLabel}`;
        audioTracks[0].onended = () => {
          void stopRoomMusic();
        };

        session.music = {
          el: null,
          stream: audioOnlyStream,
          source,
          gain,
          strip,
          volume: vol,
          wasMuted,
          mode: 'capture',
          monitor: shouldMonitor,
        };
        session.patch({
          music: {
            title,
            paused: false,
            volume: vol,
            mode: 'capture',
            monitor: shouldMonitor,
          },
        });
        await syncAgoraTrackMute();
      } catch (err) {
        const name = err instanceof DOMException ? err.name : '';
        if (name !== 'NotAllowedError' && name !== 'AbortError') {
          reportVoiceError(
            `Could not capture tab/system audio: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    },
    [reportVoiceError, session, stopRoomMusic, syncAgoraTrackMute, upgradeToSpeaker],
  );

  /**
   * Route an auxiliary audio input device (e.g. `Clubhouse_DJ_Mic`, virtual cable, or line-in)
   * directly into the Clubhouse stage mix (`agentAudioDest`) and optional headphone monitor,
   * while keeping your regular voice microphone separate for talk-over.
   */
  const startDjDeviceCapture = useCallback(
    async (deviceId: string, label?: string, options?: { monitor?: boolean }) => {
      const ctx = session.audioCtx;
      const dest = session.agentAudioDest;
      if (!ctx || !dest) {
        reportVoiceError('Join a room before connecting a Clubhouse_DJ input device.');
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        reportVoiceError('MediaDevices API is not available.');
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
          },
        });
        const audioTracks = stream.getAudioTracks();
        if (audioTracks.length === 0) {
          reportVoiceError('Selected DJ input device provided no audio track.');
          return;
        }

        const previous = session.music;
        const wasMuted = previous ? previous.wasMuted : session.state.isMuted;
        session.stopMusic();

        if (session.speakerUpgradePromise) {
          await session.speakerUpgradePromise;
        } else if (session.isSpeaker && !session.isTrackPublished) {
          await upgradeToSpeaker(undefined, { preserveMute: true });
        }

        if (ctx.state === 'suspended') await ctx.resume();
        const source = ctx.createMediaStreamSource(stream);
        const gain = ctx.createGain();
        const vol = 0.85;
        gain.gain.value = session.isAgentSpeaking ? vol * MUSIC_DUCK : vol;
        source.connect(gain);
        gain.connect(dest);

        const shouldMonitor = options?.monitor ?? true;
        mixer.declareStrip({ id: MUSIC_STRIP, label: 'Clubhouse_DJ Music', icon: '🎧' });
        let strip = null;
        if (shouldMonitor) {
          strip = mixer.connectStrip(MUSIC_STRIP);
          gain.connect(strip.input);
        }

        const resolvedLabel = label || audioTracks[0].label || 'Clubhouse_DJ Input';
        const title = `🎚️ ${resolvedLabel}`;
        audioTracks[0].onended = () => {
          void stopRoomMusic();
        };

        session.music = {
          el: null,
          stream,
          source,
          gain,
          strip,
          volume: vol,
          wasMuted,
          mode: 'device',
          monitor: shouldMonitor,
        };
        session.patch({
          music: {
            title,
            paused: false,
            volume: vol,
            mode: 'device',
            monitor: shouldMonitor,
          },
        });
        await syncAgoraTrackMute();
      } catch (err) {
        reportVoiceError(
          `Could not open DJ input device: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [reportVoiceError, session, stopRoomMusic, syncAgoraTrackMute, upgradeToSpeaker],
  );

  /** Read through the session, not render state: the turn queue outlives renders. */
  const getRoomMusic = useCallback(() => session.state.music, [session]);

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
          const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
          const detail = typeof body?.detail === 'string' ? `: ${body.detail}` : '';
          throw new Error(`TTS preview failed (${res.status})${detail}`);
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

  /**
   * What the agent's ears are doing *right now*.
   *
   * Pulled on the pane's own timer rather than pushed into React state: the VAD
   * writes `earsLevel` fifty times a second, and putting that through `patch` would
   * re-render the whole pane at 50 Hz. Same shape as `getNetworkInsights`.
   */
  const getEarsHealth = useCallback(
    (): EarsHealth => ({
      level: session.earsLevel,
      speechLevel: SPEECH_LEVEL,
      tracksConnected: session.earsTracks,
      recorderState: session.sttCapture ? 'recording' : null,
      contextState: session.audioCtx?.state ?? null,
      restarts: session.earsRestarts,
      lastHeardAt: session.lastHeardAt,
    }),
    [session],
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
    liveCaption,
    reactionOptions,
    handraiseQueueSetting,
    isHandraiseEnabled,
    music,
    playAgentAudio,
    previewTtsVoice,
    stopAgentAudio,
    playRoomMusic,
    stopRoomMusic,
    controlRoomMusic,
    getRoomMusic,
    startDjTabCapture,
    startDjDeviceCapture,
    setDjMonitor,
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
    sendGif,
    seedLiveUsers,
    syncChannelDetails,
    getNetworkInsights,
    getEarsHealth,
  };
}

/** A live read of the agent's hearing path, for the pane's STT card. */
export interface EarsHealth {
  /** The VAD's decision variable: mean frequency-bin energy, 0-255. */
  level: number;
  /** The line it must cross to count as speech. */
  speechLevel: number;
  /** Remote audio tracks wired into the agent's ears this room. */
  tracksConnected: number;
  /** `null` means no capture tap exists — the watchdog rebuilds one within 2s. */
  recorderState: RecordingState | null;
  contextState: AudioContextState | null;
  /** Times the watchdog has had to rebuild the recorder this room. */
  restarts: number;
  /** `Date.now()` of the last transcript the server accepted; 0 if never. */
  lastHeardAt: number;
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
