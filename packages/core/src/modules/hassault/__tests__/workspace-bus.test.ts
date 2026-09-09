import { describe, expect, it, beforeEach } from 'vitest';
import {
  broadcastMatchTelemetry,
  broadcastWeaponEquipped,
  dispatchConsoleCommand,
  getLatestMatchTelemetry,
  onConsoleCommand,
  onMapEditorInspect,
  onMapLoadRequested,
  onMatchTelemetry,
  onWeaponEquipped,
  onWeaponInspectRequested,
  requestMapEditorInspect,
  requestMapLoad,
  requestWeaponInspect,
  resetWorkspaceBusForTesting,
  takePendingMapEditorInspect,
  takePendingMapLoad,
  takePendingWeaponInspect,
  type MatchTelemetry,
} from '../workspace-bus';

describe('workspace-bus interoperability', () => {
  beforeEach(() => {
    resetWorkspaceBusForTesting();
  });

  describe('map load pipeline', () => {
    it('notifies listeners when map load is requested', () => {
      const received: any[] = [];
      const unsub = onMapLoadRequested((req) => received.push(req));

      requestMapLoad({
        mapName: 'ac_desert',
        spawn: { x: 12, y: 15, z: 2, yaw: 1.57 },
        source: 'studio',
      });

      expect(received).toHaveLength(1);
      expect(received[0].mapName).toBe('ac_desert');
      expect(received[0].spawn?.x).toBe(12);
      expect(received[0].source).toBe('studio');

      unsub();
      requestMapLoad({ mapName: 'ac_complex', source: 'console' });
      expect(received).toHaveLength(1);
    });

    it('parks intent for unmounted pane to consume via takePendingMapLoad', () => {
      expect(takePendingMapLoad()).toBeNull();

      requestMapLoad({
        mapName: 'draft:abc-123',
        source: 'studio',
        reload: true,
      });

      const pending = takePendingMapLoad();
      expect(pending).not.toBeNull();
      expect(pending?.mapName).toBe('draft:abc-123');
      expect(pending?.reload).toBe(true);

      // Consuming clears it
      expect(takePendingMapLoad()).toBeNull();
    });
  });

  describe('map editor inspect pipeline', () => {
    it('allows play session to request editor inspect with camera coordinates', () => {
      const received: any[] = [];
      const unsub = onMapEditorInspect((req) => received.push(req));

      requestMapEditorInspect({
        mapName: 'ac_desert',
        camera: { x: 20, y: 30, z: 4, yaw: 0.5 },
        source: 'play',
      });

      expect(received).toHaveLength(1);
      expect(received[0].mapName).toBe('ac_desert');
      expect(received[0].camera?.y).toBe(30);

      unsub();
    });

    it('parks editor inspect intent when studio is not yet open', () => {
      requestMapEditorInspect({
        mapName: 'custom_arena',
        source: 'menu',
      });

      const pending = takePendingMapEditorInspect();
      expect(pending?.mapName).toBe('custom_arena');
      expect(takePendingMapEditorInspect()).toBeNull();
    });
  });

  describe('weapon inspect and equip pipeline', () => {
    it('synchronizes armory inspect with 3D model studio', () => {
      const received: any[] = [];
      const unsub = onWeaponInspectRequested((req) => received.push(req));

      requestWeaponInspect({
        weaponId: 'karambit',
        skinId: 'karambit_fade',
        floatValue: 0.012,
        patternSeed: 412,
        source: 'armory',
      });

      expect(received).toHaveLength(1);
      expect(received[0].weaponId).toBe('karambit');
      expect(received[0].skinId).toBe('karambit_fade');
      expect(received[0].floatValue).toBe(0.012);

      unsub();
    });

    it('consumes pending weapon inspect on studio mount', () => {
      requestWeaponInspect({
        weaponId: 'ak47',
        skinId: 'ak47_vulcan',
        source: 'armory',
      });

      const pending = takePendingWeaponInspect();
      expect(pending?.weaponId).toBe('ak47');
      expect(takePendingWeaponInspect()).toBeNull();
    });

    it('broadcasts weapon equipped event across panes', () => {
      const equips: any[] = [];
      const unsub = onWeaponEquipped((req) => equips.push(req));

      broadcastWeaponEquipped({
        weaponId: 'm4a1',
        skinId: 'm4a1_hyper_beast',
        source: 'armory',
      });

      expect(equips).toHaveLength(1);
      expect(equips[0].weaponId).toBe('m4a1');
      expect(equips[0].skinId).toBe('m4a1_hyper_beast');

      unsub();
    });
  });

  describe('telemetry and companion pipeline', () => {
    it('broadcasts live match telemetry to companion and radar sidecars', () => {
      const updates: MatchTelemetry[] = [];
      const unsub = onMatchTelemetry((data) => updates.push(data));

      const mockTelemetry: MatchTelemetry = {
        phase: 'playing',
        mapName: 'ac_desert',
        room: 'room-42',
        online: true,
        player: {
          x: 10,
          y: 20,
          z: 1,
          yaw: 3.14,
          health: 100,
          armor: 100,
          weapon: 'ak47',
          ammo: 30,
          carried: 90,
          godMode: false,
          noclip: false,
        },
        roster: [],
        spotted: ['bot-1', 'bot-2'],
        roundTime: 115,
        score: { ct: 3, t: 5 },
        killEvents: [],
        lastUpdated: Date.now(),
      };

      broadcastMatchTelemetry(mockTelemetry);

      expect(updates).toHaveLength(1);
      expect(updates[0].mapName).toBe('ac_desert');
      expect(updates[0].player?.health).toBe(100);
      expect(getLatestMatchTelemetry()).toEqual(mockTelemetry);

      unsub();
    });
  });

  describe('console command pipeline', () => {
    it('allows external console pane to dispatch commands to game', () => {
      const dispatched: any[] = [];
      const unsub = onConsoleCommand((req) => dispatched.push(req));

      dispatchConsoleCommand({
        command: 'god',
        source: 'console',
      });

      expect(dispatched).toHaveLength(1);
      expect(dispatched[0].command).toBe('god');
      expect(dispatched[0].source).toBe('console');

      unsub();
    });
  });
});
