/**
 * REST client for the social layer. Mirrors `backend/modules/social/models.py`;
 * the backend stays the source of truth for these shapes.
 */
import { apiDelete, apiGet, apiPost } from '../../api';

export type FriendStatus = 'pending_out' | 'pending_in' | 'accepted' | 'blocked';

/** One machine belonging to a person. */
export interface DeviceInfo {
  node_id: string;
  person_id: string;
  node_public_key: string;
  label: string;
  online: boolean;
  last_seen: number | null;
  last_address: string | null;
  capabilities: string[];
}

export interface Friend {
  person_id: string;
  display_name: string;
  friend_code: string;
  person_public_key: string;
  status: FriendStatus;
  note: string | null;
  added_at: number;
  presence: 'online' | 'offline';
  devices: DeviceInfo[];
  /** Your own linked machines appear in the roster, flagged with this. */
  is_self: boolean;
}

export interface SelfProfile {
  person_id: string;
  friend_code: string;
  display_name: string;
  person_public_key: string;
  holds_person_key: boolean;
  devices: DeviceInfo[];
}

export interface RosterSnapshot {
  self_profile: SelfProfile;
  friends: Friend[];
}

export interface AddFriendResult {
  ok: boolean;
  friend?: Friend | null;
  error?: string | null;
}

export interface LinkDeviceResult {
  ok: boolean;
  device?: DeviceInfo | null;
  error?: string | null;
}

export function getRoster(): Promise<RosterSnapshot> {
  return apiGet<RosterSnapshot>('/social/roster');
}

export function getSelfProfile(): Promise<SelfProfile> {
  return apiGet<SelfProfile>('/social/me');
}

export function updateSelfProfile(displayName: string): Promise<SelfProfile> {
  return apiPost<SelfProfile>('/social/me', { display_name: displayName });
}

export function addFriend(code: string, address?: string, note?: string): Promise<AddFriendResult> {
  return apiPost<AddFriendResult>('/social/friends', { code, address, note });
}

export function respondToRequest(personId: string, accept: boolean): Promise<RosterSnapshot> {
  return apiPost<RosterSnapshot>('/social/friends/respond', {
    person_id: personId,
    accept,
  });
}

export function removeFriend(personId: string): Promise<RosterSnapshot> {
  return apiDelete<RosterSnapshot>(`/social/friends/${personId}`);
}

export function blockFriend(personId: string): Promise<RosterSnapshot> {
  return apiPost<RosterSnapshot>(`/social/friends/${personId}/block`, {});
}

/** Claim another of your machines, using the peer-fabric invite it minted. */
export function linkDevice(invite: string, label?: string): Promise<LinkDeviceResult> {
  return apiPost<LinkDeviceResult>('/social/devices/link', { invite, label });
}
