/**
 * REST client for the HorribleAssault map pipeline. Mirrors
 * `backend/modules/hassault/models.py`; the backend stays the source of truth.
 */
import { apiGet } from '../../api';
import { apiUrl } from '../../origin';

export interface MapSummary {
  name: string;
  /** `bundled` for a map the app ships, otherwise the install directory it came
   * from (official / servermaps / …). */
  source: string;
  size: number;
}

export interface InstallStatus {
  /** Whether an *AssaultCube install* was found — an addition, not a gate. The
   * bundled maps play without one, so the panel keys off `map_count`. */
  found: boolean;
  path: string | null;
  configured: boolean;
  /** Every playable map, bundled and installed together. */
  map_count: number;
  bundled_count: number;
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
  /** How many of `players` are bots. */
  bots: number;
  maxPlayers: number;
  createdAt: number;
}

/**
 * One weapon's numbers, as the backend defines them.
 *
 * Fetched rather than hardcoded here. The client needs the fire interval so it
 * does not send input the server will only discard, and a second copy of that
 * constant in TypeScript is a drift trap for no gain — the same reasoning as
 * `plane_order` on `MapInfo`.
 */
export interface WeaponSpec {
  id: string;
  name: string;
  damage: number;
  headMultiplier: number;
  rpm: number;
  /** Seconds between shots, derived from `rpm` server-side so both sides round it identically. */
  interval: number;
  mag: number;
  /** `-1` is unlimited. */
  reserve: number;
  reloadTime: number;
  spread: number;
  pellets: number;
  range: number;
  /** Whether holding the button keeps firing. */
  auto: boolean;
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

export function listWeapons(): Promise<WeaponSpec[]> {
  return apiGet<WeaponSpec[]>('/hassault/weapons');
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

/** Reports download progress in bytes. `total` is null when the response didn't
 * say how big it is. */
export type ProgressFn = (loaded: number, total: number | null) => void;

/**
 * The cube grid: nine byte planes concatenated, `ssize * ssize` bytes each.
 *
 * Deliberately not JSON — a 256×256 map is 65 536 cubes across nine fields, which
 * is ~590 000 numbers. Fetched as an ArrayBuffer so the planes become typed array
 * views with no parsing and no copy.
 *
 * Read through a stream reader when `onProgress` is given, so the loading screen
 * shows the real byte count rather than a spinner. `Content-Length` is present
 * (the route builds a materialised `Response`, and nothing gzips it) and is
 * CORS-safelisted, so it survives the Vite dev proxy. Two things this must not
 * assume: that the header exists at all, and that the download takes any time —
 * the route sets `Cache-Control: max-age=3600`, so a warm reload finishes in one
 * frame.
 */
export async function getMapCubes(name: string, onProgress?: ProgressFn): Promise<ArrayBuffer> {
  // Raw fetch rather than `apiGet`, which assumes JSON. `apiUrl` resolves the
  // backend origin but does *not* add the `/api` prefix — `apiGet` does that.
  const res = await fetch(apiUrl(`/api/hassault/maps/${encodeURIComponent(name)}/cubes`), {
    credentials: 'same-origin',
  });
  if (!res.ok) throw new Error(`could not load cubes for ${name}: ${res.status}`);

  const header = res.headers.get('content-length');
  const total = header ? Number(header) : null;
  // No callback, or a body that can't be streamed: take the simple path.
  if (!onProgress || !res.body) {
    const buffer = await res.arrayBuffer();
    onProgress?.(buffer.byteLength, total ?? buffer.byteLength);
    return buffer;
  }

  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress(loaded, total);
  }

  // Concatenated rather than returned as chunks: `World` adopts one contiguous
  // buffer as nine plane views, which is the whole reason this isn't JSON.
  const out = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  // A stream that ended short of the advertised length is a truncated map, and
  // `World` would read past the end of a plane rather than fail.
  if (total !== null && loaded !== total) {
    throw new Error(`map ${name} download was truncated (${loaded} of ${total} bytes)`);
  }
  return out.buffer;
}

// ---- identity ----------------------------------------------------------------

/** Who this node plays as. `enlisted` is having a callsign — the backend refuses a
 * join without one, so the pane must too. Mirrors `models.SessionInfo`. */
export interface SessionInfo {
  signed_in: boolean;
  account_id: string | null;
  display_name: string | null;
  callsign: string | null;
  enlisted: boolean;
}

/** `refresh` re-reads the account from the game server, which is how a callsign
 * claimed on another machine shows up here. Slower, so it's opt-in. */
export function getSession(refresh = false): Promise<SessionInfo> {
  return apiGet<SessionInfo>(`/hassault/session${refresh ? '?refresh=true' : ''}`);
}
