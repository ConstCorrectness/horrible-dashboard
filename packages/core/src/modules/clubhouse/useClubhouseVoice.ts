import { apiUrl } from '../../origin';
import { useCallback, useEffect, useRef, useState } from 'react';
import AgoraRTC, { IAgoraRTCClient, ILocalAudioTrack } from 'agora-rtc-sdk-ng';
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

export interface UseClubhouseVoiceProps {
  onLiveUsersChange?: (users: LiveUserState[]) => void;
  onCommentsChange?: (comments: ChatComment[]) => void;
  onSpeakingVolumesChange?: (volumes: Record<number, number>) => void;
  onTranscribe?: (text: string) => void;
}

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

export function useClubhouseVoice(props?: UseClubhouseVoiceProps) {
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

  // Audio Mixer Refs
  const audioCtxRef = useRef<AudioContext | null>(null);
  const agentAudioDestRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  const sttDestRef = useRef<MediaStreamAudioDestinationNode | null>(null);
  const sttRecorderRef = useRef<MediaRecorder | null>(null);
  const physicalMicStreamRef = useRef<MediaStream | null>(null);
  const humanGainRef = useRef<GainNode | null>(null);

  const rtcClientRef = useRef<IAgoraRTCClient | null>(null);
  const localAudioTrackRef = useRef<ILocalAudioTrack | null>(null);
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
    setComments([]);
    setActiveReactions([]);

    // Fetch historical chat
    try {
      const chatRes = await fetch(apiUrl(`/api/clubhouse/channels/${channelName}/chat`));
      if (chatRes.ok) {
        const chatData = await chatRes.json();
        if (chatData.comments && Array.isArray(chatData.comments)) {
          // Map the API chat format to ChatComment if needed, or assume it's close enough.
          // Clubhouse chat messages are typically structured with user info.
          const historicalComments = chatData.comments.map((c: any) => ({
            id: c.message_id || c.chat_message_id || String(Math.random()),
            userName: c.name || c.user_profile?.name || 'Unknown',
            userPhoto: c.photo_url || c.user_profile?.photo_url || '',
            text: c.message || c.body || '',
            timestamp: c.timestamp || Date.now()
          })).filter((c: ChatComment) => c.text.length > 0);
          setComments(historicalComments.reverse()); // usually oldest first
        }
      }
    } catch (e) {
      console.error('Failed to fetch historical chat:', e);
    }

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
          
          if (remoteAudioTrack && audioCtxRef.current && sttDestRef.current) {
            const track = remoteAudioTrack.getMediaStreamTrack();
            const stream = new MediaStream([track]);
            const source = audioCtxRef.current.createMediaStreamSource(stream);
            source.connect(sttDestRef.current);
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
        chDetails.user_id ?? undefined
      );

      // e. Create Audio Mixer
      const AudioContext = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      
      const dest = audioCtx.createMediaStreamDestination();
      agentAudioDestRef.current = dest;
      
      const sttDest = audioCtx.createMediaStreamDestination();
      sttDestRef.current = sttDest;
      
      // Start STT Recorder
      try {
        const startRecordingChunk = () => {
          if (!sttDestRef.current) return;
          const recorder = new MediaRecorder(sttDestRef.current.stream);
          sttRecorderRef.current = recorder;
          
          recorder.ondataavailable = async (e) => {
            if (e.data.size > 0 && props?.onTranscribe) {
              const formData = new FormData();
              formData.append('file', new File([e.data], 'chunk.webm', { type: e.data.type || 'audio/webm' }));
              try {
                const res = await fetch(apiUrl('/api/agent/stt'), { method: 'POST', body: formData });
                const json = await res.json();
                if (json.text && json.text.trim().length > 0) {
                  props.onTranscribe(json.text);
                }
              } catch (err) {
                console.error('STT failed:', err);
              }
            }
          };
          
          recorder.start();
          
          // Stop and restart after 5 seconds to ensure a fresh WebM header
          setTimeout(() => {
            if (sttRecorderRef.current === recorder && recorder.state === 'recording') {
              recorder.stop();
              startRecordingChunk();
            }
          }, 5000);
        };
        
        startRecordingChunk();
      } catch (err) {
        console.error('Failed to start STT recorder:', err);
      }
      
      // Get physical mic (Optional, handle missing permissions or timeouts gracefully)
      try {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          throw new Error('MediaDevices API not available (requires secure context).');
        }
        
        const micStream = await Promise.race([
          navigator.mediaDevices.getUserMedia({ audio: true }),
          new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Microphone permission timeout')), 3000))
        ]);
        
        physicalMicStreamRef.current = micStream;
        const micSource = audioCtx.createMediaStreamSource(micStream);
        
        const humanGain = audioCtx.createGain();
        humanGain.gain.value = 0; // muted by default
        humanGainRef.current = humanGain;
        
        micSource.connect(humanGain);
        humanGain.connect(dest);
        humanGain.connect(sttDest);
      } catch (err) {
        console.warn('Could not access physical microphone, continuing as listener:', err);
      }

      // Create and publish mixed microphone stream
      const mixedTrack = AgoraRTC.createCustomAudioTrack({
        mediaStreamTrack: dest.stream.getAudioTracks()[0]
      });
      localAudioTrackRef.current = mixedTrack;
      await client.publish(mixedTrack);
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

        pubnub.addListener({
          message: (event) => {
            const msg = event.message as PubNubRoomMessage;
            if (!msg) return;

            const sender = msg.user_profile || msg.user || {};
            const senderId = sender.user_id ?? msg.user_id;

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
                if (senderId != null && myUserId != null && senderId === myUserId) return;
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
          }
        });

        const channelsToSubscribe = [`channel_all.${channelName}`];
        if (myUserId) {
          channelsToSubscribe.push(`users.${myUserId}`);
          channelsToSubscribe.push(`channel_user.${channelName}.${myUserId}`);
        }
        pubnub.subscribe({ channels: channelsToSubscribe });
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
      localAudioTrackRef.current.close();
      localAudioTrackRef.current = null;
    }
    if (physicalMicStreamRef.current) {
      physicalMicStreamRef.current.getTracks().forEach(t => t.stop());
      physicalMicStreamRef.current = null;
    }
    if (sttRecorderRef.current) {
      sttRecorderRef.current.stop();
      sttRecorderRef.current = null;
    }
    
    if (audioCtxRef.current) {
      audioCtxRef.current.close();
      audioCtxRef.current = null;
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
      if (humanGainRef.current) {
        humanGainRef.current.gain.value = nextMuteState ? 0 : 1;
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
    } catch (err2) {
      console.error('Failed to publish comment:', err2);
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
    } catch (err2) {
      console.error('Failed to publish reaction:', err2);
    }
  };

  // Play Agent Audio through the mixer
  const playAgentAudio = useCallback(async (text: string) => {
    if (!audioCtxRef.current || !agentAudioDestRef.current || !localAudioTrackRef.current) return;
    try {
      const url = `/api/agent/tts?text=${encodeURIComponent(text)}`;
      const res = await fetch(url);
      const arrayBuffer = await res.arrayBuffer();
      const audioBuffer = await audioCtxRef.current.decodeAudioData(arrayBuffer);
      
      const source = audioCtxRef.current.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(agentAudioDestRef.current);
      
      return new Promise<void>((resolve) => {
        source.onended = () => resolve();
        source.start();
      });
    } catch (e) {
      console.error('Failed to play agent audio:', e);
    }
  }, []);

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
    playAgentAudio,
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
