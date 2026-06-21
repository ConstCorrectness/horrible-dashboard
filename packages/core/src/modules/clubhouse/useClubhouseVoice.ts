import { useEffect, useRef, useState } from 'react';
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
  JoinChannelResult 
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

export interface PubNubRoomMessage {
  action?: string;
  text?: string;
  body?: string;
  message?: string;
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
  emoji?: string;
  reaction?: string;
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
  
  const rtcClientRef = useRef<IAgoraRTCClient | null>(null);
  const localAudioTrackRef = useRef<IMicrophoneAudioTrack | null>(null);
  const pubnubRef = useRef<PubNub | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  
  const myProfileRef = useRef<{ name: string; photoUrl: string | null; userId: number | null } | null>(null);

  // Leave and cleanup on unmount
  useEffect(() => {
    return () => {
      if (activeChannel) {
        void leaveRoom(activeChannel);
      }
    };
  }, [activeChannel]);

  // 1. Join voice room
  const joinRoom = async (channelName: string) => {
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
      // We MUST pass user_id as the UID so that it matches the Agora token.
      await client.join(
        CLUBCARD_AGORA_APP_ID, 
        channelName, 
        chDetails.token, 
        chDetails.user_id ?? undefined
      );

      // e. Create and publish microphone stream
      const micTrack = await AgoraRTC.createMicrophoneAudioTrack();
      localAudioTrackRef.current = micTrack;
      
      // Publish the microphone stream
      await client.publish(micTrack);
      // Join as listener by default (muted)
      await micTrack.setEnabled(false);
      setIsMuted(true);

      // f. Configure PubNub signaling for live room notifications, chat comments, and reactions
      if (chDetails.pubnub_enable && chDetails.pubnub_token) {
        const pubnub = new PubNub({
          subscribeKey: CLUBCARD_PUBNUB_SUB_KEY,
          publishKey: CLUBCARD_PUBNUB_PUB_KEY,
          authKey: chDetails.pubnub_token,
          userId: String(chDetails.channel_id ?? 'anonymous')
        });
        pubnubRef.current = pubnub;
        
        pubnub.addListener({
          message: (event) => {
            const msg = event.message as PubNubRoomMessage;
            if (!msg) return;

            // Handle incoming chat comments / messages
            if (msg.action === 'post_to_chat' || msg.text || msg.body || msg.message) {
              const text = msg.text || msg.body || msg.message;
              if (text && typeof text === 'string') {
                const sender = msg.user_profile || msg.user || {};
                setComments((prev) => [
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

            // Handle incoming emoji reactions
            if (msg.action === 'react' || msg.emoji || msg.reaction) {
              const emoji = msg.emoji || msg.reaction;
              if (emoji && typeof emoji === 'string') {
                const reactionId = String(event.timetoken || Math.random());
                const x = 15 + Math.random() * 70; // Float horizontally between 15% and 85% width
                const y = 80 + Math.random() * 10;
                
                setActiveReactions((prev) => [...prev, { id: reactionId, emoji, x, y }]);
                
                // Clear reaction after animation finishes (3 seconds)
                setTimeout(() => {
                  setActiveReactions((prev) => prev.filter((r) => r.id !== reactionId));
                }, 3000);
              }
            }
          }
        });
        pubnub.subscribe({ channels: [channelName] });
      }

      // g. Establish backend heartbeat ping loop (every 30s)
      // Send the first ping immediately to register presence on the Clubhouse backend
      void pingClubhouseChannel(channelName).catch((err) => {
        console.error('Initial heartbeat ping failed:', err);
      });

      pingIntervalRef.current = setInterval(async () => {
        try {
          await pingClubhouseChannel(channelName);
        } catch (err) {
          console.error('Heartbeat ping failed:', err);
        }
      }, 30000);

      setActiveChannel(channelName);
      setJoined(true);
      setHandRaised(false);
      setComments([]);
      setActiveReactions([]);
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
    // Clear ping interval
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }

    // Stop and close mic track
    if (localAudioTrackRef.current) {
      try {
        localAudioTrackRef.current.stop();
        localAudioTrackRef.current.close();
      } catch (err) {
        console.error('Error stopping mic track:', err);
      }
      localAudioTrackRef.current = null;
    }

    // Leave Agora
    if (rtcClientRef.current) {
      try {
        await rtcClientRef.current.leave();
      } catch (err) {
        console.error('Error leaving Agora client:', err);
      }
      rtcClientRef.current = null;
    }

    // Unsubscribe from PubNub
    if (pubnubRef.current) {
      try {
        pubnubRef.current.unsubscribeAll();
      } catch (err) {
        console.error('Error unsubscribing PubNub:', err);
      }
      pubnubRef.current = null;
    }

    // Notify Clubhouse backend
    try {
      await leaveClubhouseChannel(channelName);
    } catch (err) {
      console.warn('Could not notify leave to Clubhouse API:', err);
    }

    setActiveChannel(null);
    setJoined(false);
    setHandRaised(false);
    setComments([]);
    setActiveReactions([]);
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
      // Synchronize state with Clubhouse backend
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
      // Hand lowers automatically when promoted to speaker
      setHandRaised(false);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      setError(errMsg);
      console.error('Failed to accept speaker invite:', err);
    } finally {
      setLoading(false);
    }
  };

  // 6. Post a comment chat message to the room
  const sendComment = async (text: string) => {
    if (!pubnubRef.current || !activeChannel) return;
    const profile = myProfileRef.current;
    const payload = {
      action: 'post_to_chat',
      text: text,
      user_profile: {
        name: profile?.name || 'Anonymous',
        photo_url: profile?.photoUrl || null,
        user_id: profile?.userId || null
      },
      timestamp: Date.now()
    };
    try {
      await pubnubRef.current.publish({
        channel: activeChannel,
        message: payload
      });
    } catch (err) {
      console.error('Failed to publish comment:', err);
    }
  };

  // 7. Send an emoji reaction
  const sendReaction = async (emoji: string) => {
    if (!pubnubRef.current || !activeChannel) return;
    const profile = myProfileRef.current;
    const payload = {
      action: 'react',
      emoji: emoji,
      user_profile: {
        name: profile?.name || 'Anonymous',
        photo_url: profile?.photoUrl || null,
        user_id: profile?.userId || null
      },
      timestamp: Date.now()
    };
    try {
      await pubnubRef.current.publish({
        channel: activeChannel,
        message: payload
      });
    } catch (err) {
      console.error('Failed to publish reaction:', err);
    }
  };

  return { 
    joined, 
    activeChannel, 
    isMuted, 
    handRaised,
    comments,
    activeReactions,
    loading, 
    error, 
    joinRoom, 
    leaveRoom, 
    toggleMute,
    raiseHand,
    acceptSpeakerInvite,
    sendComment,
    sendReaction
  };
}
