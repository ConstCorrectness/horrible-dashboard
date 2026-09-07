import { useEffect, useRef, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import { toastsStore } from '../../toasts';
import { getAgentStatus, saveAgentConfig, type AgentStatus } from '../agent/api';
import {
  getClubhouseChannels,
  getClubhouseStatus,
  getClubhouseChannelDetails,
  getClubhouseUserProfile,
  createClubhouseChannel,
  followClubhouseUser,
  unfollowClubhouseUser,
  inviteToClubhouseChannel,
  inviteClubhouseSpeaker,
  uninviteClubhouseSpeaker,
  makeClubhouseModerator,
  blockFromClubhouseChannel,
  endClubhouseChannel,
  getAgentTtsVoices,
  updateClubhouseTopic,
  updateClubhouseHandraiseSettings,
  type HandraisePermission,
  updateClubhouseChatSettings,
  searchClubhouseUsers,
  getClubhouseFollowing,
  listPeopleMemory,
  addPersonNote,
  removePersonNote,
  forgetPersonMemory,
  type Channel,
  type ChannelUser,
  type ClubhouseUserProfile,
  type SearchUserResult,
  type FollowUser,
  type PersonMemory,
  type TtsVoiceOption,
} from './api';
import { bindClubhouse } from './actions';
import { MediaInsightsModal } from './MediaInsightsModal';
import { useClubhouseVoice } from './useClubhouseVoice';
import {
  DEFAULT_VOICE_CONFIG,
  getVoiceState,
  pushVoiceConfig,
  resetVoiceMemory,
  takeVoiceTurn,
  type VoiceAgentConfig,
  type VoiceRoom,
  type VoiceStateTurn,
} from './voiceAgent';

// Shared styling for the Agent tab's form fields — the panel styles inline, and
// repeating these objects per control is what made the old block hard to extend.
const agentFieldStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: '0.4rem',
  flex: 1,
  minWidth: '140px',
};
const agentLabelStyle: React.CSSProperties = {
  fontSize: '0.75rem',
  color: '#94a3b8',
  fontWeight: 600,
};
/**
 * **Horizontal padding only.** `controls.css` gives every `<input>`/`<select>` a
 * fixed `height: var(--control-h)` (30px) with its vertical padding removed —
 * the One Height Rule that makes a row of mixed controls line up. Re-adding
 * `padding: 0.5rem` here did not grow the box, it ate it: 16px of padding plus
 * 2px of border out of 30px left 12px of content for 0.8rem text. Inputs got
 * away with it; a `<select>` did not, because native rendering reserves extra
 * space for its own arrow — so every dropdown in the Agent tab drew its label
 * low and clipped along the bottom.
 */
const agentInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '0 0.6rem',
  fontSize: '0.8rem',
  background: '#1d2026',
  color: '#f1f5f9',
  border: '1px solid #2e333d',
  borderRadius: '4px',
};

const DEFAULT_PRESETS: { name: string; prompt: string; chipLabel?: string }[] = [
  {
    name: 'Room Co-Host & Assistant',
    chipLabel: '🤖 Co-Host',
    prompt:
      'You are a friendly and proactive co-host in a live Clubhouse audio room. You answer questions concisely in 1-3 spoken sentences, engage with attendees naturally, and keep the discussion flowing.',
  },
  {
    name: 'Podcast & Interview Host',
    chipLabel: '🎙️ Podcast Host',
    prompt:
      'You are an energetic podcast host. You ask insightful follow-up questions, introduce speakers warmly, and summarize key takeaways in a charismatic, broadcast-ready speaking style.',
  },
  {
    name: 'Debater & Critical Thinker',
    chipLabel: '⚔️ Debater',
    prompt:
      'You are an intellectually sharp, respectful debate participant. You probe underlying assumptions, highlight alternative perspectives, and construct logical arguments.',
  },
  {
    name: 'Concise & Direct (No Fluff)',
    chipLabel: '⚡ Concise',
    prompt:
      'You are an ultra-concise assistant. You give immediate, direct answers in 1-2 punchy spoken sentences without fluff, preambles, or conversational filler.',
  },
  {
    name: 'Tech & Architecture Expert',
    chipLabel: '💡 Tech Expert',
    prompt:
      'You are a senior software architect and tech expert. You explain complex technical concepts, system tradeoffs, algorithms, and engineering patterns clearly and concisely for an engineering audience.',
  },
];

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

  // Voice agent. The conversation itself lives on the backend (one session per
  // channel); this is the settings mirror plus the local "is it talking" flag.
  const [agentConfig, setAgentConfig] = useState<VoiceAgentConfig>(() => {
    try {
      const saved = localStorage.getItem('clubhouseVoiceConfig');
      return saved ? { ...DEFAULT_VOICE_CONFIG, ...JSON.parse(saved) } : DEFAULT_VOICE_CONFIG;
    } catch {
      return DEFAULT_VOICE_CONFIG;
    }
  });
  const agentEnabled = agentConfig.enabled;
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false);
  const [isAgentThinking, setIsAgentThinking] = useState(false);
  // Why the agent stayed quiet on the last turn. Rendered in the Agent tab, because
  // a deliberate silence and a broken pipeline are indistinguishable without it.
  const [agentReason, setAgentReason] = useState<string | null>(null);
  const [agentMemory, setAgentMemory] = useState<VoiceStateTurn[]>([]);
  const [sttChunkMs] = useState(5000);

  const [wakeWordsInput, setWakeWordsInput] = useState(() => (agentConfig.wakeWords || []).join(', '));
  const [personaDraft, setPersonaDraft] = useState(() => agentConfig.persona || DEFAULT_VOICE_CONFIG.persona);
  const [selectedPreset, setSelectedPreset] = useState('');
  const [lastHeardSpeech, setLastHeardSpeech] = useState<{
    text: string;
    speaker?: string;
    time: string;
  } | null>(null);

  const [agentPromptPresets, setAgentPromptPresets] = useState<{ name: string; prompt: string }[]>(
    () => {
      try {
        const saved = JSON.parse(localStorage.getItem('agentPresets') || '[]');
        return Array.isArray(saved) && saved.length > 0 ? saved : DEFAULT_PRESETS;
      } catch {
        return DEFAULT_PRESETS;
      }
    },
  );

  const patchAgentConfig = (patch: Partial<VoiceAgentConfig>) =>
    setAgentConfig((prev) => {
      const next = { ...prev, ...patch };
      try {
        localStorage.setItem('clubhouseVoiceConfig', JSON.stringify(next));
      } catch {
        /* storage unavailable — the backend still has the live config */
      }
      return next;
    });
  const [myUserId, setMyUserId] = useState<number | null>(null);

  // Room settings modal states
  const [showRoomSettingsModal, setShowRoomSettingsModal] = useState(false);
  const [settingTopic, setSettingTopic] = useState('');
  const [settingHandraiseEnabled, setSettingHandraiseEnabled] = useState(true);
  const [settingHandraisePermission, setSettingHandraisePermission] =
    useState<HandraisePermission>('everyone');
  const [settingChatEnabled, setSettingChatEnabled] = useState(true);
  const [savingSettings, setSavingSettings] = useState(false);

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

  // Voice Agent Settings sidebar visibility (right-hand column) and resizable splitter
  const [showAgentSidebar, setShowAgentSidebar] = useState(true);
  const [showNetworkModal, setShowNetworkModal] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(340);
  const [isDraggingSplitter, setIsDraggingSplitter] = useState(false);
  const splitContainerRef = useRef<HTMLDivElement>(null);
  const [agentEngineStatus, setAgentEngineStatus] = useState<AgentStatus | null>(null);
  const [switchingModel, setSwitchingModel] = useState(false);

  // Resize drag handling for vertical splitter between stage/chat and agent sidebar
  useEffect(() => {
    if (!isDraggingSplitter) return;
    const handleMouseMove = (e: MouseEvent) => {
      if (!splitContainerRef.current) return;
      const rect = splitContainerRef.current.getBoundingClientRect();
      const newWidth = rect.right - e.clientX;
      const minW = 240;
      const maxW = Math.max(minW, rect.width - 280);
      setSidebarWidth(Math.min(maxW, Math.max(minW, newWidth)));
    };
    const handleMouseUp = () => {
      setIsDraggingSplitter(false);
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingSplitter]);

  // People Knowledge & Memory
  const [peopleMemory, setPeopleMemory] = useState<PersonMemory[]>([]);
  const [peopleMemoryQuery, setPeopleMemoryQuery] = useState('');
  const [peopleMemoryLoading, setPeopleMemoryLoading] = useState(false);
  const [newNoteInputs, setNewNoteInputs] = useState<{ [uid: number]: string }>({});

  const [ttsVoices, setTtsVoices] = useState<TtsVoiceOption[]>([]);

  const refreshPeopleMemory = async (q = peopleMemoryQuery) => {
    setPeopleMemoryLoading(true);
    try {
      const data = await listPeopleMemory(q);
      setPeopleMemory(data);
    } catch {
      // ignore
    } finally {
      setPeopleMemoryLoading(false);
    }
  };


  useEffect(() => {
    void getAgentStatus().then(setAgentEngineStatus).catch(() => {});
    void refreshPeopleMemory();
    void getAgentTtsVoices().then(setTtsVoices).catch(() => {});
  }, []);


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
        const meUser: ChannelUser | null = myUserId
          ? {
              user_id: myUserId,
              name: 'Me (Creator)',
              username: 'me',
              photo_url: null,
              is_speaker: true,
              is_moderator: true,
            }
          : null;

        const initialUsers = meUser ? [meUser] : [];
        const roomInfo: Channel = {
          channel: res.channel,
          topic: newRoomTopic.trim() || 'My New Room',
          num_speakers: 1,
          num_all: 1,
          club: null,
          users: initialUsers,
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

  const handleOpenRoomSettings = () => {
    if (!activeRoomInfo) return;
    setSettingTopic(activeRoomInfo.topic || '');
    setSettingHandraiseEnabled(true);
    setSettingHandraisePermission('everyone');
    setSettingChatEnabled(true);
    setShowRoomSettingsModal(true);
  };

  const handleSaveRoomSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeChannel) return;
    setSavingSettings(true);
    try {
      await updateClubhouseTopic(activeChannel, settingTopic.trim());
      await updateClubhouseHandraiseSettings(
        activeChannel,
        settingHandraiseEnabled,
        settingHandraisePermission,
      );
      await updateClubhouseChatSettings(activeChannel, settingChatEnabled);
      toastsStore.add('success', 'Room Settings', 'Successfully updated room settings');
      setShowRoomSettingsModal(false);
      if (activeRoomInfo) setActiveRoomInfo({ ...activeRoomInfo, topic: settingTopic.trim() });
    } catch (err) {
      toastsStore.add(
        'error',
        'Update Failed',
        err instanceof Error ? err.message : 'Could not update all settings',
      );
    } finally {
      setSavingSettings(false);
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

          <div className="ch-profile-actions" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
            {!isCurrentUser &&
              activeChannel &&
              (() => {
                const amIMod = activeRoomInfo?.users.find(
                  (u) => u.user_id === myUserId,
                )?.is_moderator;
                const isTheySpeaker =
                  liveUsers.find((u) => u.userId === selectedUser.user_id)?.isSpeaker ??
                  activeRoomInfo?.users.find((u) => u.user_id === selectedUser.user_id)?.is_speaker;
                const isTheyMod = activeRoomInfo?.users.find(
                  (u) => u.user_id === selectedUser.user_id,
                )?.is_moderator;

                if (!amIMod) return null;

                return (
                  <>
                    {!isTheySpeaker ? (
                      <button
                        className="ch-btn-action"
                        onClick={async () => {
                          try {
                            await inviteClubhouseSpeaker(activeChannel, selectedUser.user_id);
                            toastsStore.add(
                              'success',
                              'Invite Sent',
                              `Invited ${selectedUser.name} to speak.`,
                            );
                            setSelectedUser(null);
                          } catch (err) {
                            toastsStore.add(
                              'error',
                              'Failed',
                              err instanceof Error ? err.message : 'Could not invite speaker',
                            );
                          }
                        }}
                      >
                        🎤 Invite to Stage
                      </button>
                    ) : (
                      <button
                        className="ch-btn-action"
                        onClick={async () => {
                          try {
                            await uninviteClubhouseSpeaker(activeChannel, selectedUser.user_id);
                            toastsStore.add(
                              'success',
                              'Moved to Audience',
                              `Moved ${selectedUser.name} to audience.`,
                            );
                            setSelectedUser(null);
                          } catch (err) {
                            toastsStore.add(
                              'error',
                              'Failed',
                              err instanceof Error ? err.message : 'Could not demote speaker',
                            );
                          }
                        }}
                      >
                        ⬇️ Move to Audience
                      </button>
                    )}

                    {!isTheyMod && (
                      <button
                        className="ch-btn-action"
                        onClick={async () => {
                          try {
                            await makeClubhouseModerator(activeChannel, selectedUser.user_id);
                            toastsStore.add(
                              'success',
                              'Promoted',
                              `Made ${selectedUser.name} a moderator.`,
                            );
                            setSelectedUser(null);
                          } catch (err) {
                            toastsStore.add(
                              'error',
                              'Failed',
                              err instanceof Error ? err.message : 'Could not make moderator',
                            );
                          }
                        }}
                      >
                        ⭐ Make Moderator
                      </button>
                    )}

                    <button
                      className="ch-btn-action"
                      style={{ color: 'var(--danger)', borderColor: 'color-mix(in srgb, var(--danger) 40%, transparent)' }}
                      onClick={async () => {
                        if (confirm(`Are you sure you want to remove and block ${selectedUser.name} from this room?`)) {
                          try {
                            await blockFromClubhouseChannel(activeChannel, selectedUser.user_id);
                            toastsStore.add(
                              'success',
                              'Removed User',
                              `Removed ${selectedUser.name} from room.`,
                            );
                            setSelectedUser(null);
                          } catch (err) {
                            toastsStore.add(
                              'error',
                              'Failed',
                              err instanceof Error ? err.message : 'Could not remove user',
                            );
                          }
                        }
                      }}
                    >
                      🚫 Remove from Room
                    </button>
                  </>
                );
              })()}
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
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatScrollTopRef = useRef<number | null>(null);
  const isNearBottomRef = useRef<boolean>(true);
  const lastRoomActivityTsRef = useRef<number>(Date.now());
  const enqueueUtteranceRef = useRef<
    (
      text: string,
      source: 'voice' | 'chat',
      force?: boolean,
      speakerName?: string,
      speakerId?: number | null,
      partial?: boolean,
      isSelf?: boolean,
    ) => void
  >(() => {});

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
    playAgentAudio,
    previewTtsVoice,
    stopAgentAudio,
    loading: voiceLoading,
    error: voiceError,
    voiceError: speechError,
    joinRoom,
    leaveRoom,
    toggleMute,
    raiseHand,
    acceptSpeakerInvite,
    dismissSpeakerInvite,
    sendComment,
    sendReaction,
    getNetworkInsights,
  } = useClubhouseVoice({

    sttChunkIntervalMs: sttChunkMs,
    endpointingDelayMs: agentConfig.endpointingDelayMs || 750,
    allowBargeIn: agentConfig.allowBargeIn !== false,
    // Zero everywhere but the interject posture: the flush is what *creates* a
    // partial, so leaving it running under another posture would have the pane
    // cutting recordings in half for turns the server is only going to drop.
    interjectAfterMs:
      agentConfig.posture === 'interject' ? (agentConfig.interjectAfterS ?? 6) * 1000 : 0,
    onBargeIn: () => {
      if (agentAbortControllerRef.current) {
        try {
          agentAbortControllerRef.current.abort();
        } catch {
          /* ignore */
        }
        agentAbortControllerRef.current = null;
      }
      stopAgentAudio();
      agentQueueRef.current = [];
      setIsAgentThinking(false);
      setIsAgentSpeaking(false);
    },
    onTranscribe: (text, _speakerName, speakerId, partial) => {
      lastRoomActivityTsRef.current = Date.now();
      // Resolve actual speaker name from room user roster
      let resolvedName: string | undefined;
      if (speakerId === 0 || (myUserId != null && speakerId === myUserId)) {
        resolvedName = myProfileName || 'Me';
        speakerId = myUserId;
      } else if (speakerId != null) {
        const userInDetails = activeRoomInfoRef.current?.users?.find((u) => u.user_id === speakerId);
        if (userInDetails?.name) resolvedName = userInDetails.name;
      }
      if (text.trim().length > 0) {
        setLastHeardSpeech({
          text: text.trim(),
          speaker: resolvedName,
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        });
        const isSelf = isAgentSpeaking;
        enqueueUtteranceRef.current(
          text.trim(),
          'voice',
          false,
          resolvedName,
          speakerId,
          partial,
          isSelf,
        );
      }
    },

    onVoiceError: (message) => {
      toastsStore.add('warning', 'Voice', message);
    },
    onSpeakerInvite: (invite) => {
      toastsStore.add('info', 'Speaker Invite', `${invite.moderatorName} wants you to speak!`);
      if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        new Notification('Speaker Invite', { body: `${invite.moderatorName} wants you to speak!` });
      }
      if (agentEnabled) {
        acceptSpeakerInvite();
        toastsStore.add(
          'info',
          'Agent',
          `Auto-accepted speaker invite from ${invite.moderatorName}`,
        );
      }
    },
    onHandRaise: (_userId, userName) => {
      const amIMod = activeRoomInfo?.users.find((u) => u.user_id === myUserId)?.is_moderator;
      if (amIMod) {
        toastsStore.add('info', 'Hand Raised', `${userName} wants to come up on stage.`);
        if (
          typeof window !== 'undefined' &&
          'Notification' in window &&
          Notification.permission === 'granted'
        ) {
          new Notification('Hand Raised', { body: `${userName} wants to come up on stage.` });
        }
      }
    },
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
      // The agent introduces itself by this name and answers to it as a wake word.
      setMyProfileName(status.name || '');
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
    if (
      typeof window !== 'undefined' &&
      'Notification' in window &&
      Notification.permission === 'default'
    ) {
      Notification.requestPermission().catch(() => {});
    }
  }, []);

  /**
   * Publish the mic toggle for the `1` keybinding, and take it back down on unmount.
   *
   * Bound to `joined` rather than to the mount: with no room there is no track to mute,
   * and an unbound handle is how the command reports that (see `actions.ts`). The call
   * goes through a ref because `toggleMute` is a fresh closure every render and closes
   * over `isMuted` — binding the function itself would either re-publish the handle on
   * every render or, pinned to `joined`, latch the mute state as it was when the room
   * was joined and toggle against a stale value forever.
   */
  const toggleMuteRef = useRef(toggleMute);
  toggleMuteRef.current = toggleMute;
  useEffect(() => {
    if (!joined) return;
    bindClubhouse({ toggleMic: () => void toggleMuteRef.current() });
    return () => bindClubhouse(null);
  }, [joined]);

  // Poll active channel details when joined to keep participant lists updated in real-time
  useEffect(() => {
    if (!joined || !activeChannel) return;

    const updateActiveChannel = async () => {
      try {
        const details = await getClubhouseChannelDetails(activeChannel);
        setChannels((prev) => prev.map((ch) => (ch.channel === activeChannel ? details : ch)));
        setActiveRoomInfo(details); // update active room info for the UI
      } catch (err) {
        console.error('Failed to poll active channel details:', err);
      }
    };

    void updateActiveChannel();
    const interval = setInterval(updateActiveChannel, 10000);

    return () => clearInterval(interval);
  }, [joined, activeChannel]);

  // Scroll to bottom of chat when new comments arrive if user was already near the bottom
  useEffect(() => {
    if (chatEndRef.current && isNearBottomRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [comments]);


  const agentAbortControllerRef = useRef<AbortController | null>(null);

  /**
   * Bios for the people on stage, so the agent knows who it is talking to rather
   * than just their names.
   *
   * Only speakers, and cached for the session: the room list carries no bio, so each
   * one is a separate profile fetch, and doing that for a 300-person audience would
   * be 300 requests to learn about people who are not talking. A missing bio is
   * cached as null so a private profile isn't re-fetched every ten seconds.
   */
  const profileCacheRef = useRef<Map<number, string | null>>(new Map());
  useEffect(() => {
    if (!joined || !agentEnabled) return;
    const speakers = liveUsers.filter((u) => u.isSpeaker).slice(0, 12);
    const missing = speakers.filter((u) => !profileCacheRef.current.has(u.userId));
    if (missing.length === 0) return;
    let cancelled = false;
    void (async () => {
      for (const u of missing) {
        if (cancelled) return;
        try {
          const profile = await getClubhouseUserProfile(u.userId);
          profileCacheRef.current.set(u.userId, profile.bio);
        } catch {
          profileCacheRef.current.set(u.userId, null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [joined, agentEnabled, liveUsers]);

  /**
   * The room as the backend agent should see it, merging the sources that each know
   * part of it: `activeRoomInfo` (polled Clubhouse details — names, moderator flags),
   * `liveUsers`/`speakingVolumes` (the PubNub + Agora feeds, seconds fresher about
   * who is muted, has a hand up, or is talking right now), and the profile cache.
   */
  const buildRoomSnapshot = (): VoiceRoom => {
    // Read through refs, not the render-time values: a turn can take several
    // seconds, and the queue drain that calls this was created in an earlier
    // render. Closing over the room would describe it as it was when someone
    // started speaking, not as it is when the agent answers.
    const info = activeRoomInfoRef.current;
    const live = liveUsersRef.current;
    const volumes = speakingVolumesRef.current;
    const details = info?.users ?? [];
    const ids = new Set<number>([
      ...details.map((u) => u.user_id).filter((id): id is number => id != null),
      ...live.map((u) => u.userId),
    ]);
    const members = [...ids].map((id) => {
      const d = details.find((u) => u.user_id === id);
      const liveUser = live.find((u) => u.userId === id);
      return {
        user_id: id,
        name: d?.name ?? '',
        is_speaker: liveUser?.isSpeaker ?? d?.is_speaker ?? false,
        is_moderator: d?.is_moderator ?? false,
        is_muted: liveUser?.isMuted ?? false,
        hand_raised: liveUser?.handRaised ?? false,
        speaking: (volumes[id] ?? 0) > 5,
        bio: profileCacheRef.current.get(id) ?? null,
      };
    });
    return {
      topic: activeRoomInfo?.topic ?? null,
      club: activeRoomInfo?.club?.name ?? null,
      members,
      my_user_id: myUserId,
      my_name: myProfileName || 'the agent',
    };
  };

  /**
   * One utterance → the backend session → speak / post / stay quiet.
   *
   * Serialized through a queue rather than dropped. The old code returned early
   * whenever `isAgentSpeaking` was set, so anything said while the agent was talking
   * (or thinking) vanished — including the reply to a question someone had just
   * asked it. A room does not pause for the agent, so the utterances are held and
   * processed in order instead.
   */
  const agentQueueRef = useRef<
    {
      text: string;
      source: 'voice' | 'chat';
      force?: boolean;
      speakerName?: string;
      speakerId?: number | null;
      partial?: boolean;
      isSelf?: boolean;
    }[]
  >([]);
  const agentBusyRef = useRef(false);
  const agentSentTextsRef = useRef<Set<string>>(new Set());

  const drainAgentQueue = async () => {
    if (agentBusyRef.current) return;
    agentBusyRef.current = true;
    try {
      while (agentQueueRef.current.length > 0) {
        const item = agentQueueRef.current.shift()!;
        const channel = activeChannelRef.current;
        if (!channel) continue;
        const controller = new AbortController();
        agentAbortControllerRef.current = controller;
        setIsAgentThinking(true);
        try {
          const result = await takeVoiceTurn({
            channel,
            text: item.text,
            speaker: item.speakerName || (item.source === 'chat' ? 'Someone in chat' : 'A speaker'),
            speakerId: item.speakerId,
            source: item.source,
            force: item.force,
            partial: item.partial,
            room: buildRoomSnapshot(),
            config: agentConfigRef.current,
            isSelf: item.isSelf,
            signal: controller.signal,
          });
          setIsAgentThinking(false);
          agentAbortControllerRef.current = null;
          setAgentReason(result.reason);
          if (result.notice) {
            toastsStore.add('info', 'Agent', result.notice);
            if (agentConfigRef.current.postToChat) {
              const textToSend = agentConfigRef.current.robotEmojiPrefix
                ? `🤖 ${result.notice}`
                : result.notice;
              agentSentTextsRef.current.add(textToSend.trim());
              agentSentTextsRef.current.add(result.notice.trim());
              await sendComment(textToSend).catch(() => {});
            }
          }
          if (result.spoke && result.reply) {
            setIsAgentSpeaking(true);
            if (agentConfigRef.current.postToChat) {
              const textToSend = agentConfigRef.current.robotEmojiPrefix
                ? `🤖 ${result.reply}`
                : result.reply;
              agentSentTextsRef.current.add(textToSend.trim());
              agentSentTextsRef.current.add(result.reply.trim());
              await sendComment(textToSend).catch(() => {});
            }
            if (agentConfigRef.current.speak) {
              // If backend provided a natural thinking filler, speak it first
              if (result.filler && agentConfigRef.current.thinkingFiller) {
                await playAgentAudio(result.filler, {
                  voice: agentConfigRef.current.ttsVoice,
                  rate: agentConfigRef.current.ttsRate,
                  pitch: agentConfigRef.current.ttsPitch,
                  volume: agentConfigRef.current.ttsVolume,
                });
              }
              await playAgentAudio(result.reply, {
                voice: agentConfigRef.current.ttsVoice,
                rate: agentConfigRef.current.ttsRate,
                pitch: agentConfigRef.current.ttsPitch,
                volume: agentConfigRef.current.ttsVolume,
              });
            }
            setIsAgentSpeaking(false);
          }
        } catch (e) {
          setIsAgentThinking(false);
          agentAbortControllerRef.current = null;
          setIsAgentSpeaking(false);
          if (
            (e instanceof DOMException && e.name === 'AbortError') ||
            (e instanceof Error && e.name === 'AbortError') ||
            String(e).includes('aborted')
          ) {
            setAgentReason('interrupted');
            break;
          }
          console.error('Voice agent turn failed:', e);
          setAgentReason(e instanceof Error ? e.message : String(e));
        }
      }
    } finally {
      setIsAgentThinking(false);
      agentAbortControllerRef.current = null;
      agentBusyRef.current = false;
    }
  };

  const enqueueUtterance = (
    text: string,
    source: 'voice' | 'chat',
    force = false,
    speakerName?: string,
    speakerId?: number | null,
    partial = false,
    isSelf?: boolean,
  ) => {
    if (!activeChannelRef.current) return;
    lastRoomActivityTsRef.current = Date.now();
    // A partial is only worth acting on while the speaker is still inside it. Queueing
    // one behind a reply that is still being spoken means cutting in on a sentence that
    // finished ten seconds ago, which is the interruption at its most annoying.
    if (partial && (agentBusyRef.current || agentQueueRef.current.length > 0)) return;
    agentQueueRef.current.push({ text, source, force, speakerName, speakerId, partial, isSelf });
    if (agentQueueRef.current.length > 6)
      agentQueueRef.current.splice(0, agentQueueRef.current.length - 6);
    void drainAgentQueue();
  };
  enqueueUtteranceRef.current = enqueueUtterance;

  // Silence Floor Probing: Proactively break long silences in conversational mode
  useEffect(() => {
    if (
      !activeChannel ||
      !agentEnabled ||
      agentConfig.posture !== 'conversational' ||
      !agentConfig.silenceTimeoutS ||
      agentConfig.silenceTimeoutS <= 0
    )
      return;

    const interval = setInterval(() => {
      const elapsedSec = (Date.now() - lastRoomActivityTsRef.current) / 1000;
      if (
        elapsedSec >= agentConfig.silenceTimeoutS &&
        !isAgentSpeaking &&
        agentQueueRef.current.length === 0
      ) {
        lastRoomActivityTsRef.current = Date.now();
        enqueueUtterance(
          'The room has been quiet. Please make a brief, natural remark or conversational observation to keep things moving.',
          'voice',
          false,
          'Room Atmosphere',
          null,
        );
      }
    }, 2500);

    return () => clearInterval(interval);
  }, [activeChannel, agentEnabled, agentConfig.posture, agentConfig.silenceTimeoutS, isAgentSpeaking]);



  // Refs so the queue drain reads current values without being re-created (and
  // without the stale closures the old `useEffect`-driven version captured).
  const activeChannelRef = useRef<string | null>(null);
  const agentConfigRef = useRef(agentConfig);
  const activeRoomInfoRef = useRef<Channel | null>(null);
  const liveUsersRef = useRef(liveUsers);
  const speakingVolumesRef = useRef(speakingVolumes);
  const [myProfileName, setMyProfileName] = useState('');
  useEffect(() => {
    activeChannelRef.current = activeChannel;
  }, [activeChannel]);
  useEffect(() => {
    agentConfigRef.current = agentConfig;
  }, [agentConfig]);
  useEffect(() => {
    activeRoomInfoRef.current = activeRoomInfo;
  }, [activeRoomInfo]);
  useEffect(() => {
    liveUsersRef.current = liveUsers;
  }, [liveUsers]);
  useEffect(() => {
    speakingVolumesRef.current = speakingVolumes;
  }, [speakingVolumes]);

  const handleInterrupt = () => {
    if (agentAbortControllerRef.current) {
      try {
        agentAbortControllerRef.current.abort();
      } catch {
        /* ignore */
      }
      agentAbortControllerRef.current = null;
    }
    stopAgentAudio();
    agentQueueRef.current = [];
    setIsAgentThinking(false);
    setIsAgentSpeaking(false);
    setAgentReason('interrupted');
    toastsStore.add('info', 'Agent Interrupted', 'Stopped speech and cleared queued turns.');
  };

  const handleClearContext = async (silent = false) => {
    const channel = activeChannelRef.current;
    if (!channel) return;
    try {
      if (agentAbortControllerRef.current) {
        try {
          agentAbortControllerRef.current.abort();
        } catch {
          /* ignore */
        }
        agentAbortControllerRef.current = null;
      }
      stopAgentAudio();
      agentQueueRef.current = [];
      agentSentTextsRef.current.clear();
      setIsAgentThinking(false);
      setIsAgentSpeaking(false);
      await resetVoiceMemory(channel);
      setAgentMemory([]);
      if (!silent) {
        toastsStore.add('success', 'Context Cleared', 'Reset LLM conversation memory.');
      }
    } catch (e) {
      console.error('Failed to clear voice context:', e);
    }
  };

  const handleApplyPersona = async (newPersona: string, clearMemory = true) => {
    patchAgentConfig({ persona: newPersona });
    setPersonaDraft(newPersona);
    const channel = activeChannelRef.current;
    if (channel) {
      try {
        if (clearMemory) {
          if (agentAbortControllerRef.current) {
            try {
              agentAbortControllerRef.current.abort();
            } catch {
              /* ignore */
            }
            agentAbortControllerRef.current = null;
          }
          stopAgentAudio();
          agentQueueRef.current = [];
          agentSentTextsRef.current.clear();
          setIsAgentThinking(false);
          setIsAgentSpeaking(false);
          await resetVoiceMemory(channel, { resetPersona: true, persona: newPersona });
          setAgentMemory([]);
          toastsStore.add(
            'success',
            'Persona Applied',
            'Applied new persona and cleared conversation memory so agent adopts it immediately.',
          );
        } else {
          await pushVoiceConfig(channel, { ...agentConfigRef.current, persona: newPersona });
          toastsStore.add('success', 'Persona Updated', 'Saved new persona to active room.');
        }
      } catch (e) {
        toastsStore.add('error', 'Persona Update Failed', e instanceof Error ? e.message : String(e));
      }
    } else {
      toastsStore.add('info', 'Persona Saved', 'Persona saved (will apply on joining room).');
    }
  };

  // Push settings to the backend session whenever they change (and on join, since
  // the session is created there and starts on defaults).
  useEffect(() => {
    if (!activeChannel) return;
    void pushVoiceConfig(activeChannel, agentConfig).catch((e) =>
      console.warn('Failed to sync voice agent config:', e),
    );
  }, [activeChannel, agentConfig]);

  // Mirror what the agent remembers into the Agent tab.
  useEffect(() => {
    if (!activeChannel || !agentEnabled) return;
    const tick = () =>
      void getVoiceState(activeChannel)
        .then((s) => setAgentMemory(s.turns))
        .catch(() => {});
    tick();
    const interval = setInterval(tick, 5000);
    return () => clearInterval(interval);
  }, [activeChannel, agentEnabled]);

  const prevCommentsLengthRef = useRef(0);
  useEffect(() => {
    if (comments.length > prevCommentsLengthRef.current) {
      const newComment = comments[comments.length - 1];
      prevCommentsLengthRef.current = comments.length;
      const rawText = (newComment.text || '').trim();
      // The agent's own posts should not be enqueued to avoid self-reply loops.
      const isAgentSelf =
        rawText.startsWith('🤖') ||
        agentSentTextsRef.current.has(rawText) ||
        (newComment.userName &&
          myProfileName &&
          newComment.userName.toLowerCase() === myProfileName.toLowerCase());
      if (!isAgentSelf) {
        enqueueUtterance(rawText, 'chat');
      }
    }
  }, [comments, myProfileName]);

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

    // Optimistically clear the input, but we don't optimistically add it yet
    // since the hook already does optimistic adding.
    const textToSend = commentText.trim();
    setCommentText('');

    try {
      await sendComment(textToSend);
    } catch (err: unknown) {
      toastsStore.add(
        'error',
        'Failed to send message',
        err instanceof Error ? err.message : String(err),
      );
    }
  };

  const REACTIONS = ['❤️', '😂', '👍', '🙌', '👏', '🔥'];

  // Render the Dedicated Room View when joined
  if (joined && activeChannel) {
    const currentRoom = channels.find((ch) => ch.channel === activeChannel) || activeRoomInfo;
    const isCurrentUserSpeaker =
      activeRoomInfo?.users.find((u) => u.user_id === myUserId)?.is_speaker ?? false;
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
              onClick={() => void acceptSpeakerInvite()}
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
              ➕ Invite
            </button>
            {(() => {
              const amIMod = activeRoomInfo?.users.find(
                (u) => u.user_id === myUserId,
              )?.is_moderator;
              if (amIMod) {
                return (
                  <>
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
                      onClick={handleOpenRoomSettings}
                    >
                      ⚙ Settings
                    </button>
                    <button
                      className="ch-btn-action"
                      style={{
                        padding: '0.4rem 0.8rem',
                        borderRadius: '20px',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        flex: 'none',
                        background: 'rgba(239, 68, 68, 0.15)',
                        color: 'var(--danger)',
                        borderColor: 'rgba(239, 68, 68, 0.3)',
                      }}
                      onClick={async () => {
                        if (confirm('Are you sure you want to end this room for everyone?')) {
                          try {
                            await endClubhouseChannel(activeChannel);
                            toastsStore.add('success', 'Room Ended', 'You ended the room.');
                            void leaveRoom(activeChannel);
                          } catch (err) {
                            toastsStore.add('error', 'Failed', err instanceof Error ? err.message : 'Could not end room');
                          }
                        }
                      }}
                      title="End room for everyone"
                    >
                      🛑 End Room
                    </button>
                  </>
                );
              }
              return null;
            })()}
            <button
              className="ch-btn-action"
              style={{
                padding: '0.4rem 0.8rem',
                borderRadius: '20px',
                fontSize: '0.75rem',
                fontWeight: 600,
                flex: 'none',
                background: showNetworkModal ? 'rgba(56, 189, 248, 0.2)' : 'rgba(255, 255, 255, 0.05)',
                color: showNetworkModal ? '#38bdf8' : '#94a3b8',
                border: showNetworkModal ? '1px solid rgba(56, 189, 248, 0.4)' : '1px solid transparent',
              }}
              onClick={() => setShowNetworkModal(true)}
              title="Inspect WebRTC, Agora UDP, PubNub WSS, protocols and IP domains"
            >
              📡 Network Insights
            </button>
            <button
              className="ch-btn-action"
              style={{
                padding: '0.4rem 0.8rem',
                borderRadius: '20px',
                fontSize: '0.75rem',
                fontWeight: 600,
                flex: 'none',
                background: showAgentSidebar ? 'rgba(167, 139, 250, 0.18)' : 'rgba(255, 255, 255, 0.05)',
                color: showAgentSidebar ? '#c4b5fd' : '#94a3b8',
                border: showAgentSidebar ? '1px solid rgba(167, 139, 250, 0.4)' : '1px solid transparent',
              }}
              onClick={() => setShowAgentSidebar((v) => !v)}
              title="Toggle Voice Agent panel on the right side"
            >
              🤖 {showAgentSidebar ? 'Agent Panel (Open)' : 'Agent Panel'}
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

        {/* Main Content Area: 2-Column Side-by-Side Layout with Moveable Vertical Splitter */}
        <div
          ref={splitContainerRef}
          className="ch-room-scroller"
          style={{
            display: 'flex',
            flexDirection: 'row',
            flex: 1,
            minHeight: 0,
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          {/* Left Column: Stage (Participants on Top + Live Chat Below) */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              minHeight: 0,
              minWidth: '240px',
              overflow: 'hidden',
            }}
          >
              {/* 1. Participants Area (Top Half) */}
              <div
                className="ch-participants-section"
                style={{
                  flex: '0 0 auto',
                  maxHeight: '42%',
                  overflowY: 'auto',
                  borderBottom: '1px solid rgba(255,255,255,0.08)',
                  background: 'rgba(29, 32, 38, 0.5)',
                }}
              >
                {/* Speakers */}
                {speakers.length > 0 && (
                  <>
                    <div
                      className="ch-section-heading"
                      style={{
                        borderTop: 'none',
                        background: 'transparent',
                        padding: '0.6rem 0.8rem 0.4rem',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '0.72rem',
                          color: '#38bdf8',
                          textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          fontWeight: 700,
                        }}
                      >
                        🎙️ Speakers ({speakers.length})
                      </span>
                    </div>
                    <div className="ch-speakers-grid">
                      {speakers.map((u) => renderUserCard(u, true))}
                    </div>
                  </>
                )}

                {/* Audience */}
                {audience.length > 0 && (
                  <>
                    <div
                      className="ch-section-heading"
                      style={{
                        marginTop: '0.3rem',
                        borderTop: 'none',
                        background: 'transparent',
                        padding: '0.4rem 0.8rem',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <span
                        style={{
                          fontSize: '0.72rem',
                          color: '#64748b',
                          textTransform: 'uppercase',
                          letterSpacing: '0.05em',
                          fontWeight: 700,
                        }}
                      >
                        🎧 Audience ({audience.length})
                      </span>
                    </div>
                    <div className="ch-listeners-grid">
                      {audience.map((u) => renderUserCard(u, false))}
                    </div>
                  </>
                )}
              </div>

              {/* 2. Live Chat / Comments Feed (Directly Underneath Participants) */}
              <div
                className="ch-chat-section"
                style={{
                  flex: 1,
                  display: 'flex',
                  flexDirection: 'column',
                  minHeight: 0,
                  overflow: 'hidden',
                  background: '#0d1117',
                }}
              >
                <div
                  style={{
                    padding: '0.4rem 0.8rem',
                    background: 'rgba(255,255,255,0.03)',
                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                    fontSize: '0.72rem',
                    fontWeight: 700,
                    color: 'var(--text-dim, #94a3b8)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                  }}
                >
                  <span>💬 Live Room Chat ({comments.length})</span>
                  <span style={{ fontSize: '0.65rem', color: '#64748b', textTransform: 'none' }}>
                    Real-time audience & stage messages
                  </span>
                </div>

                <div
                  ref={chatScrollRef}
                  className="ch-chat-scroll"
                  style={{ flex: 1, overflowY: 'auto', padding: '0.75rem' }}
                  onScroll={(e) => {
                    const el = e.currentTarget;
                    chatScrollTopRef.current = el.scrollTop;
                    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
                    isNearBottomRef.current = distanceToBottom < 60;
                  }}
                >
                  {comments.length === 0 ? (
                    <div className="ch-chat-empty" style={{ minHeight: '120px' }}>
                      <span style={{ fontSize: '1.5rem' }}>💬</span>
                      <span>No messages yet. Send a message to chat with the room!</span>
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

          {/* Moveable Vertical Splitter Handle */}
          {showAgentSidebar && (
            <div
              role="separator"
              aria-orientation="vertical"
              title="Drag to resize panes"
              onMouseDown={(e) => {
                e.preventDefault();
                setIsDraggingSplitter(true);
              }}
              style={{
                width: '6px',
                cursor: 'col-resize',
                background: isDraggingSplitter ? 'var(--accent, #6366f1)' : 'rgba(255,255,255,0.06)',
                transition: isDraggingSplitter ? 'none' : 'background 0.15s ease',
                position: 'relative',
                zIndex: 10,
                flexShrink: 0,
                userSelect: 'none',
              }}
              onMouseEnter={(e) => {
                if (!isDraggingSplitter) (e.currentTarget as HTMLElement).style.background = 'rgba(167, 139, 250, 0.4)';
              }}
              onMouseLeave={(e) => {
                if (!isDraggingSplitter) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)';
              }}
            />
          )}

          {/* Right Column: Voice Agent Settings Sidebar */}
          {showAgentSidebar && (
            <div
              className="ch-agent-section"
              style={{
                width: `${sidebarWidth}px`,
                minWidth: '240px',
                flexShrink: 0,
                background: '#12151b',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 0,
                overflow: 'hidden',
              }}
            >
              {/* Sticky Sidebar Header */}
              <div
                style={{
                  padding: '0.65rem 0.9rem',
                  background: '#161922',
                  borderBottom: '1px solid rgba(255,255,255,0.08)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  flexShrink: 0,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                  <span style={{ fontSize: '0.82rem', fontWeight: 700, color: 'var(--accent)' }}>
                    🤖 Voice Agent
                  </span>
                  {agentEnabled && (
                    <>
                      {isAgentSpeaking ? (
                        <div
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '3px',
                            background: 'rgba(34, 197, 94, 0.15)',
                            border: '1px solid rgba(34, 197, 94, 0.3)',
                            padding: '1px 6px',
                            borderRadius: '10px',
                          }}
                          title="Agent is currently speaking audio into the room"
                        >
                          <span style={{ fontSize: '0.62rem', color: 'var(--success)', fontWeight: 700 }}>
                            SPEAKING
                          </span>
                          <span style={{ display: 'inline-block', width: '2px', height: '8px', background: 'var(--success)', borderRadius: '1px' }} />
                          <span style={{ display: 'inline-block', width: '2px', height: '12px', background: 'var(--success)', borderRadius: '1px' }} />
                          <span style={{ display: 'inline-block', width: '2px', height: '6px', background: 'var(--success)', borderRadius: '1px' }} />
                        </div>
                      ) : isAgentThinking ? (
                        <span
                          style={{
                            fontSize: '0.62rem',
                            background: 'rgba(56, 189, 248, 0.2)',
                            color: 'var(--info)',
                            border: '1px solid rgba(56, 189, 248, 0.3)',
                            padding: '1px 6px',
                            borderRadius: 6,
                            fontWeight: 700,
                          }}
                          title="Agent is generating a response"
                        >
                          ⚡ THINKING
                        </span>
                      ) : (
                        <span
                          style={{
                            fontSize: '0.62rem',
                            background: 'rgba(167, 139, 250, 0.2)',
                            color: 'var(--accent)',
                            padding: '1px 6px',
                            borderRadius: 6,
                            fontWeight: 700,
                          }}
                        >
                          ACTIVE
                        </span>
                      )}
                    </>
                  )}
                </div>
                <button
                  className={`ch-btn-action ${agentEnabled ? 'mic-active' : ''}`}
                  onClick={() => patchAgentConfig({ enabled: !agentEnabled })}
                  style={{ padding: '0.25rem 0.65rem', fontSize: '0.75rem', borderRadius: '12px' }}
                >
                  {agentEnabled ? '🤖 ON' : '🤖 OFF'}
                </button>
              </div>

              {/* Scrollable Sidebar Body */}
              <div
                style={{
                  flex: 1,
                  overflowY: 'auto',
                  padding: '0.85rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.85rem',
                }}
              >
                {!agentEnabled && (
                  <div
                    style={{
                      padding: '1rem',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px dashed rgba(255,255,255,0.1)',
                      borderRadius: '8px',
                      textAlign: 'center',
                      color: '#94a3b8',
                      fontSize: '0.78rem',
                    }}
                  >
                    <p style={{ margin: '0 0 0.5rem' }}>Voice Agent is currently paused in this room.</p>
                    <button
                      className="ch-btn-action mic-active"
                      onClick={() => patchAgentConfig({ enabled: true })}
                      style={{ padding: '0.35rem 0.8rem', fontSize: '0.75rem' }}
                    >
                      Turn On Agent
                    </button>
                  </div>
                )}

                  {agentEnabled && (
                    <div
                      style={{
                        padding: '1rem',
                        background: 'rgba(0,0,0,0.2)',
                        borderRadius: '8px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '1rem',
                      }}
                    >
                      {/* General Output & Trigger Modes */}
                      <div
                        style={{
                          display: 'flex',
                          gap: '0.8rem',
                          flexWrap: 'wrap',
                          padding: '0.6rem 0.8rem',
                          background: 'rgba(255,255,255,0.02)',
                          borderRadius: '8px',
                          border: '1px solid rgba(255,255,255,0.05)',
                        }}
                      >
                        {(
                          [
                            ['respondToVoice', '🎙️ Voice (Stage)'],
                            ['respondToChat', '💬 Live Chat'],
                            ['speak', '🔊 Speak aloud (TTS)'],
                            ['postToChat', '📝 Post to chat'],
                            ['robotEmojiPrefix', '🤖 Prefix with robot'],
                          ] as const
                        ).map(([key, label]) => (
                          <label
                            key={key}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '0.4rem',
                              fontSize: '0.74rem',
                              color: 'var(--text)',
                              cursor: 'pointer',
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={agentConfig[key]}
                              onChange={(e) => patchAgentConfig({ [key]: e.target.checked })}
                              style={{ accentColor: 'var(--accent)' }}
                            />
                            {label}
                          </label>
                        ))}
                      </div>

                      {/* CARD 1: Persona & Prompting */}
                      <div
                        style={{
                          background: 'rgba(255,255,255,0.025)',
                          border: '1px solid rgba(255,255,255,0.07)',
                          borderRadius: '8px',
                          padding: '0.75rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.6rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--accent)' }}>
                            🎭 Persona &amp; Identity
                          </span>
                          <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                            {personaDraft.length} chars
                          </span>
                        </div>

                        {/* Quick Persona Chips */}
                        <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                          {DEFAULT_PRESETS.map((p, i) => (
                            <button
                              key={i}
                              type="button"
                              className="ch-btn-action"
                              style={{
                                padding: '0.2rem 0.5rem',
                                fontSize: '0.68rem',
                                borderRadius: '12px',
                                background: personaDraft === p.prompt ? 'rgba(168, 85, 247, 0.25)' : 'rgba(255,255,255,0.04)',
                                border: personaDraft === p.prompt ? '1px solid var(--accent)' : '1px solid rgba(255,255,255,0.08)',
                                color: personaDraft === p.prompt ? 'var(--text-strong)' : 'var(--text)',
                                fontWeight: personaDraft === p.prompt ? 600 : 400,
                              }}
                              title={p.prompt}
                              onClick={() => {
                                setSelectedPreset(p.name);
                                void handleApplyPersona(p.prompt, true);
                              }}
                            >
                              {p.chipLabel || p.name}
                            </button>
                          ))}
                        </div>

                        {/* Preset Selector & Action Buttons */}
                        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
                          <select
                            value={selectedPreset}
                            onChange={(e) => {
                              const val = e.target.value;
                              setSelectedPreset(val);
                              if (val) {
                                const p = agentPromptPresets.find((pr) => pr.prompt === val);
                                if (p) {
                                  void handleApplyPersona(p.prompt, true);
                                }
                              }
                            }}
                            style={{
                              padding: '0 0.4rem',
                              fontSize: '0.7rem',
                              background: 'var(--bg-elevated)',
                              color: 'var(--text)',
                              border: '1px solid var(--border)',
                              borderRadius: '4px',
                              flex: 1,
                              minWidth: '130px',
                            }}
                          >
                            <option value="">Choose preset...</option>
                            {agentPromptPresets.map((p, i) => (
                              <option key={i} value={p.prompt}>
                                {p.name}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className="ch-btn-action"
                            style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem' }}
                            title="Save current prompt as a custom preset"
                            onClick={() => {
                              const name = prompt('Name for this custom preset:');
                              if (name && name.trim()) {
                                const newPresets = [
                                  ...agentPromptPresets,
                                  { name: name.trim(), prompt: personaDraft },
                                ];
                                setAgentPromptPresets(newPresets);
                                localStorage.setItem('agentPresets', JSON.stringify(newPresets));
                                toastsStore.add('success', 'Preset Saved', `Saved preset "${name.trim()}".`);
                              }
                            }}
                          >
                            Save Preset...
                          </button>
                          <button
                            type="button"
                            className="ch-btn-action"
                            style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem', background: 'var(--bg-elevated)' }}
                            title="Restore default assistant persona and clear conversation memory"
                            onClick={() => {
                              setSelectedPreset('');
                              void handleApplyPersona(DEFAULT_VOICE_CONFIG.persona, true);
                            }}
                          >
                            Reset Default
                          </button>
                        </div>

                        {/* Textarea */}
                        <textarea
                          value={personaDraft}
                          onChange={(e) => {
                            setPersonaDraft(e.target.value);
                            patchAgentConfig({ persona: e.target.value });
                          }}
                          placeholder="System Prompt / Persona (instruction for the voice agent)"
                          rows={3}
                          style={{
                            width: '100%',
                            minHeight: '80px',
                            padding: '0.65rem',
                            fontSize: '0.78rem',
                            background: 'var(--bg-elevated)',
                            color: 'var(--text)',
                            border: '1px solid var(--border)',
                            borderRadius: '6px',
                            resize: 'vertical',
                          }}
                        />

                        {/* Apply & Save Buttons */}
                        <div style={{ display: 'flex', gap: '0.4rem', justifyContent: 'flex-end' }}>
                          <button
                            type="button"
                            className="ch-btn-action"
                            style={{
                              padding: '0.25rem 0.6rem',
                              fontSize: '0.72rem',
                              background: 'var(--bg-elevated)',
                              border: '1px solid var(--border)',
                            }}
                            title="Update persona for upcoming turns without erasing existing context memory"
                            onClick={() => void handleApplyPersona(personaDraft, false)}
                          >
                            💾 Save Prompt
                          </button>
                          <button
                            type="button"
                            className="ch-btn-action"
                            style={{
                              padding: '0.25rem 0.75rem',
                              fontSize: '0.72rem',
                              background: 'var(--accent)',
                              color: 'var(--text-strong)',
                              border: 'none',
                              fontWeight: 600,
                            }}
                            title="Apply new persona and wipe conversation memory so the agent switches identity immediately"
                            onClick={() => void handleApplyPersona(personaDraft, true)}
                          >
                            ✨ Apply &amp; Clear Memory
                          </button>
                        </div>
                      </div>

                      {/* CARD 2: Voice & Speech Synthesis (TTS) */}
                      <div
                        style={{
                          background: 'rgba(255,255,255,0.025)',
                          border: '1px solid rgba(255,255,255,0.07)',
                          borderRadius: '8px',
                          padding: '0.75rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.6rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--info)' }}>
                            🔊 Voice &amp; Speech Synthesis (TTS)
                          </span>
                          <button
                            type="button"
                            className="ch-btn-action"
                            style={{
                              padding: '0.15rem 0.55rem',
                              fontSize: '0.68rem',
                              background: 'rgba(56, 189, 248, 0.15)',
                              color: 'var(--info)',
                              border: '1px solid rgba(56, 189, 248, 0.3)',
                              borderRadius: '10px',
                            }}
                            title="Audition selected neural voice, speed, and pitch directly on your speakers"
                            onClick={() => {
                              void previewTtsVoice({
                                voice: agentConfig.ttsVoice,
                                rate: agentConfig.ttsRate,
                                pitch: agentConfig.ttsPitch,
                                volume: agentConfig.ttsVolume,
                              });
                            }}
                          >
                            ▶️ Test Voice
                          </button>
                        </div>

                        {/* Voice Selector */}
                        <div style={agentFieldStyle}>
                          <span style={agentLabelStyle}>Neural Voice Model:</span>
                          <select
                            style={agentInputStyle}
                            value={agentConfig.ttsVoice || 'en-US-ChristopherNeural'}
                            onChange={(e) => patchAgentConfig({ ttsVoice: e.target.value })}
                          >
                            {ttsVoices.length > 0 ? (
                              ttsVoices.map((v) => (
                                <option key={v.name} value={v.name}>
                                  {v.label || v.name}
                                </option>
                              ))
                            ) : (
                              <>
                                <option value="en-US-AndrewMultilingualNeural">Andrew (US Male - Ultra-Natural &amp; Warm)</option>
                                <option value="en-US-AvaMultilingualNeural">Ava (US Female - Ultra-Natural &amp; Conversational)</option>
                                <option value="en-US-BrianMultilingualNeural">Brian (US Male - Engaging &amp; Friendly)</option>
                                <option value="en-US-EmmaMultilingualNeural">Emma (US Female - Cheerful &amp; Clear)</option>
                                <option value="en-US-ChristopherNeural">Christopher (US Male - Warm &amp; Authoritative)</option>
                                <option value="en-US-JennyNeural">Jenny (US Female - Clear &amp; Conversational)</option>
                                <option value="en-US-GuyNeural">Guy (US Male - Casual &amp; Friendly)</option>
                                <option value="en-US-AriaNeural">Aria (US Female - Expressive)</option>
                                <option value="en-US-EricNeural">Eric (US Male - Crisp)</option>
                                <option value="en-GB-RyanNeural">Ryan (UK Male - Natural)</option>
                                <option value="en-GB-SoniaNeural">Sonia (UK Female - Clear)</option>
                                <option value="en-AU-NatNeural">Nat (AU Female)</option>
                                <option value="en-AU-WilliamNeural">William (AU Male)</option>
                                <option value="en-IN-NeerjaNeural">Neerja (IN Female - Professional)</option>
                                <option value="ja-JP-KeitaNeural">Keita (Japanese Male)</option>
                                <option value="es-ES-AlvaroNeural">Alvaro (Spanish Male)</option>
                                <option value="fr-FR-HenriNeural">Henri (French Male)</option>
                                <option value="de-DE-ConradNeural">Conrad (German Male)</option>
                              </>
                            )}
                          </select>
                        </div>

                        {/* Audio Tuning: Volume, Rate, Pitch */}
                        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '85px' }}>
                            <span style={agentLabelStyle}>Output Volume:</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.ttsVolume || '+0%'}
                              onChange={(e) => patchAgentConfig({ ttsVolume: e.target.value })}
                            >
                              <option value="-50%">50% (Soft)</option>
                              <option value="-25%">75%</option>
                              <option value="+0%">100% (Normal)</option>
                              <option value="+25%">125%</option>
                              <option value="+50%">150% (Loud)</option>
                            </select>
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '85px' }}>
                            <span style={agentLabelStyle}>Speech Rate:</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.ttsRate || '+0%'}
                              onChange={(e) => patchAgentConfig({ ttsRate: e.target.value })}
                            >
                              <option value="-20%">0.8x (Slow)</option>
                              <option value="-10%">0.9x</option>
                              <option value="+0%">1.0x (Normal)</option>
                              <option value="+10%">1.1x (Fast)</option>
                              <option value="+20%">1.2x</option>
                              <option value="+30%">1.3x</option>
                            </select>
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '85px' }}>
                            <span style={agentLabelStyle}>Voice Pitch:</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.ttsPitch || '+0Hz'}
                              onChange={(e) => patchAgentConfig({ ttsPitch: e.target.value })}
                            >
                              <option value="-10Hz">-10Hz (Deeper)</option>
                              <option value="-5Hz">-5Hz</option>
                              <option value="+0Hz">Default (+0Hz)</option>
                              <option value="+5Hz">+5Hz</option>
                              <option value="+10Hz">+10Hz (Higher)</option>
                            </select>
                          </div>
                        </div>
                      </div>

                      {/* CARD 3: Speech Recognition (STT) & Floor Dynamics */}
                      <div
                        style={{
                          background: 'rgba(255,255,255,0.025)',
                          border: '1px solid rgba(255,255,255,0.07)',
                          borderRadius: '8px',
                          padding: '0.75rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.6rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--success)' }}>
                            🎙️ Hearing &amp; Speech Recognition (STT)
                          </span>
                          <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                            Whisper / VAD Filtered
                          </span>
                        </div>

                        {/* Live STT Transcript Pill */}
                        <div
                          style={{
                            padding: '0.45rem 0.65rem',
                            background: 'rgba(0, 0, 0, 0.3)',
                            border: '1px solid rgba(255, 255, 255, 0.08)',
                            borderRadius: '6px',
                            fontSize: '0.72rem',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '2px',
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-dim)', fontSize: '0.65rem' }}>
                            <span>🎙️ LIVE STT RECOGNITION</span>
                            {lastHeardSpeech && <span>{lastHeardSpeech.time}</span>}
                          </div>
                          {lastHeardSpeech ? (
                            <div style={{ color: 'var(--text)' }}>
                              <strong style={{ color: 'var(--info)' }}>{lastHeardSpeech.speaker || 'Room'}:</strong>{' '}
                              <span style={{ fontStyle: 'italic' }}>"{lastHeardSpeech.text}"</span>
                            </div>
                          ) : (
                            <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                              Microphone listening for room audio...
                            </span>
                          )}
                        </div>

                        {/* When to speak & Wake Words */}
                        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '140px' }}>
                            <span style={agentLabelStyle}>When to speak:</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.posture}
                              onChange={(e) =>
                                patchAgentConfig({
                                  posture: e.target.value as VoiceAgentConfig['posture'],
                                })
                              }
                            >
                              <option value="addressed">Only when addressed</option>
                              <option value="conversational">Conversational (with cooldown)</option>
                              <option value="always">Always (replies to everything)</option>
                              <option value="interject">Interject (cuts in mid-sentence)</option>
                            </select>
                          </div>

                          {agentConfig.posture === 'interject' && (
                            <div style={{ ...agentFieldStyle, flex: 1, minWidth: '100px' }}>
                              <span style={agentLabelStyle}>Cut in after:</span>
                              <input
                                type="number"
                                min={2}
                                max={60}
                                step={1}
                                style={agentInputStyle}
                                value={agentConfig.interjectAfterS ?? 6}
                                onChange={(e) =>
                                  patchAgentConfig({
                                    interjectAfterS: Math.max(2, Number(e.target.value) || 6),
                                  })
                                }
                              />
                            </div>
                          )}

                          <div style={{ ...agentFieldStyle, flex: 2, minWidth: '150px' }}>
                            <span style={agentLabelStyle}>Wake words (comma separated):</span>
                            <input
                              type="text"
                              style={agentInputStyle}
                              value={wakeWordsInput}
                              onChange={(e) => {
                                const val = e.target.value;
                                setWakeWordsInput(val);
                                patchAgentConfig({
                                  wakeWords: val
                                    .split(',')
                                    .map((w) => w.trim().toLowerCase())
                                    .filter(Boolean),
                                });
                              }}
                              onBlur={() => {
                                const cleaned = wakeWordsInput
                                  .split(',')
                                  .map((w) => w.trim().toLowerCase())
                                  .filter(Boolean);
                                setWakeWordsInput(cleaned.join(', '));
                              }}
                              placeholder="agent, assistant, bot"
                            />
                          </div>
                        </div>

                        {/* Conversational Timing */}
                        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '140px' }}>
                            <span style={agentLabelStyle}>Turn Eagerness (Speed):</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.turnEagerness || 'normal'}
                              onChange={(e) => {
                                const eagerness = e.target.value as 'fast' | 'normal' | 'patient';
                                const delay = eagerness === 'fast' ? 400 : eagerness === 'patient' ? 1200 : 750;
                                patchAgentConfig({ turnEagerness: eagerness, endpointingDelayMs: delay });
                              }}
                            >
                              <option value="fast">⚡ Fast (400ms pause)</option>
                              <option value="normal">⚖️ Normal (750ms pause)</option>
                              <option value="patient">🧘 Patient (1200ms pause)</option>
                            </select>
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '140px' }}>
                            <span style={agentLabelStyle}>Take Turn After Silence:</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.silenceTimeoutS || 0}
                              onChange={(e) => patchAgentConfig({ silenceTimeoutS: Number(e.target.value) })}
                            >
                              <option value={0}>Disabled</option>
                              <option value={15}>After 15s quiet</option>
                              <option value={30}>After 30s quiet</option>
                              <option value={60}>After 60s quiet</option>
                            </select>
                          </div>
                        </div>

                        {/* Natural Flow & Interruption Toggles */}
                        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', padding: '0.1rem 0' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.74rem', color: 'var(--text)', cursor: 'pointer' }}>
                            <input
                              type="checkbox"
                              checked={agentConfig.thinkingFiller !== false}
                              onChange={(e) => patchAgentConfig({ thinkingFiller: e.target.checked })}
                              style={{ accentColor: 'var(--accent)' }}
                            />
                            Soft thinking filler ("Hmm, let me check...")
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.74rem', color: 'var(--text)', cursor: 'pointer' }}>
                            <input
                              type="checkbox"
                              checked={agentConfig.allowBargeIn !== false}
                              onChange={(e) => patchAgentConfig({ allowBargeIn: e.target.checked })}
                              style={{ accentColor: 'var(--accent)' }}
                            />
                            Allow users to interrupt agent while speaking (Barge-in)
                          </label>
                        </div>
                      </div>

                      {/* CARD 4: Model & Reasoning Parameters */}
                      <div
                        style={{
                          background: 'rgba(255,255,255,0.025)',
                          border: '1px solid rgba(255,255,255,0.07)',
                          borderRadius: '8px',
                          padding: '0.75rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.6rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--warning)' }}>
                            🧠 Model &amp; Reasoning Parameters
                          </span>
                          {agentEngineStatus?.provider && (
                            <span style={{ fontSize: '0.65rem', color: 'var(--text-dim)' }}>
                              Provider: {agentEngineStatus.provider}
                            </span>
                          )}
                        </div>

                        {/* Engine Model Selector */}
                        <div style={agentFieldStyle}>
                          <label style={agentLabelStyle}>Active Engine Model:</label>
                          <select
                            style={agentInputStyle}
                            value={agentEngineStatus?.model || ''}
                            disabled={switchingModel}
                            onChange={async (e) => {
                              const newModel = e.target.value;
                              if (!agentEngineStatus?.provider) return;
                              setSwitchingModel(true);
                              try {
                                await saveAgentConfig(
                                  newModel,
                                  agentEngineStatus.provider,
                                  agentEngineStatus.endpoint || undefined,
                                );
                                const updated = await getAgentStatus();
                                setAgentEngineStatus(updated);
                                toastsStore.add('success', 'Model Switched', `Voice Agent now using ${newModel}`);
                              } catch (err) {
                                toastsStore.add('error', 'Model Switch Failed', err instanceof Error ? err.message : String(err));
                              } finally {
                                setSwitchingModel(false);
                              }
                            }}
                          >
                            {agentEngineStatus?.available_models && agentEngineStatus.available_models.length > 0 ? (
                              agentEngineStatus.available_models.map((m) => (
                                <option key={m} value={m}>
                                  {m.includes('3b') || m.includes('2b') || m.includes('1b') ? `⚡ ${m} (Fast - Recommended)` : m}
                                </option>
                              ))
                            ) : (
                              <option value={agentEngineStatus?.model || ''}>{agentEngineStatus?.model || 'Loading...'}</option>
                            )}
                          </select>
                        </div>

                        {/* Model override, Temperature, Max Tokens */}
                        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                          <div style={{ ...agentFieldStyle, flex: 2, minWidth: '150px' }}>
                            <span style={agentLabelStyle}>Model Override (optional):</span>
                            <input
                              type="text"
                              placeholder="System default"
                              value={agentConfig.model || ''}
                              onChange={(e) =>
                                patchAgentConfig({ model: e.target.value.trim() || undefined })
                              }
                              style={agentInputStyle}
                            />
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '85px' }}>
                            <span style={agentLabelStyle}>Temperature ({agentConfig.temperature}):</span>
                            <input
                              type="number"
                              min="0"
                              max="1.5"
                              step="0.1"
                              value={agentConfig.temperature}
                              onChange={(e) =>
                                patchAgentConfig({ temperature: Number(e.target.value) })
                              }
                              style={agentInputStyle}
                            />
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '85px' }}>
                            <span style={agentLabelStyle}>Max Tokens:</span>
                            <input
                              type="number"
                              min="20"
                              max="400"
                              step="10"
                              value={agentConfig.maxTokens}
                              onChange={(e) =>
                                patchAgentConfig({ maxTokens: Number(e.target.value) })
                              }
                              style={agentInputStyle}
                            />
                          </div>
                        </div>

                        {/* Memory and Cooldown */}
                        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '110px' }}>
                            <span style={agentLabelStyle}>Remember turns:</span>
                            <input
                              type="number"
                              min="0"
                              max="40"
                              step="2"
                              style={agentInputStyle}
                              value={agentConfig.memoryTurns}
                              onChange={(e) =>
                                patchAgentConfig({ memoryTurns: Number(e.target.value) })
                              }
                            />
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '110px' }}>
                            <span style={agentLabelStyle}>Cooldown (seconds):</span>
                            <input
                              type="number"
                              min="0"
                              max="60"
                              step="1"
                              style={agentInputStyle}
                              value={agentConfig.cooldownS}
                              onChange={(e) =>
                                patchAgentConfig({ cooldownS: Number(e.target.value) })
                              }
                            />
                          </div>
                          <div style={{ ...agentFieldStyle, flex: 1, minWidth: '130px' }}>
                            <span style={agentLabelStyle}>Look things up:</span>
                            <select
                              style={agentInputStyle}
                              value={agentConfig.retrieval}
                              onChange={(e) =>
                                patchAgentConfig({
                                  retrieval: e.target.value as VoiceAgentConfig['retrieval'],
                                })
                              }
                            >
                              <option value="off">Never</option>
                              <option value="command">Only on /agent search</option>
                              <option value="auto">Automatically for questions</option>
                            </select>
                          </div>
                        </div>
                      </div>



                      {/* Why it did or didn't speak, and what it currently remembers. */}
                      <div
                        style={{
                          background: 'rgba(255,255,255,0.03)',
                          borderRadius: '8px',
                          padding: '0.75rem',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '0.5rem',
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <span>Last turn: <strong style={{ color: 'var(--text-strong)' }}>{agentReason ?? '—'}</strong></span>
                            {isAgentThinking && (
                              <span style={{ color: 'var(--info)', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                                ⚡ Generating response...
                              </span>
                            )}
                            {isAgentSpeaking && (
                              <span style={{ color: 'var(--success)', display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                                🔊 Speaking
                              </span>
                            )}
                          </div>
                          <button
                            type="button"
                            className="ch-btn-action"
                            style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem', background: 'var(--bg-elevated)' }}
                            onClick={() => void handleClearContext(false)}
                          >
                            🧹 Clear Context Window
                          </button>
                        </div>
                        {speechError && (
                          <div style={{ fontSize: '0.75rem', color: 'var(--warning)' }}>
                            ⚠️ {speechError}
                          </div>
                        )}
                        {agentMemory.length > 0 && (
                          <details>
                            <summary
                              style={{ fontSize: '0.75rem', color: '#94a3b8', cursor: 'pointer' }}
                            >
                              Conversation memory ({agentMemory.length})
                            </summary>
                            <div
                              style={{
                                maxHeight: '160px',
                                overflowY: 'auto',
                                marginTop: '0.5rem',
                                display: 'flex',
                                flexDirection: 'column',
                                gap: '0.25rem',
                              }}
                            >
                              {agentMemory.map((t, i) => (
                                <div
                                  key={i}
                                  style={{
                                    fontSize: '0.72rem',
                                    color: t.role === 'agent' ? '#a5b4fc' : '#cbd5e1',
                                  }}
                                >
                                  <strong>
                                    {t.role === 'agent' ? 'Agent' : t.speaker || 'Room'}:
                                  </strong>{' '}
                                  {t.text}
                                </div>
                              ))}
                            </div>
                          </details>
                        )}


                        {/* People Knowledge & Profile Memory */}
                        <div
                          style={{
                            background: 'rgba(0,0,0,0.25)',
                            borderRadius: '8px',
                            padding: '0.75rem',
                            border: '1px solid rgba(255,255,255,0.06)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '0.6rem',
                            marginTop: '0.4rem',
                          }}
                        >
                          <div
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                            }}
                          >
                            <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#38bdf8' }}>
                              👥 People Knowledge ({peopleMemory.length})
                            </span>
                            <button
                              className="ch-btn-action"
                              style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem' }}
                              onClick={() => void refreshPeopleMemory()}
                              disabled={peopleMemoryLoading}
                            >
                              {peopleMemoryLoading ? '...' : '🔄 Refresh'}
                            </button>
                          </div>

                          <input
                            type="text"
                            placeholder="Search remembered people..."
                            value={peopleMemoryQuery}
                            onChange={(e) => {
                              setPeopleMemoryQuery(e.target.value);
                              void refreshPeopleMemory(e.target.value);
                            }}
                            // Vertical padding stays out — see agentInputStyle.
                            style={{ ...agentInputStyle, fontSize: '0.75rem' }}
                          />

                          {peopleMemory.length === 0 ? (
                            <p style={{ fontSize: '0.72rem', color: '#64748b', margin: '0.2rem 0' }}>
                              No users remembered yet. The agent automatically learns people and their facts when they speak or chat!
                            </p>
                          ) : (
                            <div
                              style={{
                                maxHeight: '220px',
                                overflowY: 'auto',
                                display: 'flex',
                                flexDirection: 'column',
                                gap: '0.5rem',
                              }}
                            >
                              {peopleMemory.map((p) => {
                                const noteInput = newNoteInputs[p.user_id] || '';
                                return (
                                  <div
                                    key={p.user_id}
                                    style={{
                                      background: 'rgba(255,255,255,0.03)',
                                      borderRadius: '6px',
                                      padding: '0.5rem 0.6rem',
                                      border: '1px solid rgba(255,255,255,0.04)',
                                      display: 'flex',
                                      flexDirection: 'column',
                                      gap: '0.3rem',
                                    }}
                                  >
                                    <div
                                      style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'space-between',
                                      }}
                                    >
                                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                        {p.photo_url ? (
                                          <img
                                            src={p.photo_url}
                                            alt=""
                                            style={{ width: '20px', height: '20px', borderRadius: '6px' }}
                                          />
                                        ) : (
                                          <div
                                            style={{
                                              width: '20px',
                                              height: '20px',
                                              borderRadius: '6px',
                                              background: 'var(--bg-elevated)',
                                              fontSize: '0.6rem',
                                              display: 'flex',
                                              alignItems: 'center',
                                              justifyContent: 'center',
                                              color: '#cbd5e1',
                                            }}
                                          >
                                            {p.name.slice(0, 1).toUpperCase()}
                                          </div>
                                        )}
                                        <span style={{ fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-strong)' }}>
                                          {p.name}
                                        </span>
                                        {p.username && (
                                          <span style={{ fontSize: '0.7rem', color: '#64748b' }}>
                                            @{p.username}
                                          </span>
                                        )}
                                      </div>
                                      <button
                                        style={{
                                          background: 'transparent',
                                          border: 'none',
                                          color: '#ef4444',
                                          fontSize: '0.65rem',
                                          cursor: 'pointer',
                                          padding: '2px 4px',
                                        }}
                                        title="Forget this person"
                                        onClick={async () => {
                                          await forgetPersonMemory(p.user_id);
                                          void refreshPeopleMemory();
                                        }}
                                      >
                                        ✕
                                      </button>
                                    </div>

                                    {p.bio && (
                                      <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>
                                        {p.bio.slice(0, 90)}{p.bio.length > 90 ? '...' : ''}
                                      </div>
                                    )}

                                    {/* Notes / Learned Facts */}
                                    {p.notes.length > 0 && (
                                      <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                                        {p.notes.map((note, nIdx) => (
                                          <div
                                            key={nIdx}
                                            style={{
                                              fontSize: '0.68rem',
                                              color: '#a78bfa',
                                              display: 'flex',
                                              alignItems: 'center',
                                              justifyContent: 'space-between',
                                              background: 'rgba(167, 139, 250, 0.08)',
                                              padding: '2px 6px',
                                              borderRadius: '4px',
                                            }}
                                          >
                                            <span>💡 {note}</span>
                                            <button
                                              style={{
                                                background: 'transparent',
                                                border: 'none',
                                                color: '#94a3b8',
                                                cursor: 'pointer',
                                                fontSize: '0.6rem',
                                                padding: '0 2px',
                                              }}
                                              onClick={async () => {
                                                await removePersonNote(p.user_id, nIdx);
                                                void refreshPeopleMemory();
                                              }}
                                            >
                                              ✕
                                            </button>
                                          </div>
                                        ))}
                                      </div>
                                    )}

                                    {/* Add Note Input */}
                                    <form
                                      onSubmit={async (e) => {
                                        e.preventDefault();
                                        if (!noteInput.trim()) return;
                                        await addPersonNote(p.user_id, noteInput.trim());
                                        setNewNoteInputs((prev) => ({ ...prev, [p.user_id]: '' }));
                                        void refreshPeopleMemory();
                                      }}
                                      style={{ display: 'flex', gap: '0.3rem', marginTop: '2px' }}
                                    >
                                      <input
                                        type="text"
                                        placeholder="+ Add note / fact..."
                                        value={noteInput}
                                        onChange={(e) =>
                                          setNewNoteInputs((prev) => ({
                                            ...prev,
                                            [p.user_id]: e.target.value,
                                          }))
                                        }
                                        style={{
                                          ...agentInputStyle,
                                          padding: '0 0.4rem',
                                          fontSize: '0.68rem',
                                          flex: 1,
                                        }}
                                      />
                                      <button
                                        type="submit"
                                        className="ch-btn-action"
                                        style={{ padding: '0.2rem 0.5rem', fontSize: '0.68rem' }}
                                      >
                                        Add
                                      </button>
                                    </form>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                          <span style={{ fontSize: '0.65rem', color: '#64748b' }}>
                            💡 Spoken / Chat commands: <code>/agent whois @name</code>, <code>/agent remember @name fact</code>, <code>/agent people</code>
                          </span>
                        </div>

                        {/* Compact Media Telemetry & Protocol Summary Card */}
                        <div
                          style={{
                            background: 'rgba(0,0,0,0.2)',
                            borderRadius: '8px',
                            padding: '0.6rem 0.8rem',
                            border: '1px solid rgba(255,255,255,0.05)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            marginTop: '0.4rem',
                          }}
                        >
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                            <span style={{ fontSize: '0.72rem', fontWeight: 600, color: '#38bdf8' }}>
                              📡 Protocols & Media Telemetry
                            </span>
                            <span style={{ fontSize: '0.65rem', color: '#94a3b8' }}>
                              UDP/Opus • PubNub WSS • Agora RTN
                            </span>
                          </div>
                          <button
                            className="ch-btn-action"
                            style={{ padding: '0.25rem 0.6rem', fontSize: '0.68rem' }}
                            onClick={() => setShowNetworkModal(true)}
                          >
                            Inspect
                          </button>
                        </div>
                      </div>

                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          gap: '0.5rem',
                          marginTop: '0.5rem',
                          borderTop: '1px solid rgba(255,255,255,0.05)',
                          paddingTop: '1rem',
                        }}
                      >
                        <button
                          type="button"
                          className="ch-btn-action"
                          style={{
                            padding: '0.6rem 1.2rem',
                            fontSize: '0.85rem',
                            background: '#e11d48',
                            color: 'white',
                            border: 'none',
                            borderRadius: '20px',
                            cursor: (!isAgentThinking && !isAgentSpeaking) ? 'not-allowed' : 'pointer',
                            opacity: (!isAgentThinking && !isAgentSpeaking) ? 0.5 : 1,
                          }}
                          onClick={handleInterrupt}
                          disabled={!isAgentThinking && !isAgentSpeaking}
                          title="Immediately stop speaking and abort in-flight LLM generation"
                        >
                          ✋ Interrupt
                        </button>
                        <button
                          type="button"
                          className="ch-btn-action"
                          style={{
                            padding: '0.6rem 1.2rem',
                            fontSize: '0.85rem',
                            background: '#2e333d',
                            border: 'none',
                            borderRadius: '20px',
                            cursor: (isAgentThinking || isAgentSpeaking) ? 'not-allowed' : 'pointer',
                            opacity: (isAgentThinking || isAgentSpeaking) ? 0.6 : 1,
                          }}
                          onClick={() =>
                            enqueueUtterance(
                              'Say something to the room — pick up the current conversation, or open one if it has gone quiet.',
                              'voice',
                              true,
                            )
                          }
                          disabled={isAgentThinking || isAgentSpeaking}
                        >
                          {isAgentThinking ? '⏳ Generating…' : isAgentSpeaking ? '🔊 Speaking…' : '🗣️ Speak Now'}
                        </button>
                        <button
                          type="button"
                          className="ch-btn-action"
                          style={{
                            padding: '0.6rem 1.2rem',
                            fontSize: '0.85rem',
                            background: '#2e333d',
                            border: 'none',
                            borderRadius: '20px',
                            cursor: 'pointer',
                          }}
                          onClick={() => void handleClearContext(false)}
                          title="Wipe conversation history so agent forgets recent context"
                        >
                          🧹 Forget
                        </button>
                      </div>
                      {!isCurrentUserSpeaker && (
                        <p
                          style={{
                            fontSize: '0.75rem',
                            color: '#94a3b8',
                            margin: '0',
                            textAlign: 'right',
                          }}
                        >
                          ℹ️ You are in the audience. Agent will respond in text chat only.
                        </p>
                      )}
                    </div>
                  )}
              </div>
            </div>
          )}
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
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}></div>
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
                    onClick={() => void acceptSpeakerInvite()}
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

        {/* Room Settings Modal */}
        {showRoomSettingsModal && (
          <div className="ch-modal-overlay" onClick={() => setShowRoomSettingsModal(false)}>
            <div className="ch-modal-card ch-settings-modal" onClick={(e) => e.stopPropagation()}>
              <div className="ch-modal-header">
                <h3 className="ch-modal-title">Room Settings</h3>
                <button className="ch-modal-close" onClick={() => setShowRoomSettingsModal(false)}>
                  ✕
                </button>
              </div>
              <div className="ch-modal-body">
                <form onSubmit={handleSaveRoomSettings} className="ch-start-room-form">
                  <div className="ch-form-group">
                    <label>Room Topic</label>
                    <input
                      type="text"
                      placeholder="What is this room about?"
                      value={settingTopic}
                      onChange={(e) => setSettingTopic(e.target.value)}
                      className="ch-input"
                    />
                  </div>
                  <div className="ch-form-group">
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <input
                        type="checkbox"
                        checked={settingHandraiseEnabled}
                        onChange={(e) => setSettingHandraiseEnabled(e.target.checked)}
                      />{' '}
                      Enable Hand Raising
                    </label>
                  </div>
                  {settingHandraiseEnabled && (
                    <div className="ch-form-group">
                      <label>Who can raise hands?</label>
                      <select
                        value={settingHandraisePermission}
                        onChange={(e) =>
                          setSettingHandraisePermission(e.target.value as HandraisePermission)
                        }
                        className="ch-input"
                        style={{ width: '100%' }}
                      >
                        <option value="everyone">Everyone</option>
                        <option value="followed_by_speakers">Followed by Speakers</option>
                      </select>
                    </div>
                  )}
                  <div className="ch-form-group">
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <input
                        type="checkbox"
                        checked={settingChatEnabled}
                        onChange={(e) => setSettingChatEnabled(e.target.checked)}
                      />{' '}
                      Enable Room Chat
                    </label>
                  </div>
                  <button type="submit" className="ch-btn-submit" disabled={savingSettings}>
                    {savingSettings ? 'Saving...' : 'Save Settings'}
                  </button>
                </form>
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

      <MediaInsightsModal
        isOpen={showNetworkModal}
        onClose={() => setShowNetworkModal(false)}
        getInsights={getNetworkInsights}
        channelName={activeChannel}
      />

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

