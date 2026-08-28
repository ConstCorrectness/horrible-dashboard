/**
 * REST client for the HorribleAssault map pipeline. Mirrors
 * `backend/modules/hassault/models.py`; the backend stays the source of truth.
 */
import { apiGet, apiPost } from '../../api';
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
  /**
   * Cubes per second the shot shoves the **shooter**, opposite their aim.
   *
   * AssaultCube's recoil push, and the whole of shoot-jumping: aim at the floor
   * and a shotgun blast reaches ledges a jump cannot. Served rather than
   * hardcoded here because the client has to predict the identical impulse the
   * server is about to apply — two copies of this number is a mispredict on every
   * shot. See `kickVector` in `combat.ts`.
   */
  kickback: number;
  /**
   * Magnifications the scope steps through, in order. Empty means no scope.
   *
   * Served for the same reason `interval` is: these divide both the FOV *and*
   * the mouse sensitivity, so a hardcoded copy here would be an aim that is
   * wrong only while scoped — the hardest kind of wrong to notice.
   */
  zoomLevels: number[];
  /**
   * Cone half-angle while not scoped. Equal to `spread` for every weapon
   * without a scope, so this can be read unconditionally.
   */
  hipfireSpread: number;
}

/**
 * A friend who could be invited right now.
 *
 * Assembled by the hassault backend from the social roster, so this pane never
 * imports across a module boundary to build it.
 */
export interface Invitee {
  name: string;
  /**
   * Their `@username`, when the roster has resolved one.
   *
   * Empty is a real answer, not a bug: a friend added by friend code before
   * either of you signed in to the game server has no account bound to their
   * person key yet. Served alongside `name` for exactly that reason — prefer this
   * and fall back, rather than rendering an empty row.
   */
  username: string;
  person_id: string;
  friend_code: string;
  /** False means their build predates matches, so an invite would land nowhere. */
  can_play: boolean;
  devices_online: number;
  /** A room on this node they are standing in, and its map — presence beyond
   * online/offline. Empty when they are online but not playing. */
  room: string;
  room_map: string;
}

export interface MatchInvite {
  room: string;
  map: string;
  /** The inviting node id — authenticated by the fabric, unlike `hostName`. */
  host: string;
  /**
   * Who invited you, as `@username` — a **person**, not a machine.
   *
   * This used to be the sender's node name, so an invite from a friend read
   * "horribleComputer invited you": the invite is assembled on the fabric side,
   * where only the device label is in scope, and the account username the rest of
   * the app keys on was never joined in. The backend resolves it against the
   * roster first and the sender's stamp second (`fabric._invite_display_name`).
   */
  hostName: string;
  /** Which of their machines it came from — a secondary detail, since an invite
   * fans out to every device a person has online. */
  hostDevice?: string;
  /** The inviting person, when the roster knows them. Used for per-person mutes. */
  personId?: string;
  ts: number;
  /** When it stops being joinable. A room does not outlive the process hosting
   * it, so an invite has a shelf life rather than sitting there forever. */
  expiresAt?: number;
}

/**
 * One row of the server browser.
 *
 * `host` is the empty string for a match on this node and a node id for a
 * friend's — which is exactly what `join` wants, so a row is joinable without the
 * pane having to know which kind it is looking at.
 */
export interface BrowseMatch extends MatchSummary {
  host: string;
  hostName: string;
}

/** Someone reachable: a friend on the roster, wherever they happen to be. */
export interface BrowsePlayer {
  name: string;
  person_id: string;
  friend_code: string;
  /** A room id when one of their devices is playing in a match hosted here. */
  room: string;
  can_play: boolean;
  devices_online: number;
}

/**
 * A refresh of the server browser. `peers_asked`/`peers_answered` differ when a
 * friend's node was asked and didn't reply in time — the list is then partial,
 * and saying so beats quietly showing less than there is.
 */
export interface ServerBrowse {
  matches: BrowseMatch[];
  players: BrowsePlayer[];
  peers_asked: number;
  peers_answered: number;
}

/**
 * One grenade's numbers, served rather than duplicated here.
 *
 * The `interval` / `zoomLevels` / `plane_order` precedent: the HUD reads the
 * carry count and the name, and the renderer draws a cloud at `radius` — a
 * hardcoded copy of that would be a smoke drawn a different size from the one
 * actually blocking sight on the server.
 *
 * The list arrives in **slot order**, and the wire carries a slot index rather
 * than an id, so the order is load-bearing: see `NADE_ACTIONS` in `controls.ts`.
 */
export interface TacticalSpec {
  id: string;
  name: string;
  type: 'smoke' | 'flash' | 'he' | 'fire';
  fuseTime: number;
  /** Detonates on contact instead of on the fuse — the incendiary. */
  impact: boolean;
  radius: number;
  duration: number;
  maxDamage: number;
  damagePerSecond: number;
  bounceDamping: number;
  /** How many you spawn with. */
  carried: number;
}

export interface LaunchNativeOptions {
  /**
   * What was pressed. `train` is not a match at all — the client stays off the
   * socket, because the server's roomless join is join-*or*-create and would seat
   * a learner in whatever firefight is already on that map. `host` opens one and
   * fields `bots`; `join` enters one that exists.
   */
  /** `ranked` opens a match the game server adjudicates; the rest are local. */
  mode?: 'train' | 'host' | 'join' | 'ranked';
  /** A specific room; empty means "any match on this map, or open one". */
  room_id?: string;
  map_name: string;
  /** A friend's node id, when the room is on their machine. */
  host?: string;
  /** A wire label only — the node plays you as your account's username. */
  username?: string;
  fullscreen?: boolean;
  raw_input?: boolean;
  max_fps?: number;
  /** Bots to field. `host` only — `add_bot` is host-only on the channel. */
  bots?: number;
  bot_skill?: string;
}

export interface LaunchNativeResult {
  /**
   * How far the launch has got: `starting`, `building`, `launched`, `failed`,
   * or `idle` (only from the status route — "nothing has been launched", which
   * is not the same fact as a launch that failed).
   *
   * A launch is a **job on the node**, not a request: an edited client is
   * compiled before it is started, and a cold build of that crate is minutes.
   * The POST answers `building` rather than not answering, and
   * `nativeLaunchStatus` hands the same job back to a pane that has been
   * unmounted and remounted in the meantime — which is what a tab switch does.
   */
  phase?: 'idle' | 'starting' | 'building' | 'launched' | 'failed';
  launched: boolean;
  pid?: number;
  connect_args: string[];
  message?: string;
  /** The route compiled the client before starting it, and how long that took.
   * Served rather than read out of `message`: the pane is unresponsive for the
   * duration and a build is the one launch measured in minutes. */
  rebuilt?: boolean;
  build_seconds?: number;
  /** It started a build older than its own source — only reachable with
   * `hassault.autoBuildNative` off. Worth saying loudly: this is the failure
   * that reads as "my change did not work". */
  stale?: boolean;
}

export function listMatches(): Promise<MatchSummary[]> {
  return apiGet<MatchSummary[]>('/hassault/matches');
}

/** Matches and players, here and on friends' nodes. Slower than `listMatches`:
 * it waits on the peer fan-out, so it is a refresh button, not a poll. */
export function browseServers(): Promise<ServerBrowse> {
  return apiGet<ServerBrowse>('/hassault/browse');
}

export function listWeapons(): Promise<WeaponSpec[]> {
  return apiGet<WeaponSpec[]>('/hassault/weapons');
}

export function listTacticals(): Promise<TacticalSpec[]> {
  return apiGet<TacticalSpec[]>('/hassault/tacticals');
}

/**
 * Maps a **rated** match can be played on.
 *
 * The game server's own answer, proxied by this node — not a local filter on
 * `source === 'bundled'`. The two agree today, and the server's is the one that
 * decides: a map added on either side then needs no matching change on the other.
 * An empty list means the server could not be reached, which is a reason to grey
 * Ranked out rather than to let a join fail at the socket.
 */
export async function getRankedMaps(): Promise<string[]> {
  const res = await apiGet<{ maps: string[] }>('/hassault/ranked/maps');
  return res.maps ?? [];
}

export function launchNativeFps(opts: LaunchNativeOptions): Promise<LaunchNativeResult> {
  return apiPost<LaunchNativeResult>('/hassault/launch_native', opts);
}

/**
 * Where this node's launch has got to, if one is running.
 *
 * Read on mount as well as while polling. That is the whole of the tab-switch
 * fix: a pane is unmounted when its tab loses focus, taking the promise it was
 * awaiting with it, and without this the remounted pane shows an idle button
 * over a `cargo build` still running behind it.
 */
export function nativeLaunchStatus(): Promise<LaunchNativeResult> {
  return apiGet<LaunchNativeResult>('/hassault/launch_native/status');
}

// ---- the native client's own installation ------------------------------------

/**
 * Where the native client would come from if it were launched right now.
 *
 * Mirrors `models.NativeClientStatus`. `source` is served rather than worked out
 * here on purpose: the three tiers are resolved in Python, and a second copy of
 * that ordering in TypeScript is a second chance to get it backwards — which
 * would show an install button over a local build that is about to win anyway.
 */
export interface NativeClientStatus {
  /** `setting` | `build` | `download` | `none`. */
  source: 'setting' | 'build' | 'download' | 'none';
  binary: string | null;
  version: string;
  installed: boolean;
  /** Whether GitHub published a digest for the installed asset. Meaningless when
   * `installed` is false — an unverified install is a fact worth showing, not an
   * error. */
  verified: boolean;
  installed_size_bytes?: number | null;
  /** A checkout is present, so building is an option too. */
  has_crate: boolean;
}

export function nativeClientStatus(): Promise<NativeClientStatus> {
  return apiGet<NativeClientStatus>('/hassault/client/status');
}

export function removeNativeClient(version = ''): Promise<{ removed: boolean }> {
  return apiPost<{ removed: boolean }>('/hassault/client/remove', { version });
}

/** One progress event from `client_install.install_client`. */
export interface ClientInstallEvent {
  status?: 'resolving' | 'downloading' | 'verifying' | 'done';
  error?: string;
  asset?: string;
  total?: number;
  completed?: number;
  version?: string;
  verified?: boolean;
}

/**
 * Download the prebuilt client, reporting progress as it goes.
 *
 * NDJSON rather than a plain POST for the same reason `llamacpp/install` is: this
 * is tens of megabytes over somebody's connection, and a request that merely takes
 * a minute to return is indistinguishable from one that has hung.
 *
 * Resolves with the terminal event. A stream that ends with neither `done` nor an
 * `error` is reported as one rather than resolving quietly — a truncated install
 * that says nothing is exactly the state `install_client` deletes its directory to
 * avoid leaving behind.
 */
export async function installNativeClient(
  onEvent: (event: ClientInstallEvent) => void,
  version = '',
): Promise<ClientInstallEvent> {
  const res = await fetch(apiUrl('/api/hassault/client/install'), {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ version }),
  });
  if (!res.ok || !res.body) {
    throw new Error(`could not start the client install: ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // A holder rather than a bare `let`: every write happens inside `consume`, and
  // TypeScript's control-flow analysis cannot see through the closure — it narrows
  // a plain local to `null` and then to `never` at the check below.
  const seen: { last: ClientInstallEvent | null } = { last: null };

  const consume = (line: string) => {
    const text = line.trim();
    if (!text) return;
    let event: ClientInstallEvent;
    try {
      event = JSON.parse(text) as ClientInstallEvent;
    } catch {
      // A half-written line is normal mid-stream; the loop below only calls this
      // on complete ones, so anything unparseable here is genuinely malformed and
      // is worth skipping rather than killing the install over.
      return;
    }
    seen.last = event;
    onEvent(event);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    // The last element is whatever came after the final newline: a partial line,
    // or '' when the chunk ended cleanly. Either way it is not ready yet.
    buffer = lines.pop() ?? '';
    for (const line of lines) consume(line);
  }
  consume(buffer);

  const terminal = seen.last;
  if (!terminal || (!terminal.error && terminal.status !== 'done')) {
    return { error: 'the install ended without finishing' };
  }
  return terminal;
}

export interface SkinDefinition {
  id: string;
  name: string;
  weaponId: string;
  rarity:
    | 'consumer'
    | 'industrial'
    | 'mil_spec'
    | 'restricted'
    | 'classified'
    | 'covert'
    | 'special';
  rarityColor: string;
  collection: string;
  baseColor: string;
  accentColor: string;
  patternType: string;
  description: string;
}

export interface SkinInstance {
  instanceId: string;
  skinId: string;
  floatValue: number;
  wearName: string;
  patternSeed: number;
  acquiredAt: number;
  isEquipped: boolean;
  isTradable: boolean;
  statTrackerKills: number | null;
  definition?: SkinDefinition;
}

export function listSkinCatalog(): Promise<SkinDefinition[]> {
  return apiGet<SkinDefinition[]>('/hassault/skins/catalog');
}

export function getSkinInventory(): Promise<SkinInstance[]> {
  return apiGet<SkinInstance[]>('/hassault/skins/inventory');
}

export function equipSkin(instanceId: string): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>(
    `/hassault/skins/equip?instance_id=${encodeURIComponent(instanceId)}`,
    {},
  );
}

/** What the care-package banner is allowed to offer, straight from the ledger. */
export interface DropStatus {
  level: number;
  totalXp: number;
  levelProgressPercent: number;
  dropsEarned: number;
  dropsClaimed: number;
  available: number;
  xpPerLevel: number;
  xpToNextDrop: number;
}

export function getDropStatus(): Promise<DropStatus> {
  return apiGet<DropStatus>('/hassault/skins/drops');
}

/**
 * Spend one level-up entitlement.
 *
 * Rejects with the server's reason when there is nothing to spend — the button
 * is disabled on `available`, but the gate that matters is the 409, since the
 * count the browser is holding is a copy.
 */
export function claimLevelUpDrop(): Promise<SkinInstance & { remaining: number }> {
  return apiPost<SkinInstance & { remaining: number }>('/hassault/skins/claim_drop', {});
}

export function executeTradeUp(instanceIds: string[]): Promise<SkinInstance> {
  return apiPost<SkinInstance>('/hassault/skins/tradeup', instanceIds);
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

/** Who this node plays as. `enlisted` is having a username — the backend refuses a
 * join without one, so the pane must too. Mirrors `models.SessionInfo`. */
export interface SessionInfo {
  signed_in: boolean;
  account_id: string | null;
  display_name: string | null;
  username: string | null;
  /** Pre-fill for the chooser when `username` is null. A suggestion, not a hold. */
  suggested_username: string;
  enlisted: boolean;
}

/** `refresh` re-reads the account from the game server, which is how a username
 * claimed on another machine shows up here. Slower, so it's opt-in. */
export function getSession(refresh = false): Promise<SessionInfo> {
  return apiGet<SessionInfo>(`/hassault/session${refresh ? '?refresh=true' : ''}`);
}

export interface PostMatchSummary {
  mapName: string;
  won: boolean;
  kills: number;
  deaths: number;
  headshots: number;
  headshotPercent: number;
  damageDealt: number;
  isMvp: boolean;
  /** How many other players were in the room — bots included. Zero is a solo
   * warm-up, and the card says so rather than calling it a victory. */
  opponents: number;
  xpGained: number;
  currentLevel: number;
  levelProgressPercent: number;
  /** Career XP, summed from the match rows rather than counted separately. */
  totalXp: number;
  earnedDrop: SkinInstance | null;
  timestamp: number;
  /** The row this came from, in `hassault_matches`. */
  matchId: string;
}

export function getProcessStatus(): Promise<{ running: boolean; pid?: number }> {
  return apiGet<{ running: boolean; pid?: number }>('/hassault/match/process_status');
}

export function getLatestMatchSummary(): Promise<PostMatchSummary | null> {
  return apiGet<PostMatchSummary | null>('/hassault/match/latest_summary');
}

export function dismissMatchSummary(): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>('/hassault/match/dismiss_summary', {});
}
