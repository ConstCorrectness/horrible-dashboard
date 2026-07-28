/**
 * REST client for the HorribleAssault map pipeline. Mirrors
 * `backend/modules/hassault/models.py`; the backend stays the source of truth.
 */
import { apiGet } from '../../api';
import { apiUrl } from '../../origin';

export interface MapSummary {
  name: string;
  source: string;
  size: number;
}

export interface InstallStatus {
  found: boolean;
  path: string | null;
  configured: boolean;
  map_count: number;
  message: string | null;
}

export interface MapEntity {
  type: number;
  name: string;
  x: number;
  y: number;
  z: number;
  yaw: number | null;
  attrs: number[];
}

export interface MapInfo {
  name: string;
  title: string;
  magic: string;
  version: number;
  sfactor: number;
  ssize: number;
  cubic_size: number;
  waterlevel: number;
  watercolor: number[];
  maprevision: number;
  ambient: number;
  flags: number;
  timestamp: number;
  entity_count: number;
  entities: MapEntity[];
  spawns: Record<string, number>;
  truncated: boolean;
  legacy_unscaled_attrs: boolean;
  /** Plane names in the order they appear in the /cubes payload. */
  plane_order: string[];
}

export interface MatchSummary {
  id: string;
  map: string;
  players: number;
  maxPlayers: number;
  createdAt: number;
}

/**
 * A friend who could be invited right now.
 *
 * Assembled by the hassault backend from the social roster, so this pane never
 * imports across a module boundary to build it.
 */
export interface Invitee {
  name: string;
  person_id: string;
  friend_code: string;
  /** False means their build predates matches, so an invite would land nowhere. */
  can_play: boolean;
  devices_online: number;
}

export interface MatchInvite {
  room: string;
  map: string;
  /** The inviting node id — authenticated by the fabric, unlike `hostName`. */
  host: string;
  hostName: string;
  ts: number;
}

export function listMatches(): Promise<MatchSummary[]> {
  return apiGet<MatchSummary[]>('/hassault/matches');
}

export function listInvitees(): Promise<Invitee[]> {
  return apiGet<Invitee[]>('/hassault/invitees');
}

export function listInvites(): Promise<MatchInvite[]> {
  return apiGet<MatchInvite[]>('/hassault/invites');
}

export function getInstallStatus(): Promise<InstallStatus> {
  return apiGet<InstallStatus>('/hassault/status');
}

export function listMaps(): Promise<MapSummary[]> {
  return apiGet<MapSummary[]>('/hassault/maps');
}

export function getMapInfo(name: string): Promise<MapInfo> {
  return apiGet<MapInfo>(`/hassault/maps/${encodeURIComponent(name)}`);
}

/**
 * The cube grid: nine byte planes concatenated, `ssize * ssize` bytes each.
 *
 * Deliberately not JSON — a 256×256 map is 65 536 cubes across nine fields, which
 * is ~590 000 numbers. Fetched as an ArrayBuffer so the planes become typed array
 * views with no parsing and no copy.
 */
export async function getMapCubes(name: string): Promise<ArrayBuffer> {
  // Raw fetch rather than `apiGet`, which assumes JSON. `apiUrl` resolves the
  // backend origin but does *not* add the `/api` prefix — `apiGet` does that.
  const res = await fetch(apiUrl(`/api/hassault/maps/${encodeURIComponent(name)}/cubes`), {
    credentials: 'same-origin',
  });
  if (!res.ok) throw new Error(`could not load cubes for ${name}: ${res.status}`);
  return res.arrayBuffer();
}
