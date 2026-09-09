/**
 * HorribleAssault Workspace Interoperability Event Bus.
 *
 * Provides a strongly-typed, reactive event channel and state synchronization
 * between all hAssault windows and panes:
 *  - HorribleAssaultPanel (play/match/engine)
 *  - ModelStudioPanel (3D asset inspector, level editor, CAD/DCC scene outliner)
 *  - ArmoryMarketplace / ArmoryPanel (inventory, master catalog, trade-up forge)
 *  - StandaloneMatchCompanionPanel (telemetry, live vitals, team scoreboard)
 *  - StandaloneRadarPanel (2D tactical minimap, enemy blips, callouts)
 *  - DeveloperConsole (CVars, concommands, macros)
 */

import type { PlayerRow } from './net';
import type { KillFeedEvent } from './panels/KillFeed';

export interface MapCoordinates {
  x: number;
  y: number;
  z: number;
  yaw?: number | null;
}

export interface MapLoadRequest {
  mapName: string;
  spawn?: MapCoordinates;
  reload?: boolean;
  source: 'studio' | 'console' | 'armory' | 'menu' | 'companion';
}

export interface MapEditorInspectRequest {
  mapName: string;
  camera?: MapCoordinates;
  source: 'play' | 'console' | 'menu' | 'companion';
}

export interface WeaponInspectRequest {
  weaponId: string;
  skinId?: string;
  floatValue?: number;
  patternSeed?: number;
  source: 'armory' | 'play' | 'studio' | 'console';
}

export interface WeaponEquipRequest {
  weaponId: string;
  skinId: string;
  source: 'armory' | 'studio' | 'console';
}

export interface PlayerVitalsTelemetry {
  x: number;
  y: number;
  z: number;
  yaw: number;
  health: number;
  armor: number;
  weapon: string;
  ammo: number;
  carried: number;
  godMode?: boolean;
  noclip?: boolean;
  isSprinting?: boolean;
  isSliding?: boolean;
  stamina?: number;
}

export interface MatchTelemetry {
  phase: 'boot' | 'loading' | 'signed_out' | 'signin' | 'enlist' | 'main_menu' | 'menu' | 'playing' | 'debrief' | 'native' | string;
  mapName: string;
  room: string;
  online: boolean;
  player: PlayerVitalsTelemetry | null;
  roster: PlayerRow[];
  spotted: readonly string[];
  roundTime: number;
  score: { ct: number; t: number };
  killEvents: KillFeedEvent[];
  activeSpeakers?: string[];
  lastUpdated: number;
}

export interface ConsoleCommandRequest {
  command: string;
  source: 'console' | 'panel' | 'ui';
}

// ---------------------------------------------------------------------------
// Bus State & Listener Registries
// ---------------------------------------------------------------------------

let pendingMapLoad: MapLoadRequest | null = null;
const mapLoadListeners = new Set<(req: MapLoadRequest) => void>();

let pendingMapEditorInspect: MapEditorInspectRequest | null = null;
const mapEditorInspectListeners = new Set<(req: MapEditorInspectRequest) => void>();

let pendingWeaponInspect: WeaponInspectRequest | null = null;
const weaponInspectListeners = new Set<(req: WeaponInspectRequest) => void>();

const weaponEquipListeners = new Set<(req: WeaponEquipRequest) => void>();

let latestTelemetry: MatchTelemetry | null = null;
const telemetryListeners = new Set<(data: MatchTelemetry) => void>();

const consoleCommandListeners = new Set<(req: ConsoleCommandRequest) => void>();

// ---------------------------------------------------------------------------
// Map Load Pipeline
// ---------------------------------------------------------------------------

export function requestMapLoad(req: MapLoadRequest): void {
  pendingMapLoad = req;
  for (const listener of mapLoadListeners) {
    listener(req);
  }
}

export function takePendingMapLoad(): MapLoadRequest | null {
  const req = pendingMapLoad;
  pendingMapLoad = null;
  return req;
}

export function onMapLoadRequested(listener: (req: MapLoadRequest) => void): () => void {
  mapLoadListeners.add(listener);
  return () => {
    mapLoadListeners.delete(listener);
  };
}

// ---------------------------------------------------------------------------
// Map Editor / Studio Inspect Pipeline
// ---------------------------------------------------------------------------

export function requestMapEditorInspect(req: MapEditorInspectRequest): void {
  pendingMapEditorInspect = req;
  for (const listener of mapEditorInspectListeners) {
    listener(req);
  }
}

export function takePendingMapEditorInspect(): MapEditorInspectRequest | null {
  const req = pendingMapEditorInspect;
  pendingMapEditorInspect = null;
  return req;
}

export function onMapEditorInspect(listener: (req: MapEditorInspectRequest) => void): () => void {
  mapEditorInspectListeners.add(listener);
  return () => {
    mapEditorInspectListeners.delete(listener);
  };
}

// ---------------------------------------------------------------------------
// Weapon Inspect & Equip Pipeline
// ---------------------------------------------------------------------------

export function requestWeaponInspect(req: WeaponInspectRequest): void {
  pendingWeaponInspect = req;
  for (const listener of weaponInspectListeners) {
    listener(req);
  }
}

export function takePendingWeaponInspect(): WeaponInspectRequest | null {
  const req = pendingWeaponInspect;
  pendingWeaponInspect = null;
  return req;
}

export function onWeaponInspectRequested(listener: (req: WeaponInspectRequest) => void): () => void {
  weaponInspectListeners.add(listener);
  return () => {
    weaponInspectListeners.delete(listener);
  };
}

export function broadcastWeaponEquipped(req: WeaponEquipRequest): void {
  for (const listener of weaponEquipListeners) {
    listener(req);
  }
}

export function onWeaponEquipped(listener: (req: WeaponEquipRequest) => void): () => void {
  weaponEquipListeners.add(listener);
  return () => {
    weaponEquipListeners.delete(listener);
  };
}

// ---------------------------------------------------------------------------
// Live Match Telemetry Pipeline
// ---------------------------------------------------------------------------

export function broadcastMatchTelemetry(telemetry: MatchTelemetry): void {
  latestTelemetry = telemetry;
  for (const listener of telemetryListeners) {
    listener(telemetry);
  }
}

export function getLatestMatchTelemetry(): MatchTelemetry | null {
  return latestTelemetry;
}

export function onMatchTelemetry(listener: (data: MatchTelemetry) => void): () => void {
  telemetryListeners.add(listener);
  return () => {
    telemetryListeners.delete(listener);
  };
}

// ---------------------------------------------------------------------------
// Console Command Pipeline
// ---------------------------------------------------------------------------

export function dispatchConsoleCommand(req: ConsoleCommandRequest): void {
  for (const listener of consoleCommandListeners) {
    listener(req);
  }
}

export function onConsoleCommand(listener: (req: ConsoleCommandRequest) => void): () => void {
  consoleCommandListeners.add(listener);
  return () => {
    consoleCommandListeners.delete(listener);
  };
}

/** Reset all parked intents and listeners — primarily for test isolation. */
export function resetWorkspaceBusForTesting(): void {
  pendingMapLoad = null;
  mapLoadListeners.clear();
  pendingMapEditorInspect = null;
  mapEditorInspectListeners.clear();
  pendingWeaponInspect = null;
  weaponInspectListeners.clear();
  weaponEquipListeners.clear();
  latestTelemetry = null;
  telemetryListeners.clear();
  consoleCommandListeners.clear();
}
