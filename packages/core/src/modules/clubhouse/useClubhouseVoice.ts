import { useCallback, useEffect, useRef, useState } from 'react';
import AgoraRTC, { IAgoraRTCClient, IMicrophoneAudioTrack } from 'agora-rtc-sdk-ng';
import PubNub from 'pubnub';
import {
  joinClubhouseChannel,
  leaveClubhouseChannel,
  pingClubhouseChannel,
  muteClubhouseChannel,
  setClubhouseHand,
  acceptClubhouseSpeaker,
  getClubhouseStatus,
  JoinChannelResult,
  ChannelUser,
} from './api';

const CLUBCARD_AGORA_APP_ID = '938d7e95aeaa4f4ca1f416ab40a498d9';
const CLUBCARD_PUBNUB_SUB_KEY = 'sub-c-a4abea84-9ca3-11ea-8e71-f2b83ac9263d';
const CLUBCARD_PUBNUB_PUB_KEY = 'pub-c-6878d382-5ae6-4494-9099-f930f938868b';

export interface ChatComment {
  id: string;
  userName: string;
  userPhoto: string | null;
  text: string;
  timestamp: number;
}

export interface FloatingReaction {
  id: string;
  emoji: string;
  x: number;
  y: number;
}

// Speaker invite notification shown in the room UI
export interface SpeakerInvite {
  moderatorId: number;
  moderatorName: string;
  moderatorPhoto: string | null;
}

// Per-user live state tracked inside the room
export interface LiveUserState {
  userId: number;
  handRaised: boolean;
  isSpeaker: boolean;
  isMuted: boolean;
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

export function useClubhouseVoice() {
  const [joined, setJoined] = useState(false);
  const [activeChannel, setActiveChannel] = useState<string | null>(null);
  const [isMuted, setIsMuted] = useState(false);
  const [handRaised, setHandRaised] = useState(false);
  const [comments, setComments] = useState<ChatComment[]>([]);
  const [activeReactions, setActiveReactions] = useState<FloatingReaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Live room state — dynamically updated from PubNub events
  const [liveUsers, setLiveUsers] = useState<LiveUserState[]>([]);
  // Pending speaker invite (shown as a toast)
  const [speakerInvite, setSpeakerInvite] = useState<SpeakerInvite | null>(null);
  // Map of uid → volume level (0–100) from Agora volume indicator
  const [speakingVolumes, setSpeakingVolumes] = useState<Record<number, number>>({});

  const rtcClientRef = useRef<IAgoraRTCClient | null>(null);
  const localAudioTrackRef = useRef<IMicrophoneAudioTrack | null>(null);
  const pubnubRef = useRef<PubNub | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const volumeIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const myProfileRef = useRef<{ name: string; photoUrl: string | null; userId: number | null } | null>(null);

  // Leave and cleanup on unmount
  useEffect(() => {
    return () => {
      if (activeChannel) {
        void leaveRoom(activeChannel);
      }
    };
  }, [activeChannel]);

  // Helper: update a single user's live state
  const updateLiveUser = useCallback((userId: number, patch: Partial<Omit<LiveUserState, 'userId'>>) => {
    setLiveUsers(prev => {
      const existing = prev.find(u => u.userId === userId);
      if (existing) {
        return prev.map(u => u.userId === userId ? { ...u, ...patch } : u);
      }
      // New user — add with defaults
      return [...prev, { userId, handRaised: false, isSpeaker: false, isMuted: false, ...patch }];
    });
  }, []);

  // Helper: remove a user from live state
  const removeLiveUser = useCallback((userId: number) => {
    setLiveUsers(prev => prev.filter(u => u.userId !== userId));
  }, []);

  // Seed liveUsers from the initial room user list after joining
  const seedLiveUsers = useCallback((users: ChannelUser[]) => {
    const states: LiveUserState[] = users
      .filter(u => u.user_id != null)
      .map(u => ({
        userId: u.user_id!,
        handRaised: false,
        isSpeaker: u.is_speaker ?? false,
        isMuted: false,
      }));
    setLiveUsers(states);
  }, []);

  // 1. Join voice room
  const joinRoom = async (channelName: string, initialUsers?: ChannelUser[]) => {
    setLoading(true);
    setError(null);
    try {
      // Get own profile status first
      const status = await getClubhouseStatus();
      myProfileRef.current = {
        name: status.name || 'Anonymous',
        photoUrl: status.photo_url,
        userId: status.user_id
      };

      // a. Request credentials & tokens from our backend
      const chDetails: JoinChannelResult = await joinClubhouseChannel(channelName);
      if (!chDetails.token) {
        throw new Error('Failed to retrieve Agora token from Clubhouse');
      }

      // b. Initialize Agora Client
      const client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
      rtcClientRef.current = client;

      // c. Listen for remote audio updates
      client.on('user-published', async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === 'audio') {
          const remoteAudioTrack = user.audioTrack;
          remoteAudioTrack?.play();
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
        chDetails.user_id ?? undefined
      );

      // e. Create and publish microphone stream
      const micTrack = await AgoraRTC.createMicrophoneAudioTrack();
      localAudioTrackRef.current = micTrack;
      await client.publish(micTrack);
      // Join as listener by default (muted)
      await micTrack.setEnabled(false);
      setIsMuted(true);

      // e2. Start Agora volume indicator — fires every 200ms with per-user volumes
      client.enableAudioVolumeIndicator();
      client.on('volume-indicator', (volumes) => {
        const map: Record<number, number> = {};
        for (const { uid, level } of volumes) {
          map[uid as number] = level;
        }
        setSpeakingVolumes(map);
      });
      // Clear stale volumes every 600ms in case no volume event fires
      volumeIntervalRef.current = setInterval(() => {
        setSpeakingVolumes(prev => {
          const now: Record<number, number> = {};
          for (const [uid, vol] of Object.entries(prev)) {
            if (vol > 5) now[Number(uid)] = vol;
          }
          return now;
        });
      }, 600);

      // f. Configure PubNub signaling
      if (chDetails.pubnub_enable && chDetails.pubnub_token) {
        const myUserId = myProfileRef.current?.userId;
        const myUserIdStr = myUserId ? String(myUserId) : `anon-${Math.random().toString(36).slice(2, 9)}`;

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
        pubnubRef.current = pubnub;

        console.log('[PubNub] Initialized (PAMv3). userId:', myUserIdStr, 'origin:', chDetails.pubnub_origin);

        pubnub.addListener({
          message: (event) => {
            console.log('[PubNub] RAW message:', { channel: event.channel, message: event.message });

            const msg = event.message as PubNubRoomMessage;
            if (!msg) return;

            const sender = msg.user_profile || msg.user || {};
            const senderId = sender.user_id ?? msg.user_id;

            // --- Room signaling events ---
            const action = msg.action;

            if (action === 'join_channel') {
              // Someone joined the room
              if (senderId != null) {
                updateLiveUser(senderId, {
                  isSpeaker: msg.is_speaker ?? false,
                  isMuted: false,
                });
              }
            } else if (action === 'leave_channel' || action === 'remove_speaker') {
              // Someone left or was removed
              if (senderId != null) {
                removeLiveUser(senderId);
              }
            } else if (action === 'raise_hands' || action === 'hand_raised') {
              if (senderId != null) {
                updateLiveUser(senderId, { handRaised: true });
              }
            } else if (action === 'lower_hands' || action === 'unraise_hands') {
              if (senderId != null) {
                updateLiveUser(senderId, { handRaised: false });
              }
            } else if (action === 'make_speaker' || action === 'accept_speaker_invite') {
              // User was promoted to speaker
              if (senderId != null) {
                updateLiveUser(senderId, { isSpeaker: true, handRaised: false });
              }
            } else if (action === 'mute_speaker' || action === 'update_muted') {
              if (senderId != null) {
                updateLiveUser(senderId, { isMuted: msg.is_muted ?? true });
              }
            } else if (action === 'invite_speaker') {
              // A moderator is inviting us to speak
              const targetId = msg.user_id;
              if (targetId != null && targetId === myUserId) {
                setSpeakerInvite({
                  moderatorId: msg.moderator_id ?? (senderId ?? 0),
                  moderatorName: msg.moderator_name || sender.name || 'A moderator',
                  moderatorPhoto: msg.moderator_photo_url || sender.photo_url || null,
                });
              }
            }

            // --- Chat messages ---
            if (action === 'post_to_chat' || (!action && (msg.text || msg.body || msg.message))) {
              const text = msg.text || msg.body || msg.message;
              if (text && typeof text === 'string') {
                // Skip own messages (shown locally immediately)
                if (senderId != null && myUserId != null && senderId === myUserId) return;
                console.log('[PubNub] Chat message:', text);
                setComments(prev => [
                  ...prev,
                  {
                    id: String(event.timetoken || Math.random()),
                    userName: sender.name || msg.from_name || 'Anonymous',
                    userPhoto: sender.photo_url || msg.from_photo_url || null,
                    text,
                    timestamp: Math.floor((Number(event.timetoken) || Date.now() * 10000) / 10000)
                  }
                ]);
              }
            }

            // --- Emoji reactions ---
            if (action === 'react' || (!action && (msg.emoji || msg.reaction))) {
              const emoji = msg.emoji || msg.reaction;
              if (emoji && typeof emoji === 'string') {
                if (senderId != null && myUserId != null && senderId === myUserId) return;
                const reactionId = String(event.timetoken || Math.random());
                const x = 15 + Math.random() * 70;
                const y = 80 + Math.random() * 10;
                setActiveReactions(prev => [...prev, { id: reactionId, emoji, x, y }]);
                setTimeout(() => {
                  setActiveReactions(prev => prev.filter(r => r.id !== reactionId));
                }, 3000);
              }
            }
          },
          signal: (event) => {
            console.log('[PubNub] Signal:', event);
          },
          status: (statusEvent) => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const s = statusEvent as any;
            console.log('[PubNub] Status:', statusEvent.category, {
              affectedChannels: s.affectedChannels,
              statusCode: s.statusCode,
              error: s.error,
            });
          }
        });

        // Subscribe only to channels in the PAMv3 token grant
        const channelsToSubscribe = [`channel_all.${channelName}`];
        if (myUserId) {
          channelsToSubscribe.push(`users.${myUserId}`);
          channelsToSubscribe.push(`channel_user.${channelName}.${myUserId}`);
        }
        console.log('[PubNub] Subscribing to:', channelsToSubscribe);
        pubnub.subscribe({ channels: channelsToSubscribe });
      } else {
        console.warn('[PubNub] Not initialized — pubnub_enable:', chDetails.pubnub_enable);
      }

      // g. Heartbeat ping loop (every 30s)
      void pingClubhouseChannel(channelName).catch(err => {
        console.error('Initial heartbeat ping failed:', err);
      });
      pingIntervalRef.current = setInterval(async () => {
        try { await pingClubhouseChannel(channelName); }
        catch (err) { console.error('Heartbeat ping failed:', err); }
      }, 30000);

      setActiveChannel(channelName);
      setJoined(true);
      setHandRaised(false);
      setComments([]);
      setActiveReactions([]);
      setSpeakerInvite(null);
      setSpeakingVolumes({});

      // Seed live user state from the room's initial user list
      if (initialUsers) {
        seedLiveUsers(initialUsers);
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      setError(errMsg);
      console.error('Join room failed:', e);
      if (rtcClientRef.current || localAudioTrackRef.current) {
        await leaveRoom(channelName);
      }
    } finally {
      setLoading(false);
    }
  };

  // 2. Leave voice room & cleanup
  const leaveRoom = async (channelName: string) => {
    setLoading(true);

    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (volumeIntervalRef.current) {
      clearInterval(volumeIntervalRef.current);
      volumeIntervalRef.current = null;
    }

    if (localAudioTrackRef.current) {
      try { localAudioTrackRef.current.stop(); localAudioTrackRef.current.close(); }
      catch (err) { console.error('Error stopping mic track:', err); }
      localAudioTrackRef.current = null;
    }

    if (rtcClientRef.current) {
      try { await rtcClientRef.current.leave(); }
      catch (err) { console.error('Error leaving Agora:', err); }
      rtcClientRef.current = null;
    }

    if (pubnubRef.current) {
      try { pubnubRef.current.unsubscribeAll(); }
      catch (err) { console.error('Error unsubscribing PubNub:', err); }
      pubnubRef.current = null;
    }

    try { await leaveClubhouseChannel(channelName); }
    catch (err) { console.warn('Could not notify Clubhouse leave:', err); }

    setActiveChannel(null);
    setJoined(false);
    setHandRaised(false);
    setComments([]);
    setActiveReactions([]);
    setLiveUsers([]);
    setSpeakerInvite(null);
    setSpeakingVolumes({});
    setLoading(false);
  };

  // 3. Mute/Unmute microphone
  const toggleMute = async () => {
    if (!activeChannel) return;
    const nextMuteState = !isMuted;
    try {
      if (localAudioTrackRef.current) {
        await localAudioTrackRef.current.setEnabled(!nextMuteState);
      }
      setIsMuted(nextMuteState);
      await muteClubhouseChannel(activeChannel, nextMuteState);
    } catch (err) {
      console.error('Failed to toggle mic state:', err);
    }
  };

  // 4. Raise/Lower hand
  const raiseHand = async (raised: boolean) => {
    if (!activeChannel) return;
    setLoading(true);
    try {
      await setClubhouseHand(activeChannel, raised);
      setHandRaised(raised);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      setError(errMsg);
      console.error('Failed to update hand raise state:', err);
    } finally {
      setLoading(false);
    }
  };

  // 5. Accept speaker invitation
  const acceptSpeakerInvite = async (moderatorId: number) => {
    if (!activeChannel) return;
    setLoading(true);
    try {
      await acceptClubhouseSpeaker(activeChannel, moderatorId);
      setHandRaised(false);
      setSpeakerInvite(null);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      setError(errMsg);
      console.error('Failed to accept speaker invite:', err);
    } finally {
      setLoading(false);
    }
  };

  // 6. Dismiss speaker invite
  const dismissSpeakerInvite = () => setSpeakerInvite(null);

  // 7. Post a comment chat message to the room
  const sendComment = async (text: string) => {
    if (!pubnubRef.current || !activeChannel) return;
    const profile = myProfileRef.current;
    const commentId = 'my-msg-' + Math.random().toString(36).slice(2, 9);
    const timestamp = Date.now();

    // Add locally for instant feedback
    setComments(prev => [...prev, {
      id: commentId,
      userName: profile?.name || 'Anonymous',
      userPhoto: profile?.photoUrl || null,
      text,
      timestamp
    }]);

    const payload = {
      action: 'post_to_chat',
      text,
      user_profile: {
        name: profile?.name || 'Anonymous',
        photo_url: profile?.photoUrl || null,
        user_id: profile?.userId || null
      },
      timestamp
    };
    try {
      await pubnubRef.current.publish({ channel: `channel_all.${activeChannel}`, message: payload });
    } catch {
      try {
        await pubnubRef.current.publish({ channel: activeChannel, message: payload });
      } catch (err2) {
        console.error('Failed to publish comment:', err2);
      }
    }
  };

  // 8. Send an emoji reaction
  const sendReaction = async (emoji: string) => {
    if (!pubnubRef.current || !activeChannel) return;
    const profile = myProfileRef.current;
    const reactionId = 'my-react-' + Math.random().toString(36).slice(2, 9);
    const x = 15 + Math.random() * 70;
    const y = 80 + Math.random() * 10;

    setActiveReactions(prev => [...prev, { id: reactionId, emoji, x, y }]);
    setTimeout(() => {
      setActiveReactions(prev => prev.filter(r => r.id !== reactionId));
    }, 3000);

    const payload = {
      action: 'react',
      emoji,
      user_profile: {
        name: profile?.name || 'Anonymous',
        photo_url: profile?.photoUrl || null,
        user_id: profile?.userId || null
      },
      timestamp: Date.now()
    };
    try {
      await pubnubRef.current.publish({ channel: `channel_all.${activeChannel}`, message: payload });
    } catch {
      try {
        await pubnubRef.current.publish({ channel: activeChannel, message: payload });
      } catch (err2) {
        console.error('Failed to publish reaction:', err2);
      }
    }
  };

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
    loading,
    error,
    joinRoom,
    leaveRoom,
    toggleMute,
    raiseHand,
    acceptSpeakerInvite,
    dismissSpeakerInvite,
    sendComment,
    sendReaction,
    seedLiveUsers,
  };
}
