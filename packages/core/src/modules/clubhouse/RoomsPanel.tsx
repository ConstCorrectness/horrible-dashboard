import { useEffect, useRef, useState } from 'react';

import { apiUrl } from '../../origin';
import { useAgentContext } from '../../agent-context';
import { toastsStore } from '../../toasts';
import {
  getClubhouseChannels,
  getClubhouseStatus,
  getClubhouseChannelDetails,
  getClubhouseUserProfile,
  createClubhouseChannel,
  followClubhouseUser,
  unfollowClubhouseUser,
  inviteToClubhouseChannel,
  searchClubhouseUsers,
  getClubhouseFollowing,
  type Channel,
  type ChannelUser,
  type ClubhouseUserProfile,
  type SearchUserResult,
  type FollowUser,
} from './api';
import { useClubhouseVoice } from './useClubhouseVoice';

/**
 * Live Clubhouse rooms panel. Handles searching, joining rooms, active call stage,
 * real-time comments chat, reactions, raising hands, and speaking.
 */
export function RoomsPanel() {
  const [state, setState] = useState<'loading' | 'disconnected' | 'ready' | 'error'>('loading');
  const [channels, setChannels] = useState<Channel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeRoomInfo, setActiveRoomInfo] = useState<Channel | null>(null);
  const [commentText, setCommentText] = useState('');
  const [selectedUser, setSelectedUser] = useState<ClubhouseUserProfile | null>(null);
  const [loadingProfile, setLoadingProfile] = useState(false);

  // Voice Agent States
  const [agentEnabled, setAgentEnabled] = useState(false);
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false);
  const [agentTranscript, setAgentTranscript] = useState<string | null>(null);
  const [myUserId, setMyUserId] = useState<number | null>(null);

  // New states for extended Clubhouse functionality
  const [showStartRoomModal, setShowStartRoomModal] = useState(false);
  const [newRoomTopic, setNewRoomTopic] = useState('');
  const [newRoomPrivacy, setNewRoomPrivacy] = useState<'public' | 'social' | 'private'>('public');
  const [creatingRoom, setCreatingRoom] = useState(false);

  const [followingLoading, setFollowingLoading] = useState(false);

  const [activeTab, setActiveTab] = useState<'rooms' | 'people'>('rooms');
  const [peopleSearchQuery, setPeopleSearchQuery] = useState('');
  const [peopleSearchResults, setPeopleSearchResults] = useState<SearchUserResult[]>([]);
  const [loadingPeople, setLoadingPeople] = useState(false);

  const [showInviteModal, setShowInviteModal] = useState(false);
  const [followingUsers, setFollowingUsers] = useState<FollowUser[]>([]);
  const [loadingFollowing, setLoadingFollowing] = useState(false);
  const [invitedUserIds, setInvitedUserIds] = useState<Set<number>>(new Set());

  // Handlers for extended Clubhouse functionality
  const handleStartRoom = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingRoom(true);
    try {
      const isPrivate = newRoomPrivacy === 'private';
      const isSocialMode = newRoomPrivacy === 'social';
      const res = await createClubhouseChannel(newRoomTopic.trim(), isPrivate, isSocialMode);
      setShowStartRoomModal(false);
      setNewRoomTopic('');
      if (res.channel) {
        const roomInfo: Channel = {
          channel: res.channel,
          topic: newRoomTopic.trim() || 'My New Room',
          num_speakers: 1,
          num_all: 1,
          club: null,
          users: [],
        };
        setActiveRoomInfo(roomInfo);
        void joinRoom(res.channel, roomInfo.users);
      }
    } catch (err) {
      console.error('Failed to start room:', err);
      toastsStore.add('error', 'Failed to start room', String(err));
    } finally {
      setCreatingRoom(false);
    }
  };

  const handleToggleFollow = async () => {
    if (!selectedUser) return;
    setFollowingLoading(true);
    try {
      const isCurrentlyFollowing =
        selectedUser.notification_type !== undefined && selectedUser.notification_type > 0;
      if (isCurrentlyFollowing) {
        await unfollowClubhouseUser(selectedUser.user_id);
        setSelectedUser((prev) =>
          prev
            ? {
                ...prev,
                notification_type: 0,
                num_followers: Math.max(0, prev.num_followers - 1),
              }
            : null,
        );
      } else {
        await followClubhouseUser(selectedUser.user_id);
        setSelectedUser((prev) =>
          prev
            ? {
                ...prev,
                notification_type: 1,
                num_followers: prev.num_followers + 1,
              }
            : null,
        );
      }
    } catch (err) {
      console.error('Failed to toggle follow:', err);
    } finally {
      setFollowingLoading(false);
    }
  };

  const handlePeopleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!peopleSearchQuery.trim()) return;
    setLoadingPeople(true);
    try {
      const res = await searchClubhouseUsers(peopleSearchQuery.trim());
      setPeopleSearchResults(res.users ?? []);
    } catch (err) {
      console.error('Failed to search users:', err);
    } finally {
      setLoadingPeople(false);
    }
  };

  const handleOpenInvite = async () => {
    setShowInviteModal(true);
    setLoadingFollowing(true);
    try {
      const res = await getClubhouseFollowing();
      setFollowingUsers(res.users ?? []);
    } catch (err) {
      console.error('Failed to fetch following users:', err);
    } finally {
      setLoadingFollowing(false);
    }
  };

  const handleSendInvite = async (userId: number) => {
    if (!activeChannel) return;
    try {
      await inviteToClubhouseChannel(activeChannel, userId);
      setInvitedUserIds((prev) => {
        const next = new Set(prev);
        next.add(userId);
        return next;
      });
    } catch (err) {
      console.error('Failed to send invite:', err);
      toastsStore.add('error', 'Failed to send invite', String(err));
    }
  };

  const handleUserClick = async (userId: number | null) => {
    if (!userId) return;
    setLoadingProfile(true);
    try {
      const profile = await getClubhouseUserProfile(userId);
      setSelectedUser(profile);
    } catch (err) {
      console.error('Failed to fetch user profile:', err);
    } finally {
      setLoadingProfile(false);
    }
  };

  const renderProfileOverlay = () => {
    if (!selectedUser) return null;
    const isCurrentUser = selectedUser.user_id === myUserId;
    const isFollowing =
      selectedUser.notification_type !== undefined && selectedUser.notification_type > 0;

    return (
      <div className="ch-profile-overlay" onClick={() => setSelectedUser(null)}>
        <div className="ch-profile-card" onClick={(e) => e.stopPropagation()}>
          <button className="ch-profile-close" onClick={() => setSelectedUser(null)}>
            ✕
          </button>
          <div className="ch-profile-header">
            {selectedUser.photo_url ? (
              <img className="ch-profile-avatar" src={selectedUser.photo_url} alt="" />
            ) : (
              <div className="ch-profile-avatar-placeholder">
                {selectedUser.name?.slice(0, 2).toUpperCase() || '?'}
              </div>
            )}
            <div className="ch-profile-names">
              <h4 className="ch-profile-name">{selectedUser.name}</h4>
              <p className="ch-profile-username">@{selectedUser.username}</p>
              {selectedUser.follows_me && <span className="ch-follows-badge">Follows you</span>}
              {!isCurrentUser && (
                <button
                  className={`ch-profile-follow-btn ${isFollowing ? 'following' : ''}`}
                  onClick={handleToggleFollow}
                  disabled={followingLoading}
                >
                  {followingLoading ? 'Updating...' : isFollowing ? '✓ Following' : '+ Follow'}
                </button>
              )}
            </div>
          </div>

          <div className="ch-profile-stats">
            <div className="ch-profile-stat">
              <span className="ch-profile-stat-val">{selectedUser.num_followers ?? 0}</span>
              <span className="ch-profile-stat-lbl">followers</span>
            </div>
            <div className="ch-profile-stat">
              <span className="ch-profile-stat-val">{selectedUser.num_following ?? 0}</span>
              <span className="ch-profile-stat-lbl">following</span>
            </div>
          </div>

          {selectedUser.bio && <div className="ch-profile-bio">{selectedUser.bio}</div>}

          {(selectedUser.twitter || selectedUser.instagram) && (
            <div className="ch-profile-socials">
              {selectedUser.twitter && (
                <div className="ch-profile-social-item">
                  Twitter: <span>@{selectedUser.twitter}</span>
                </div>
              )}
              {selectedUser.instagram && (
                <div className="ch-profile-social-item">
                  Instagram: <span>@{selectedUser.instagram}</span>
                </div>
              )}
            </div>
          )}

          {selectedUser.invited_by_user_profile && (
            <div className="ch-profile-invited">
              Joined Clubhouse via <strong>{selectedUser.invited_by_user_profile.name}</strong>
            </div>
          )}
        </div>
      </div>
    );
  };

  const chatEndRef = useRef<HTMLDivElement | null>(null);

  const {
    joined,
    activeChannel,
    isMuted,
    handRaised,
    comments,
    activeReactions,
    liveUsers,
    speakerInvite,
    speakingVolumes,
    loading: voiceLoading,
    error: voiceError,
    joinRoom,
    leaveRoom,
    toggleMute,
    playAgentAudio,
    raiseHand,
    acceptSpeakerInvite,
    dismissSpeakerInvite,
    sendComment,
    sendReaction,
  } = useClubhouseVoice({
    onTranscribe: (text) => {
      if (agentEnabled) {
        setAgentTranscript(text);
      }
    }
  });

  const load = async () => {
    setState('loading');
    setError(null);
    try {
      const status = await getClubhouseStatus();
      if (!status.connected) {
        setState('disconnected');
        return;
      }
      setMyUserId(status.user_id);
      const res = await getClubhouseChannels();
      setChannels(res.channels ?? []);
      setState('ready');
    } catch (e) {
      setError(String(e));
      setState('error');
    }
  };

  useEffect(() => {
    void load();
  }, []);

  // Poll active channel details when joined to keep participant lists updated in real-time
  useEffect(() => {
    if (!joined || !activeChannel) return;

    const updateActiveChannel = async () => {
      try {
        const details = await getClubhouseChannelDetails(activeChannel);
        setChannels((prev) => prev.map((ch) => (ch.channel === activeChannel ? details : ch)));
      } catch (err) {
        console.error('Failed to poll active channel details:', err);
      }
    };

    void updateActiveChannel();
    const interval = setInterval(updateActiveChannel, 10000);

    return () => clearInterval(interval);
  }, [joined, activeChannel]);

  // Scroll to bottom of chat when new comments arrive
  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [comments]);

  // Voice Agent Logic
  const triggerAgentResponse = async (text: string) => {
    if (!text || isAgentSpeaking) return;
    try {
      setIsAgentSpeaking(true);
      const req = await fetch(apiUrl('/api/agent/complete'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefix: 'You are an AI in a Clubhouse room. You hear: "' + text + '". Reply concisely as yourself: "', suffix: '"', language: 'text' }),
      });
      const data = await req.json();
      
      if (data.completion) {
        const text = data.completion.trim();
        await sendComment(`🤖 ${text}`);
        await playAgentAudio(text);
      }
      setIsAgentSpeaking(false);
    } catch (e) {
      console.error('Agent response failed:', e);
      setIsAgentSpeaking(false);
    }
  };

  const prevCommentsLengthRef = useRef(0);
  useEffect(() => {
    if (comments.length > prevCommentsLengthRef.current) {
      const newComment = comments[comments.length - 1];
      prevCommentsLengthRef.current = comments.length;
      if (agentEnabled && !newComment.text?.startsWith('🤖')) {
         triggerAgentResponse(newComment.text || '');
      }
    }
  }, [comments, agentEnabled, myUserId, isAgentSpeaking]);

  useEffect(() => {
    if (agentTranscript && agentEnabled) {
      triggerAgentResponse(agentTranscript);
      setAgentTranscript(null);
    }
  }, [agentTranscript, agentEnabled]);

  // Filter channels based on search query
  const filteredChannels = channels.filter((c) => {
    const query = searchQuery.toLowerCase().trim();
    if (!query) return true;
    return (
      (c.topic && c.topic.toLowerCase().includes(query)) ||
      (c.club?.name && c.club.name.toLowerCase().includes(query)) ||
      c.users.some((u) => u.name && u.name.toLowerCase().includes(query))
    );
  });

  // Let the agent read the live rooms currently listed.
  useAgentContext(() => ({
    state,
    rooms: channels.map((c) => ({
      topic: c.topic || null,
      club: c.club?.name ?? null,
      speakers: c.num_speakers ?? 0,
      total: c.num_all ?? 0,
      people: c.users.map((u) => u.name).filter(Boolean),
    })),
  }));

  const handleSendComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!commentText.trim()) return;
    await sendComment(commentText.trim());
    setCommentText('');
  };

  const REACTIONS = ['❤️', '😂', '👍', '🙌', '👏', '🔥'];

  // Render the Dedicated Room View when joined
  if (joined && activeChannel) {
    const currentRoom = channels.find((ch) => ch.channel === activeChannel) || activeRoomInfo;
    const isCurrentUserSpeaker =
      currentRoom?.users.find((u) => u.user_id === myUserId)?.is_speaker ?? false;
    const moderators = currentRoom?.users.filter((u) => u.is_moderator && u.user_id) ?? [];
    const speakers = currentRoom?.users.filter((u) => u.is_speaker) ?? [];
    const audience = currentRoom?.users.filter((u) => !u.is_speaker) ?? [];

    const renderUserCard = (u: ChannelUser, isSpeaker: boolean) => {
      const initials = u.name
        ? u.name
            .split(' ')
            .map((n) => n[0])
            .join('')
            .slice(0, 2)
        : '?';
      const shortName = u.name ? u.name.split(' ')[0] : 'Anonymous';
      const uid = u.user_id ?? 0;
      // Get live state for this user from PubNub events
      const liveState = liveUsers.find((l) => l.userId === uid);
      const isHandRaised = uid === myUserId ? handRaised : (liveState?.handRaised ?? false);
      const isSpeaking = uid > 0 && (speakingVolumes[uid] ?? 0) > 8;
      const speakingLevel = Math.min(1, (speakingVolumes[uid] ?? 0) / 60);
      const speakingScale = 1 + speakingLevel * 0.18;
      const speakingGlow = isSpeaking
        ? `0 0 ${Math.round(4 + speakingLevel * 12)}px ${Math.round(2 + speakingLevel * 6)}px rgba(110,168,254,${0.35 + speakingLevel * 0.35})`
        : 'none';

      return (
        <div
          key={u.user_id || Math.random()}
          className="ch-user-card"
          title={u.name || ''}
          onClick={() => handleUserClick(u.user_id)}
        >
          <div className="ch-avatar-container" style={{ position: 'relative' }}>
            {u.photo_url ? (
              <img
                className={`ch-avatar-squircle ${isSpeaker ? 'speaker' : 'listener'}`}
                src={u.photo_url}
                alt={u.name || ''}
                style={{
                  transform: `scale(${speakingScale})`,
                  boxShadow: speakingGlow,
                  transition: 'transform 0.15s ease, box-shadow 0.15s ease',
                }}
              />
            ) : (
              <div
                className={`ch-avatar-placeholder ${isSpeaker ? 'speaker' : 'listener'}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: isSpeaker ? '48px' : '40px',
                  height: isSpeaker ? '48px' : '40px',
                  borderRadius: isSpeaker ? '16px' : '14px',
                  background: 'linear-gradient(135deg, #2e333d, #14161a)',
                  color: 'var(--text-dim, #94a3b8)',
                  fontSize: isSpeaker ? '0.8rem' : '0.7rem',
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  border: isSpeaker
                    ? '2px solid var(--accent, #6ea8fe)'
                    : '1px solid var(--border)',
                  transform: `scale(${speakingScale})`,
                  boxShadow: speakingGlow,
                  transition: 'transform 0.15s ease, box-shadow 0.15s ease',
                }}
              >
                {initials}
              </div>
            )}
            {u.is_moderator && (
              <span className="ch-mod-badge" title="Moderator">
                ✳️
              </span>
            )}
            {isHandRaised && (
              <span className="ch-hand-badge" title="Hand Raised">
                🖐️
              </span>
            )}
            {isSpeaking && (
              <span
                style={{
                  position: 'absolute',
                  bottom: '-2px',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  fontSize: '8px',
                  background: 'rgba(110,168,254,0.9)',
                  borderRadius: '4px',
                  padding: '1px 3px',
                  color: '#000',
                  fontWeight: 700,
                  pointerEvents: 'none',
                  whiteSpace: 'nowrap',
                }}
              >
                🎙
              </span>
            )}
          </div>
          <span className={`ch-user-name ${isSpeaker ? '' : 'dim'}`}>{shortName}</span>
        </div>
      );
    };

    // Speaker invite toast
    const renderSpeakerInviteToast = () => {
      if (!speakerInvite) return null;
      return (
        <div
          style={{
            position: 'absolute',
            top: '60px',
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 200,
            background: 'linear-gradient(135deg, #1a2235, #1d2740)',
            border: '1px solid rgba(110,168,254,0.4)',
            borderRadius: '16px',
            padding: '1rem 1.25rem',
            boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
            minWidth: '260px',
            maxWidth: '320px',
            backdropFilter: 'blur(12px)',
            animation: 'slideDown 0.25s ease',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              marginBottom: '0.75rem',
            }}
          >
            {speakerInvite.moderatorPhoto ? (
              <img
                src={speakerInvite.moderatorPhoto}
                alt=""
                style={{ width: 36, height: 36, borderRadius: '12px', objectFit: 'cover' }}
              />
            ) : (
              <div
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: '12px',
                  background: '#2e3a4e',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '1rem',
                }}
              >
                🎤
              </div>
            )}
            <div>
              <div style={{ fontWeight: 700, fontSize: '0.85rem', color: '#f1f5f9' }}>
                You're invited to speak!
              </div>
              <div style={{ fontSize: '0.75rem', color: '#94a3b8', marginTop: '2px' }}>
                {speakerInvite.moderatorName} wants you on stage
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={() => void acceptSpeakerInvite(speakerInvite.moderatorId)}
              style={{
                flex: 1,
                padding: '0.5rem',
                borderRadius: '10px',
                border: 'none',
                background: 'linear-gradient(135deg, #3b82f6, #6ea8fe)',
                color: '#fff',
                fontWeight: 700,
                fontSize: '0.8rem',
                cursor: 'pointer',
              }}
            >
              🎤 Accept
            </button>
            <button
              onClick={dismissSpeakerInvite}
              style={{
                flex: 1,
                padding: '0.5rem',
                borderRadius: '10px',
                border: '1px solid rgba(255,255,255,0.12)',
                background: 'rgba(255,255,255,0.05)',
                color: '#94a3b8',
                fontWeight: 600,
                fontSize: '0.8rem',
                cursor: 'pointer',
              }}
            >
              Decline
            </button>
          </div>
        </div>
      );
    };

    return (
      <div className="ch-active-room-container">
        <style>{`
          .ch-active-room-container {
            display: flex;
            flex-direction: column;
            height: 100%;
            background: #14161a;
            color: var(--text, #f1f5f9);
            position: relative;
            overflow: hidden;
          }

          .ch-room-header-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.75rem 1rem;
            background: #1d2026;
            border-bottom: 1px solid var(--border, #2e333d);
            flex-shrink: 0;
            z-index: 10;
          }

          .ch-room-title-section {
            flex: 1;
            margin: 0 1rem;
            min-width: 0;
          }

          .ch-room-title-text {
            font-size: 0.95rem;
            font-weight: 600;
            margin: 0;
            color: #fff;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-room-club-text {
            font-size: 0.7rem;
            color: var(--accent, #6ea8fe);
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-btn-leave-quietly {
            background: rgba(239, 68, 68, 0.12);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #ef4444;
            padding: 0.4rem 0.8rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 0.25rem;
          }

          .ch-btn-leave-quietly:hover {
            background: rgba(239, 68, 68, 0.22);
            transform: translateY(-1px);
          }

          .ch-room-scroller {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            min-height: 0;
          }

          .ch-participants-section {
            max-height: 45%;
            overflow-y: auto;
            padding: 1rem;
            border-bottom: 1px solid var(--border, #2e333d);
            background: rgba(29, 32, 38, 0.4);
            flex-shrink: 0;
          }

          .ch-section-heading {
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-dim, #94a3b8);
            margin-bottom: 0.75rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
          }

          .ch-speakers-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(64px, 1fr));
            gap: 1rem 0.5rem;
            justify-items: center;
            margin-bottom: 1.5rem;
          }

          .ch-listeners-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(56px, 1fr));
            gap: 0.75rem 0.5rem;
            justify-items: center;
          }

          .ch-user-card {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
            position: relative;
          }

          .ch-avatar-container {
            position: relative;
            margin-bottom: 0.25rem;
          }

          .ch-avatar-squircle {
            width: 48px;
            height: 48px;
            border-radius: 16px;
            object-fit: cover;
            border: 2px solid transparent;
            background: #2e333d;
            transition: all 0.2s ease;
          }

          .ch-avatar-squircle.speaker {
            border-color: var(--accent, #6ea8fe);
            box-shadow: 0 0 8px rgba(110, 168, 254, 0.25);
          }

          .ch-avatar-squircle.listener {
            width: 40px;
            height: 40px;
            border-radius: 14px;
          }

          .ch-user-card:hover .ch-avatar-squircle {
            transform: scale(1.05);
          }

          .ch-mod-badge {
            position: absolute;
            bottom: -2px;
            left: -2px;
            background: #10b981;
            color: white;
            font-size: 0.5rem;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid #14161a;
            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
          }

          .ch-hand-badge {
            position: absolute;
            bottom: -2px;
            right: -2px;
            background: #f59e0b;
            font-size: 0.6rem;
            width: 15px;
            height: 15px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid #14161a;
            box-shadow: 0 1px 3px rgba(0,0,0,0.3);
          }

          .ch-user-name {
            font-size: 0.7rem;
            text-align: center;
            width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: var(--text, #f1f5f9);
          }

          .ch-user-name.dim {
            color: var(--text-dim, #94a3b8);
          }

          .ch-chat-section {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            background: #111317;
            min-height: 0;
          }

          .ch-chat-scroll {
            flex: 1;
            overflow-y: auto;
            padding: 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
          }

          .ch-chat-empty {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: var(--text-dim, #94a3b8);
            font-size: 0.8rem;
            gap: 0.5rem;
          }

          .ch-comment-item {
            display: flex;
            align-items: flex-start;
            gap: 0.5rem;
            animation: ch-fade-in 0.25s ease-out;
          }

          @keyframes ch-fade-in {
            from { opacity: 0; transform: translateY(4px); }
            to { opacity: 1; transform: translateY(0); }
          }

          .ch-comment-avatar {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            object-fit: cover;
            background: #2e333d;
            flex-shrink: 0;
            margin-top: 2px;
          }

          .ch-comment-content {
            flex: 1;
            min-width: 0;
          }

          .ch-comment-header {
            display: flex;
            align-items: baseline;
            gap: 0.4rem;
            margin-bottom: 0.15rem;
          }

          .ch-comment-user {
            font-size: 0.75rem;
            font-weight: 600;
            color: #fff;
          }

          .ch-comment-time {
            font-size: 0.6rem;
            color: var(--text-dim, #94a3b8);
          }

          .ch-comment-bubble {
            background: #1d2026;
            border: 1px solid var(--border, #2e333d);
            padding: 0.4rem 0.6rem;
            border-radius: 8px 12px 12px 8px;
            font-size: 0.8rem;
            line-height: 1.35;
            color: #f1f5f9;
            word-break: break-word;
          }

          .ch-room-bottom-panel {
            padding: 0.75rem 1rem;
            background: #1d2026;
            border-top: 1px solid var(--border, #2e333d);
            flex-shrink: 0;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
          }

          .ch-reactions-row {
            display: flex;
            align-items: center;
            justify-content: space-around;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 0.25rem 0.5rem;
          }

          .ch-reaction-btn {
            background: transparent;
            border: none;
            font-size: 1.3rem;
            cursor: pointer;
            padding: 0.25rem;
            border-radius: 8px;
            transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
          }

          .ch-reaction-btn:hover {
            transform: scale(1.25) translateY(-2px);
            background: rgba(255, 255, 255, 0.05);
          }

          .ch-input-row {
            display: flex;
            align-items: center;
            gap: 0.5rem;
          }

          .ch-comment-input {
            flex: 1;
            background: #14161a;
            border: 1px solid var(--border, #2e333d);
            border-radius: 20px;
            color: #fff;
            padding: 0.45rem 1rem;
            font-size: 0.85rem;
            outline: none;
            transition: all 0.2s ease;
          }

          .ch-comment-input:focus {
            border-color: var(--accent, #6ea8fe);
            box-shadow: 0 0 0 2px rgba(110, 168, 254, 0.15);
          }

          .ch-btn-send {
            background: var(--accent, #6ea8fe);
            color: #14161a;
            border: none;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
            flex-shrink: 0;
          }

          .ch-btn-send:hover:not(:disabled) {
            transform: scale(1.05);
            box-shadow: 0 2px 8px rgba(110, 168, 254, 0.3);
          }

          .ch-btn-send:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .ch-stage-actions-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.5rem;
            margin-top: 0.25rem;
          }

          .ch-btn-action {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.25rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border, #2e333d);
            color: var(--text, #f1f5f9);
            padding: 0.45rem 0.75rem;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
          }

          .ch-btn-action:hover:not(:disabled) {
            background: rgba(255, 255, 255, 0.1);
            transform: translateY(-1px);
          }

          .ch-btn-action.mic-muted {
            background: #ef4444;
            border-color: #ef4444;
            color: #fff;
          }

          .ch-btn-action.mic-active {
            background: #10b981;
            border-color: #10b981;
            color: #fff;
          }

          .ch-btn-action.hand-raised {
            background: #f59e0b;
            border-color: #f59e0b;
            color: #fff;
          }

          .ch-btn-action.join-stage {
            background: var(--accent, #6ea8fe);
            border-color: var(--accent, #6ea8fe);
            color: #14161a;
          }

          .ch-btn-action:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          .ch-floating-reactions-overlay {
            position: absolute;
            left: 0;
            right: 0;
            top: 0;
            bottom: 140px;
            pointer-events: none;
            overflow: hidden;
            z-index: 5;
          }

          .ch-floating-reaction {
            position: absolute;
            font-size: 2.2rem;
            pointer-events: none;
            animation: ch-reaction-float 2.8s cubic-bezier(0.08, 0.82, 0.17, 1) forwards;
            text-shadow: 0 2px 10px rgba(0,0,0,0.5);
          }

          @keyframes ch-reaction-float {
            0% {
              transform: translateY(0) scale(0.3) rotate(0deg);
              opacity: 0;
            }
            10% {
              transform: translateY(-20px) scale(1.2) rotate(var(--rot, 0deg));
              opacity: 1;
            }
            90% {
              opacity: 1;
            }
            100% {
              transform: translateY(-280px) scale(0.8) rotate(var(--rot-end, 15deg));
              opacity: 0;
            }
          }

          .ch-user-card {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
            position: relative;
            cursor: pointer;
          }

          .ch-profile-overlay {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.65);
            backdrop-filter: blur(4px);
            z-index: 100;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.5rem;
            animation: ch-fade-in 0.2s ease-out;
          }

          .ch-profile-card {
            background: #1d2026;
            border: 1px solid var(--border, #2e333d);
            border-radius: 20px;
            width: 100%;
            max-width: 320px;
            padding: 1.5rem;
            position: relative;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5);
            display: flex;
            flex-direction: column;
            gap: 1rem;
            animation: ch-profile-scale 0.25s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
          }

          @keyframes ch-profile-scale {
            from { transform: scale(0.9); opacity: 0; }
            to { transform: scale(1); opacity: 1; }
          }

          .ch-profile-close {
            position: absolute;
            top: 1rem;
            right: 1rem;
            background: transparent;
            border: none;
            color: var(--text-dim, #94a3b8);
            font-size: 1rem;
            cursor: pointer;
            padding: 0.25rem;
            transition: color 0.15s ease;
          }

          .ch-profile-close:hover {
            color: #fff;
          }

          .ch-profile-header {
            display: flex;
            align-items: center;
            gap: 1rem;
          }

          .ch-profile-avatar {
            width: 64px;
            height: 64px;
            border-radius: 22px;
            object-fit: cover;
            border: 2px solid var(--border, #2e333d);
          }

          .ch-profile-avatar-placeholder {
            width: 64px;
            height: 64px;
            border-radius: 22px;
            background: linear-gradient(135deg, #2e333d, #14161a);
            border: 2px solid var(--border, #2e333d);
            color: var(--text-dim, #94a3b8);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 1.2rem;
          }

          .ch-profile-names {
            display: flex;
            flex-direction: column;
            gap: 0.15rem;
            min-width: 0;
          }

          .ch-profile-name {
            font-size: 1.1rem;
            font-weight: 700;
            color: #fff;
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-profile-username {
            font-size: 0.8rem;
            color: var(--text-dim, #94a3b8);
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-follows-badge {
            align-self: flex-start;
            font-size: 0.65rem;
            background: rgba(110, 168, 254, 0.12);
            border: 1px solid rgba(110, 168, 254, 0.3);
            color: var(--accent, #6ea8fe);
            padding: 0.1rem 0.4rem;
            border-radius: 10px;
            font-weight: 600;
            margin-top: 0.25rem;
          }

          .ch-profile-stats {
            display: flex;
            gap: 1.5rem;
            border-bottom: 1px solid var(--border, #2e333d);
            padding-bottom: 0.75rem;
          }

          .ch-profile-stat {
            display: flex;
            align-items: baseline;
            gap: 0.25rem;
          }

          .ch-profile-stat-val {
            font-weight: 700;
            color: #fff;
            font-size: 0.95rem;
          }

          .ch-profile-stat-lbl {
            color: var(--text-dim, #94a3b8);
            font-size: 0.8rem;
          }

          .ch-profile-bio {
            font-size: 0.8rem;
            line-height: 1.4;
            color: #d1d5db;
            white-space: pre-wrap;
            max-height: 120px;
            overflow-y: auto;
          }

          .ch-profile-socials {
            display: flex;
            flex-direction: column;
            gap: 0.4rem;
            background: rgba(0, 0, 0, 0.15);
            padding: 0.5rem 0.75rem;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.02);
          }

          .ch-profile-social-item {
            font-size: 0.75rem;
            color: var(--text-dim, #94a3b8);
          }

          .ch-profile-social-item span {
            color: var(--accent, #6ea8fe);
            font-weight: 600;
          }

          .ch-profile-invited {
            font-size: 0.75rem;
            color: var(--text-dim, #94a3b8);
            border-top: 1px solid var(--border, #2e333d);
            padding-top: 0.75rem;
          }

          .ch-profile-loading {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.75rem;
            color: #fff;
            font-size: 0.9rem;
          }

          .ch-spinner {
            width: 24px;
            height: 24px;
            border: 2px solid rgba(255, 255, 255, 0.1);
            border-top-color: var(--accent, #6ea8fe);
            border-radius: 50%;
            animation: ch-spin 0.8s linear infinite;
          }

          @keyframes ch-spin {
            to { transform: rotate(360deg); }
          }

          /* Follow/Unfollow Button */
          .ch-profile-follow-btn {
            background: var(--accent, #6ea8fe);
            color: #14161a;
            border: none;
            padding: 0.35rem 1rem;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-top: 0.35rem;
            align-self: flex-start;
          }

          .ch-profile-follow-btn:hover:not(:disabled) {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(110, 168, 254, 0.3);
          }

          .ch-profile-follow-btn.following {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: #fff;
          }

          .ch-profile-follow-btn.following:hover:not(:disabled) {
            background: rgba(239, 68, 68, 0.15);
            border-color: rgba(239, 68, 68, 0.3);
            color: #ef4444;
            box-shadow: none;
          }

          /* General Modal Layout */
          .ch-modal-overlay {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(6px);
            z-index: 150;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.5rem;
            animation: ch-fade-in 0.2s ease-out;
          }

          .ch-modal-card {
            background: #1d2026;
            border: 1px solid var(--border, #2e333d);
            border-radius: 16px;
            width: 100%;
            max-width: 360px;
            padding: 1.5rem;
            position: relative;
            box-shadow: 0 12px 36px rgba(0, 0, 0, 0.5);
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
            animation: ch-profile-scale 0.25s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
          }

          .ch-modal-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
          }

          .ch-modal-title {
            font-size: 1.05rem;
            font-weight: 700;
            margin: 0;
            color: #fff;
          }

          .ch-modal-close {
            background: transparent;
            border: none;
            color: var(--text-dim, #94a3b8);
            font-size: 1rem;
            cursor: pointer;
            padding: 0.25rem;
            transition: color 0.15s ease;
          }

          .ch-modal-close:hover {
            color: #fff;
          }

          /* Forms inside Modals */
          .ch-form-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
          }

          .ch-label {
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-dim, #94a3b8);
          }

          .ch-text-input {
            background: #14161a;
            border: 1px solid var(--border, #2e333d);
            border-radius: 8px;
            color: #fff;
            padding: 0.55rem 0.75rem;
            font-size: 0.85rem;
            outline: none;
            transition: border-color 0.15s ease;
          }

          .ch-text-input:focus {
            border-color: var(--accent, #6ea8fe);
          }

          .ch-radio-group {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            background: rgba(0, 0, 0, 0.15);
            padding: 0.75rem;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.02);
          }

          .ch-radio-label {
            display: flex;
            align-items: flex-start;
            gap: 0.75rem;
            cursor: pointer;
            font-size: 0.85rem;
            color: #d1d5db;
            padding: 0.6rem 0.75rem;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.04);
            background: rgba(255, 255, 255, 0.01);
            transition: all 0.2s ease;
          }

          .ch-radio-label:hover {
            background: rgba(255, 255, 255, 0.04);
            border-color: rgba(255, 255, 255, 0.12);
          }

          .ch-radio-label.active {
            background: rgba(110, 168, 254, 0.08);
            border-color: rgba(110, 168, 254, 0.4);
            color: #fff;
          }

          .ch-radio-input {
            margin-top: 4px;
            cursor: pointer;
            accent-color: var(--accent, #6ea8fe);
          }

          .ch-radio-desc {
            font-size: 0.7rem;
            color: var(--text-dim, #94a3b8);
            margin-top: 0.1rem;
            display: block;
          }

          .ch-btn-submit {
            background: var(--accent, #6ea8fe);
            color: #14161a;
            border: none;
            border-radius: 8px;
            padding: 0.6rem 1rem;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            width: 100%;
            margin-top: 0.5rem;
          }

          .ch-btn-submit:hover:not(:disabled) {
            transform: translateY(-1px);
            box-shadow: 0 4px 12px rgba(110, 168, 254, 0.3);
          }

          .ch-btn-submit:disabled {
            opacity: 0.5;
            cursor: not-allowed;
          }

          /* Tabs styling on the main list */
          .ch-tabs {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1rem;
            border-bottom: 1px solid var(--border, #2e333d);
            padding-bottom: 0.5rem;
          }

          .ch-tab-btn {
            background: transparent;
            border: none;
            color: var(--text-dim, #94a3b8);
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            padding: 0.35rem 0.75rem;
            border-radius: 6px;
            transition: all 0.2s ease;
            position: relative;
          }

          .ch-tab-btn:hover {
            color: #fff;
          }

          .ch-tab-btn.active {
            color: var(--accent, #6ea8fe);
            background: rgba(110, 168, 254, 0.08);
          }

          /* People List in Find People */
          .ch-people-list {
            list-style: none;
            padding: 0;
            margin: 0;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            overflow-y: auto;
            flex: 1;
          }

          .ch-person-card {
            display: flex;
            align-items: center;
            gap: 0.85rem;
            padding: 0.85rem;
            background: var(--bg-raised);
            border: 1px solid var(--border);
            border-radius: 12px;
            transition: all 0.2s ease;
            cursor: pointer;
            width: 100%;
            box-sizing: border-box;
          }

          .ch-person-card:hover {
            border-color: color-mix(in srgb, var(--accent) 30%, var(--border));
            background: var(--bg-hover);
          }

          .ch-person-avatar {
            width: 44px;
            height: 44px;
            border-radius: 16px;
            object-fit: cover;
            border: 1px solid var(--border);
          }

          .ch-person-avatar-placeholder {
            width: 44px;
            height: 44px;
            border-radius: 16px;
            background: linear-gradient(135deg, #2e333d, #14161a);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.95rem;
            color: var(--text-dim, #94a3b8);
            border: 1px solid var(--border);
          }

          .ch-person-info {
            flex: 1;
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 0.15rem;
          }

          .ch-person-name {
            font-size: 0.85rem;
            font-weight: 600;
            color: #fff;
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-person-username {
            font-size: 0.75rem;
            color: var(--text-dim, #94a3b8);
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-person-bio {
            font-size: 0.7rem;
            color: #d1d5db;
            margin: 0.15rem 0 0 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }

          .ch-btn-follow-inline {
            background: var(--accent, #6ea8fe);
            color: #14161a;
            border: none;
            border-radius: 14px;
            padding: 0.3rem 0.8rem;
            font-size: 0.7rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
          }

          .ch-btn-follow-inline:hover {
            transform: scale(1.05);
          }

          .ch-btn-follow-inline.following {
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: #fff;
          }

          .ch-btn-follow-inline.following:hover {
            color: #ef4444;
            background: rgba(239, 68, 68, 0.12);
            border-color: rgba(239, 68, 68, 0.25);
          }

          /* Invite Friends Modal List */
          .ch-invite-list {
            list-style: none;
            padding: 0;
            margin: 0;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            max-height: 250px;
            overflow-y: auto;
          }

          .ch-invite-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            padding: 0.5rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
          }

          .ch-invite-avatar {
            width: 32px;
            height: 32px;
            border-radius: 12px;
            object-fit: cover;
          }

          .ch-invite-avatar-placeholder {
            width: 32px;
            height: 32px;
            border-radius: 12px;
            background: #2e333d;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.75rem;
            font-weight: 700;
            color: var(--text-dim, #94a3b8);
          }

          .ch-invite-user-info {
            flex: 1;
            min-width: 0;
          }

          .ch-invite-name {
            font-size: 0.8rem;
            font-weight: 600;
            color: #fff;
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-invite-username {
            font-size: 0.7rem;
            color: var(--text-dim, #94a3b8);
            margin: 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }

          .ch-btn-invite {
            background: var(--accent, #6ea8fe);
            color: #14161a;
            border: none;
            border-radius: 14px;
            padding: 0.25rem 0.75rem;
            font-size: 0.7rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
          }

          .ch-btn-invite:hover {
            transform: scale(1.05);
          }

          .ch-btn-invite.invited {
            background: rgba(255, 255, 255, 0.08);
            color: var(--text-dim, #94a3b8);
            border: 1px solid rgba(255, 255, 255, 0.05);
            cursor: default;
          }

          .ch-btn-invite.invited:hover {
            transform: none;
          }
        `}</style>

        {/* Floating Reactions overlay */}
        <div className="ch-floating-reactions-overlay">
          {activeReactions.map((r) => {
            const rot = (r.x % 30) - 15;
            const rotEnd = ((r.x * 2) % 60) - 30;
            return (
              <span
                key={r.id}
                className="ch-floating-reaction"
                style={{
                  left: `${r.x}%`,
                  top: `${r.y}%`,
                  ...({
                    '--rot': `${rot}deg`,
                    '--rot-end': `${rotEnd}deg`,
                  } as React.CSSProperties),
                }}
              >
                {r.emoji}
              </span>
            );
          })}
        </div>

        {/* Speaker invite toast — floats over the content */}
        {renderSpeakerInviteToast()}

        {/* Header Bar */}
        <div className="ch-room-header-bar">
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="ch-btn-leave-quietly"
              onClick={() => void leaveRoom(activeChannel)}
              disabled={voiceLoading}
            >
              ✌️ Leave quietly
            </button>
            <button
              className="ch-btn-action"
              style={{
                padding: '0.4rem 0.8rem',
                borderRadius: '20px',
                fontSize: '0.75rem',
                fontWeight: 600,
                flex: 'none',
                background: 'rgba(255, 255, 255, 0.05)',
              }}
              onClick={handleOpenInvite}
            >
              ➕ Invite Friends
            </button>
          </div>

          <div className="ch-room-title-section">
            {currentRoom?.club?.name && (
              <p className="ch-room-club-text">{currentRoom.club.name}</p>
            )}
            <h3 className="ch-room-title-text" title={currentRoom?.topic || '(Untitled Room)'}>
              {currentRoom?.topic || '(Untitled Room)'}
            </h3>
          </div>

          <span
            className="ch-pulse-dot"
            style={{
              width: '8px',
              height: '8px',
              backgroundColor: '#10b981',
              boxShadow: '0 0 8px #10b981',
            }}
          />
        </div>

        {/* Main Content Area */}
        <div className="ch-room-scroller">
          {/* Participants Area */}
          <div className="ch-participants-section">
            {/* Speakers */}
            <div className="ch-section-heading">
              <span>Speakers ({speakers.length})</span>
            </div>
            <div className="ch-speakers-grid">{speakers.map((u) => renderUserCard(u, true))}</div>

            {/* Audience */}
            {audience.length > 0 && (
              <>
                <div className="ch-section-heading">
                  <span>Audience ({audience.length})</span>
                </div>
                <div className="ch-listeners-grid">
                  {audience.map((u) => renderUserCard(u, false))}
                </div>
              </>
            )}
          </div>

          {/* Chat / Comments Feed */}
          <div className="ch-chat-section">
            <div
              className="ch-section-heading"
              style={{
                padding: '0.75rem 1rem 0.25rem 1rem',
                background: '#111317',
                borderBottom: '1px solid rgba(255,255,255,0.03)',
              }}
            >
              <span>Live Chat</span>
            </div>
            <div className="ch-chat-scroll">
              {comments.length === 0 ? (
                <div className="ch-chat-empty">
                  💬
                  <span>No messages yet. Be the first to start the chat!</span>
                </div>
              ) : (
                comments.map((c) => (
                  <div key={c.id} className="ch-comment-item">
                    {c.userPhoto ? (
                      <img className="ch-comment-avatar" src={c.userPhoto} alt="" />
                    ) : (
                      <div
                        className="ch-comment-avatar"
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          background: '#2e333d',
                          color: 'var(--text-dim)',
                          fontSize: '0.65rem',
                          fontWeight: 600,
                        }}
                      >
                        {c.userName.slice(0, 2).toUpperCase()}
                      </div>
                    )}
                    <div className="ch-comment-content">
                      <div className="ch-comment-header">
                        <span className="ch-comment-user">{c.userName}</span>
                        <span className="ch-comment-time">
                          {new Date(c.timestamp).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </span>
                      </div>
                      <div className="ch-comment-bubble">{c.text}</div>
                    </div>
                  </div>
                ))
              )}
              <div ref={chatEndRef} />
            </div>
          </div>
        </div>

        {/* Bottom Interactive Area */}
        <div className="ch-room-bottom-panel">
          {/* Reaction Bar */}
          <div className="ch-reactions-row">
            {REACTIONS.map((emoji) => (
              <button
                key={emoji}
                className="ch-reaction-btn"
                onClick={() => void sendReaction(emoji)}
                title={`Send ${emoji}`}
              >
                {emoji}
              </button>
            ))}
          </div>

          {/* Chat Input */}
          <form className="ch-input-row" onSubmit={handleSendComment}>
            <input
              className="ch-comment-input"
              type="text"
              placeholder="Send a chat message..."
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              disabled={voiceLoading}
            />
            <button
              className="ch-btn-send"
              type="submit"
              disabled={voiceLoading || !commentText.trim()}
              title="Send comment"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="22" y1="2" x2="11" y2="13"></line>
                <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
              </svg>
            </button>
          </form>

          {/* Action buttons (Mute/Raise hand/Accept) */}
          <div className="ch-stage-actions-row">
            <button
              className={`ch-btn-action ${agentEnabled ? 'mic-active' : ''}`}
              onClick={() => setAgentEnabled(!agentEnabled)}
            >
              🤖 {agentEnabled ? 'Agent ON' : 'Agent OFF'}
            </button>
            {isCurrentUserSpeaker ? (
              <button
                className={`ch-btn-action ${isMuted ? 'mic-muted' : 'mic-active'}`}
                onClick={() => void toggleMute()}
                disabled={voiceLoading}
              >
                {isMuted ? '🎙️ Unmute Mic' : '🎙️ Mic Active (Mute)'}
              </button>
            ) : (
              <>
                <button
                  className={`ch-btn-action ${handRaised ? 'hand-raised' : ''}`}
                  onClick={() => void raiseHand(!handRaised)}
                  disabled={voiceLoading}
                >
                  🖐️ {handRaised ? 'Lower Hand' : 'Raise Hand'}
                </button>
                {moderators.length > 0 && (
                  <button
                    className="ch-btn-action join-stage"
                    onClick={() => void acceptSpeakerInvite(moderators[0].user_id!)}
                    disabled={voiceLoading}
                  >
                    📢 Join Stage
                  </button>
                )}
              </>
            )}
          </div>
        </div>

        {voiceError && (
          <div
            style={{
              padding: '0.5rem 1rem',
              background: '#7f1d1d',
              color: '#fca5a5',
              fontSize: '0.8rem',
              textAlign: 'center',
            }}
          >
            Voice Error: {voiceError}
          </div>
        )}

        {renderProfileOverlay()}

        {loadingProfile && (
          <div className="ch-profile-overlay">
            <div className="ch-profile-loading">
              <div className="ch-spinner" />
              <span>Fetching profile...</span>
            </div>
          </div>
        )}

        {showInviteModal && (
          <div className="ch-modal-overlay" onClick={() => setShowInviteModal(false)}>
            <div className="ch-modal-card" onClick={(e) => e.stopPropagation()}>
              <div className="ch-modal-header">
                <h3 className="ch-modal-title">Invite Friends</h3>
                <button className="ch-modal-close" onClick={() => setShowInviteModal(false)}>
                  ✕
                </button>
              </div>
              <div className="ch-modal-body">
                {loadingFollowing ? (
                  <div className="ch-profile-loading">
                    <div className="ch-spinner" />
                    <span>Loading friends...</span>
                  </div>
                ) : followingUsers.length === 0 ? (
                  <p className="dashboard-hint" style={{ textAlign: 'center' }}>
                    You are not following anyone yet.
                  </p>
                ) : (
                  <ul className="ch-invite-list">
                    {followingUsers.map((u) => {
                      const initials = u.name
                        ? u.name
                            .split(' ')
                            .map((n) => n[0])
                            .join('')
                            .slice(0, 2)
                        : '?';
                      const isInvited = invitedUserIds.has(u.user_id!);
                      return (
                        <li key={u.user_id} className="ch-invite-item">
                          {u.photo_url ? (
                            <img className="ch-invite-avatar" src={u.photo_url} alt="" />
                          ) : (
                            <div className="ch-invite-avatar-placeholder">{initials}</div>
                          )}
                          <div className="ch-invite-user-info">
                            <p className="ch-invite-name">{u.name}</p>
                            <p className="ch-invite-username">@{u.username}</p>
                          </div>
                          <button
                            className={`ch-btn-invite ${isInvited ? 'invited' : ''}`}
                            onClick={() => !isInvited && handleSendInvite(u.user_id!)}
                            disabled={isInvited}
                          >
                            {isInvited ? 'Invited' : 'Ping'}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="ch-rooms">
      <header className="ch-rooms-head">
        <h2>Clubhouse</h2>
        <div className="ch-rooms-controls">
          {state === 'ready' && (
            <button
              className="ch-btn-start-room"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.25rem',
                background: 'var(--accent, #6ea8fe)',
                border: 'none',
                color: '#14161a',
                padding: '0.4rem 0.9rem',
                borderRadius: '20px',
                fontSize: '0.75rem',
                fontWeight: 600,
                cursor: 'pointer',
                transition: 'all 0.2s ease',
              }}
              onClick={() => setShowStartRoomModal(true)}
            >
              <span>+ Start Room</span>
            </button>
          )}
          {state === 'ready' && activeTab === 'rooms' && (
            <div className="ch-search-container">
              <span className="ch-search-icon">
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" x2="16.65" y1="21" y2="16.65" />
                </svg>
              </span>
              <input
                className="ch-search-input"
                type="text"
                placeholder="Search topics, clubs, or users..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
          )}
          <button
            className={`ch-btn-refresh ${state === 'loading' ? 'spinning' : ''}`}
            onClick={() => void load()}
            disabled={state === 'loading'}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M23 4v6h-6" />
              <path d="M1 20v-6h6" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
            {state === 'loading' ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      {state === 'ready' && (
        <div className="ch-tabs" style={{ padding: '0 1.25rem' }}>
          <button
            className={`ch-tab-btn ${activeTab === 'rooms' ? 'active' : ''}`}
            onClick={() => setActiveTab('rooms')}
          >
            Live Rooms
          </button>
          <button
            className={`ch-tab-btn ${activeTab === 'people' ? 'active' : ''}`}
            onClick={() => setActiveTab('people')}
          >
            Find People
          </button>
        </div>
      )}

      {voiceError && (
        <p
          className="widget-error"
          style={{
            margin: '0.75rem 1.25rem',
            padding: '0.5rem',
            background: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid var(--danger, #ef4444)',
            borderRadius: '6px',
          }}
        >
          Voice Error: {voiceError}
        </p>
      )}

      {state === 'disconnected' && (
        <p className="dashboard-hint">
          Connect your Clubhouse account first (using the Clubhouse widget on the dashboard).
        </p>
      )}
      {state === 'error' && <p className="widget-error">{error}</p>}

      {state === 'ready' && activeTab === 'rooms' && (
        <>
          {channels.length === 0 && <p className="dashboard-hint">No rooms are live right now.</p>}
          {channels.length > 0 && filteredChannels.length === 0 && (
            <p className="dashboard-hint">No rooms match your search query.</p>
          )}
          <ul className="ch-room-list" style={{ padding: '0 1.25rem' }}>
            {filteredChannels.map((c) => {
              const mainSpeakers = c.users.filter((u) => u.is_speaker).slice(0, 3);
              const otherSpeakersCount = Math.max(
                0,
                c.users.filter((u) => u.is_speaker).length - 3,
              );

              return (
                <li key={c.channel} className="ch-room">
                  <div className="ch-room-header">
                    <h3 className="ch-room-topic">{c.topic || '(Untitled Room)'}</h3>
                    {c.club?.name && (
                      <span className="ch-room-club-badge" title={c.club.name}>
                        {c.club.name}
                      </span>
                    )}
                  </div>

                  <div className="ch-room-body">
                    <div className="ch-avatar-stack">
                      {mainSpeakers.map((u) => {
                        const initials = u.name
                          ? u.name
                              .split(' ')
                              .map((n) => n[0])
                              .join('')
                              .slice(0, 2)
                          : '?';
                        return u.photo_url ? (
                          <img
                            key={u.user_id}
                            className="ch-avatar-stack-item"
                            src={u.photo_url}
                            alt={u.name || ''}
                            title={`${u.name}${u.is_moderator ? ' (Moderator)' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleUserClick(u.user_id);
                            }}
                            style={{ cursor: 'pointer' }}
                          />
                        ) : (
                          <div
                            key={u.user_id}
                            className="ch-avatar-placeholder"
                            title={`${u.name}${u.is_moderator ? ' (Moderator)' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleUserClick(u.user_id);
                            }}
                            style={{ cursor: 'pointer' }}
                          >
                            {initials}
                          </div>
                        );
                      })}
                      {otherSpeakersCount > 0 && (
                        <div
                          className="ch-avatar-placeholder"
                          title={`${otherSpeakersCount} more speakers`}
                        >
                          +{otherSpeakersCount}
                        </div>
                      )}
                    </div>

                    <div className="ch-room-speakers-text">
                      {c.users.map((u, idx) => (
                        <span
                          key={u.user_id || idx}
                          style={{ fontWeight: u.is_moderator ? 600 : 'normal' }}
                        >
                          {u.is_moderator && <span className="ch-pulse-dot" title="Moderator" />}
                          {u.name}
                          {idx < c.users.length - 1 ? ', ' : ''}
                        </span>
                      ))}
                    </div>
                  </div>

                  <div className="ch-room-footer">
                    <div className="ch-room-stats">
                      <div className="ch-stat-badge" title="Speakers">
                        <svg
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                          <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
                          <line x1="12" x2="12" y1="19" y2="22" />
                        </svg>
                        <span>{c.num_speakers ?? 0}</span>
                      </div>
                      <div className="ch-stat-badge" title="Listeners">
                        <svg
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                          <circle cx="9" cy="7" r="4" />
                          <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
                          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                        </svg>
                        <span>{c.num_all ?? 0}</span>
                      </div>
                    </div>
                    {joined && activeChannel === c.channel ? (
                      <button
                        className="ch-btn-leave"
                        onClick={() => void leaveRoom(c.channel!)}
                        disabled={voiceLoading}
                      >
                        {voiceLoading ? 'Leaving…' : 'Leave'}
                      </button>
                    ) : (
                      <button
                        className="ch-btn-join"
                        onClick={() => {
                          setActiveRoomInfo(c);
                          void joinRoom(c.channel!, c.users);
                        }}
                        disabled={voiceLoading || (joined && activeChannel !== c.channel)}
                      >
                        {voiceLoading && activeChannel === c.channel ? 'Connecting…' : 'Join Room'}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {state === 'ready' && activeTab === 'people' && (
        <div
          className="ch-people-search-section"
          style={{
            padding: '0 1.25rem 1.5rem 1.25rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '1rem',
            flex: 1,
            overflow: 'hidden',
          }}
        >
          <form onSubmit={handlePeopleSearch} className="ch-input-row" style={{ width: '100%' }}>
            <input
              className="ch-comment-input"
              style={{ borderRadius: '8px' }}
              type="text"
              placeholder="Search people by name or username..."
              value={peopleSearchQuery}
              onChange={(e) => setPeopleSearchQuery(e.target.value)}
            />
            <button
              className="ch-btn-send"
              style={{ borderRadius: '8px', width: '38px', height: '38px' }}
              type="submit"
              disabled={loadingPeople || !peopleSearchQuery.trim()}
            >
              {loadingPeople ? (
                <div className="ch-spinner" style={{ width: '14px', height: '14px' }} />
              ) : (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" x2="16.65" y1="21" y2="16.65" />
                </svg>
              )}
            </button>
          </form>

          {peopleSearchResults.length === 0 ? (
            <p className="dashboard-hint" style={{ textAlign: 'center', marginTop: '2rem' }}>
              Search for users to see their profile and follow them.
            </p>
          ) : (
            <ul className="ch-people-list">
              {peopleSearchResults.map((u) => {
                const initials = u.name
                  ? u.name
                      .split(' ')
                      .map((n) => n[0])
                      .join('')
                      .slice(0, 2)
                  : '?';
                const isFollowing = u.is_following;

                const handleFollowToggleInline = async (e: React.MouseEvent) => {
                  e.stopPropagation();
                  try {
                    if (isFollowing) {
                      await unfollowClubhouseUser(u.user_id);
                      setPeopleSearchResults((prev) =>
                        prev.map((item) =>
                          item.user_id === u.user_id ? { ...item, is_following: false } : item,
                        ),
                      );
                    } else {
                      await followClubhouseUser(u.user_id);
                      setPeopleSearchResults((prev) =>
                        prev.map((item) =>
                          item.user_id === u.user_id ? { ...item, is_following: true } : item,
                        ),
                      );
                    }
                  } catch (err) {
                    console.error('Failed to toggle follow inline:', err);
                  }
                };

                return (
                  <li
                    key={u.user_id}
                    className="ch-person-card"
                    onClick={() => handleUserClick(u.user_id)}
                  >
                    {u.photo_url ? (
                      <img className="ch-person-avatar" src={u.photo_url} alt="" />
                    ) : (
                      <div className="ch-person-avatar-placeholder">{initials}</div>
                    )}
                    <div className="ch-person-info">
                      <h4 className="ch-person-name">{u.name}</h4>
                      <p className="ch-person-username">@{u.username}</p>
                      {u.bio && <p className="ch-person-bio">{u.bio}</p>}
                    </div>
                    <button
                      className={`ch-btn-follow-inline ${isFollowing ? 'following' : ''}`}
                      onClick={handleFollowToggleInline}
                    >
                      {isFollowing ? 'Following' : 'Follow'}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {renderProfileOverlay()}

      {loadingProfile && (
        <div className="ch-profile-overlay">
          <div className="ch-profile-loading">
            <div className="ch-spinner" />
            <span>Fetching profile...</span>
          </div>
        </div>
      )}

      {showStartRoomModal && (
        <div className="ch-modal-overlay" onClick={() => setShowStartRoomModal(false)}>
          <div className="ch-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="ch-modal-header">
              <h3 className="ch-modal-title">Start a Room</h3>
              <button className="ch-modal-close" onClick={() => setShowStartRoomModal(false)}>
                ✕
              </button>
            </div>
            <form
              onSubmit={handleStartRoom}
              style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}
            >
              <div className="ch-form-group">
                <label className="ch-label">Topic</label>
                <input
                  className="ch-text-input"
                  type="text"
                  placeholder="What do you want to talk about?"
                  value={newRoomTopic}
                  onChange={(e) => setNewRoomTopic(e.target.value)}
                  maxLength={100}
                />
              </div>
              <div className="ch-form-group">
                <label className="ch-label">Privacy</label>
                <div className="ch-radio-group">
                  <label
                    className={`ch-radio-label ${newRoomPrivacy === 'public' ? 'active' : ''}`}
                  >
                    <input
                      className="ch-radio-input"
                      type="radio"
                      name="room-privacy"
                      value="public"
                      checked={newRoomPrivacy === 'public'}
                      onChange={() => setNewRoomPrivacy('public')}
                    />
                    <div>
                      <span>Open (Public)</span>
                      <span className="ch-radio-desc">Anyone can join your room</span>
                    </div>
                  </label>
                  <label
                    className={`ch-radio-label ${newRoomPrivacy === 'social' ? 'active' : ''}`}
                  >
                    <input
                      className="ch-radio-input"
                      type="radio"
                      name="room-privacy"
                      value="social"
                      checked={newRoomPrivacy === 'social'}
                      onChange={() => setNewRoomPrivacy('social')}
                    />
                    <div>
                      <span>Social</span>
                      <span className="ch-radio-desc">Only people you follow can join</span>
                    </div>
                  </label>
                  <label
                    className={`ch-radio-label ${newRoomPrivacy === 'private' ? 'active' : ''}`}
                  >
                    <input
                      className="ch-radio-input"
                      type="radio"
                      name="room-privacy"
                      value="private"
                      checked={newRoomPrivacy === 'private'}
                      onChange={() => setNewRoomPrivacy('private')}
                    />
                    <div>
                      <span>Closed (Private)</span>
                      <span className="ch-radio-desc">Only people you invite can join</span>
                    </div>
                  </label>
                </div>
              </div>
              <button className="ch-btn-submit" type="submit" disabled={creatingRoom}>
                {creatingRoom ? 'Starting Room...' : "🎉 Let's go"}
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
