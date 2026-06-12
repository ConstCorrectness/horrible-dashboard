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
