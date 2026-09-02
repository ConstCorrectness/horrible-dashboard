import { apiDelete, apiGet, apiPost } from '../../api';

export interface ClubhouseStatus {
  connected: boolean;
  user_id: number | null;
  username: string | null;
  name: string | null;
  photo_url: string | null;
}

export function getClubhouseStatus(): Promise<ClubhouseStatus> {
  return apiGet<ClubhouseStatus>('/clubhouse/status');
}

export function startClubhouseAuth(phoneNumber: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>('/clubhouse/auth/start', { phone_number: phoneNumber });
}

export function completeClubhouseAuth(
  phoneNumber: string,
  verificationCode: string,
): Promise<ClubhouseStatus> {
  return apiPost<ClubhouseStatus>('/clubhouse/auth/complete', {
    phone_number: phoneNumber,
    verification_code: verificationCode,
  });
}

export function connectClubhouseWithToken(
  authToken: string,
  userId: number,
  deviceId?: string,
): Promise<ClubhouseStatus> {
  return apiPost<ClubhouseStatus>('/clubhouse/auth/token', {
    auth_token: authToken,
    user_id: userId,
    device_id: deviceId ?? null,
  });
}

export function disconnectClubhouse(): Promise<ClubhouseStatus> {
  return apiDelete<ClubhouseStatus>('/clubhouse/auth');
}

export interface ChannelUser {
  user_id: number | null;
  name: string | null;
  username: string | null;
  photo_url: string | null;
  is_speaker: boolean | null;
  is_moderator: boolean | null;
}

export interface Channel {
  channel: string | null;
  topic: string | null;
  num_speakers: number | null;
  num_all: number | null;
  club: { name: string | null } | null;
  users: ChannelUser[];
}

export interface FollowUser {
  user_id: number | null;
  name: string | null;
  username: string | null;
  photo_url: string | null;
}

export function getClubhouseChannels(): Promise<{ channels: Channel[] }> {
  return apiGet<{ channels: Channel[] }>('/clubhouse/channels');
}

export function getClubhouseFollowing(): Promise<{ users: FollowUser[] }> {
  return apiGet<{ users: FollowUser[] }>('/clubhouse/following');
}

export interface JoinChannelResult {
  success: boolean;
  channel_id: number | null;
  channel: string | null;
  token: string | null;
  rtm_token: string | null;
  pubnub_token: string | null;
  pubnub_origin: string | null;
  pubnub_heartbeat_value: number | null;
  pubnub_heartbeat_interval: number | null;
  pubnub_enable: boolean | null;
  agora_native_mute: boolean | null;
  user_id: number | null;
}

export function joinClubhouseChannel(channel: string): Promise<JoinChannelResult> {
  return apiPost<JoinChannelResult>(`/clubhouse/channels/${channel}/join`, {});
}

export function leaveClubhouseChannel(channel: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/leave`, {});
}

export function pingClubhouseChannel(channel: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/ping`, {});
}

export function muteClubhouseChannel(
  channel: string,
  isMuted: boolean,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/mute`, {
    is_muted: isMuted,
  });
}

export function setClubhouseHand(
  channel: string,
  raiseHands: boolean,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/hand`, {
    raise_hands: raiseHands,
  });
}

/** Come up on stage. Takes no user id: upstream's `become_speaker` accepts the
 *  stage itself, not one particular moderator's invitation. */
export function acceptClubhouseSpeaker(channel: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/accept_speaker`, {});
}

export function inviteClubhouseSpeaker(
  channel: string,
  userId: number,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/invite_speaker`, {
    user_id: userId,
  });
}

export function updateClubhouseTopic(
  channel: string,
  topic: string,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/topic`, { topic });
}

/** Who may raise a hand. Named, never numbered: Clubhouse's wire ints changed
 *  meaning between API generations (1 used to be "everyone", it now means
 *  "locked"), so the mapping to a number lives only in the backend. */
export type HandraisePermission = 'open_mic' | 'locked' | 'everyone' | 'followed_by_speakers';

export function updateClubhouseHandraiseSettings(
  channel: string,
  isEnabled: boolean,
  permission: HandraisePermission,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/handraise_settings`, {
    is_enabled: isEnabled,
    handraise_permission: permission,
  });
}

export interface HandraiseQueueUser {
  user_id: number | null;
  name: string | null;
  username: string | null;
  photo_url: string | null;
}

/** Who currently has a hand raised in the room. */
export function getClubhouseHandraiseQueue(
  channel: string,
): Promise<{ handraises: HandraiseQueueUser[] }> {
  return apiGet<{ handraises: HandraiseQueueUser[] }>(
    `/clubhouse/channels/${channel}/handraise_queue`,
  );
}

export function updateClubhouseChatSettings(
  channel: string,
  enableChat: boolean,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/chat_settings`, {
    enable_chat: enableChat,
  });
}

export function getClubhouseChannelDetails(channel: string): Promise<Channel> {
  return apiGet<Channel>(`/clubhouse/channels/${channel}`);
}

export interface ApiChatComment {
  message?: string;
  text?: string;
  user_profile?: {
    name?: string;
    photo_url?: string;
  };
  from_name?: string;
  from_photo_url?: string;
  time_created?: string;
  [key: string]: unknown;
}

export function getClubhouseChannelChat(channel: string): Promise<{ comments: ApiChatComment[] }> {
  return apiGet<{ comments: ApiChatComment[] }>(`/clubhouse/channels/${channel}/chat`);
}

export interface ClubhouseUserProfile {
  user_id: number;
  name: string | null;
  username: string | null;
  photo_url: string | null;
  bio: string | null;
  num_followers: number;
  num_following: number;
  twitter: string | null;
  instagram: string | null;
  follows_me: boolean;
  notification_type?: number;
  invited_by_user_profile: {
    user_id: number;
    name: string;
    photo_url: string | null;
    username: string;
  } | null;
}

export function getClubhouseUserProfile(userId: number): Promise<ClubhouseUserProfile> {
  return apiGet<ClubhouseUserProfile>(`/clubhouse/users/${userId}`);
}

export interface SearchUserResult {
  user_id: number;
  name: string | null;
  username: string | null;
  photo_url: string | null;
  bio: string | null;
  is_following?: boolean;
}

export function createClubhouseChannel(
  topic: string,
  isPrivate: boolean = false,
  isSocialMode: boolean = false,
): Promise<JoinChannelResult> {
  return apiPost<JoinChannelResult>('/clubhouse/channels', {
    topic,
    is_private: isPrivate,
    is_social_mode: isSocialMode,
  });
}

export function followClubhouseUser(userId: number): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/users/${userId}/follow`, {});
}

export function unfollowClubhouseUser(userId: number): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/users/${userId}/unfollow`, {});
}

export function inviteToClubhouseChannel(
  channel: string,
  userId: number,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/invite`, {
    user_id: userId,
  });
}

export function searchClubhouseUsers(query: string): Promise<{ users: SearchUserResult[] }> {
  return apiGet<{ users: SearchUserResult[] }>(
    `/clubhouse/users/search?query=${encodeURIComponent(query)}`,
  );
}

export function sendChannelMessage(
  channel: string,
  message: string,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/send_channel_message`, {
    channel,
    message,
  });
}

export function uninviteClubhouseSpeaker(
  channel: string,
  userId: number,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/uninvite_speaker`, {
    user_id: userId,
  });
}

export function makeClubhouseModerator(
  channel: string,
  userId: number,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/make_moderator`, {
    user_id: userId,
  });
}

export function blockFromClubhouseChannel(
  channel: string,
  userId: number,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/block`, { user_id: userId });
}

export function endClubhouseChannel(channel: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/end`, {});
}

export function rejectClubhouseSpeakerInvite(
  channel: string,
  userId: number,
): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>(`/clubhouse/channels/${channel}/reject_speaker`, {
    user_id: userId,
  });
}

export function getClubhouseFollowers(
  userId: number,
  pageSize: number = 50,
  page: number = 1,
): Promise<{ users: FollowUser[] }> {
  return apiGet<{ users: FollowUser[] }>(
    `/clubhouse/users/${userId}/followers?page_size=${pageSize}&page=${page}`,
  );
}

export interface ClubhouseNotification {
  notification_id: number | null;
  message: string | null;
  time_created: string | null;
  type: number | null;
  user_profile: ChannelUser | null;
  channel: string | null;
}

/** Recent activity. Paginates by opaque cursor — the upstream replacement for
 *  `get_notifications` has no page numbers, so there is nothing to pass but the
 *  `next_cursor` handed back by the previous call. */
export function getClubhouseNotifications(
  nextCursor?: string,
): Promise<{ notifications: ClubhouseNotification[]; next_cursor: string | null }> {
  const query = nextCursor ? `?next_cursor=${encodeURIComponent(nextCursor)}` : '';
  return apiGet<{ notifications: ClubhouseNotification[]; next_cursor: string | null }>(
    `/clubhouse/notifications${query}`,
  );
}

export function updateClubhouseBio(bio: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>('/clubhouse/me/bio', { bio });
}

export function updateClubhouseName(name: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>('/clubhouse/me/name', { name });
}

export function updateClubhouseUsername(username: string): Promise<{ success: boolean }> {
  return apiPost<{ success: boolean }>('/clubhouse/me/username', { username });
}

export interface TtsVoiceOption {
  name: string;
  gender?: string;
  locale?: string;
  label?: string;
}

export function getAgentTtsVoices(): Promise<TtsVoiceOption[]> {
  return apiGet<TtsVoiceOption[]>('/agent/tts/voices');
}

// --- People Knowledge & Profile Memory Types & API ---

export interface PersonMemory {
  user_id: number;
  name: string;
  username: string;
  bio: string | null;
  photo_url: string | null;
  twitter: string | null;
  instagram: string | null;
  notes: string[];
  tags: string[];
  rooms_seen: string[];
  first_seen_ts: number;
  last_seen_ts: number;
  interaction_count: number;
  summary: string | null;
}

export function listPeopleMemory(query: string = ''): Promise<PersonMemory[]> {
  return apiGet<PersonMemory[]>(
    `/clubhouse/people-memory${query ? `?q=${encodeURIComponent(query)}` : ''}`,
  );
}

export function getPersonMemory(userId: number): Promise<PersonMemory> {
  return apiGet<PersonMemory>(`/clubhouse/people-memory/${userId}`);
}

export function addPersonNote(userId: number, note: string): Promise<PersonMemory> {
  return apiPost<PersonMemory>(`/clubhouse/people-memory/${userId}/notes`, { note });
}

export function removePersonNote(userId: number, noteIdx: number): Promise<PersonMemory> {
  return apiDelete<PersonMemory>(`/clubhouse/people-memory/${userId}/notes/${noteIdx}`);
}

export function addPersonTag(userId: number, tag: string): Promise<PersonMemory> {
  return apiPost<PersonMemory>(`/clubhouse/people-memory/${userId}/tags`, { tag });
}

export function removePersonTag(userId: number, tag: string): Promise<PersonMemory> {
  return apiDelete<PersonMemory>(
    `/clubhouse/people-memory/${userId}/tags/${encodeURIComponent(tag)}`,
  );
}

export function forgetPersonMemory(
  userId: number,
): Promise<{ user_id: number; forgotten: boolean }> {
  return apiDelete<{ user_id: number; forgotten: boolean }>(`/clubhouse/people-memory/${userId}`);
}
