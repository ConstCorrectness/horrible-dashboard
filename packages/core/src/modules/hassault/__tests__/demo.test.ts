import { describe, it, expect } from 'vitest';
import { afterEach, vi } from 'vitest';
import {
  DemoError,
  freecamFromView,
  interpolateRadians,
  parseDemo,
  rowFromSnapshot,
  snapshotFromRow,
  viewFromFreecam,
  catmullRom,
  interpolateAngle,
  DroneFreecam,
  DollyPath,
  DemoRecorder,
  DemoPlayer,
} from '../demo';

describe('HADEMO Demo Recording & Replay Suite', () => {
  describe('Spline & Math Utilities', () => {
    it('catmullRom interpolates between points smoothly', () => {
      // At t=0, it should equal p1
      expect(catmullRom(0, 10, 20, 30, 0)).toBeCloseTo(10);
      // At t=1, it should equal p2
      expect(catmullRom(0, 10, 20, 30, 1)).toBeCloseTo(20);
      // At t=0.5 on a linear progression, it should be 15
      expect(catmullRom(0, 10, 20, 30, 0.5)).toBeCloseTo(15);
    });

    it('interpolateAngle handles 360 degree wraparound without 350-degree flips', () => {
      // 350 -> 10 should interpolate across 0, not backwards 340 degrees
      const mid = interpolateAngle(350, 10, 0.5);
      expect((mid + 360) % 360).toBeCloseTo(0);

      // Normal angle interpolation
      expect(interpolateAngle(40, 60, 0.5)).toBeCloseTo(50);
    });
  });

  describe('DroneFreecam (6-DOF Cinematic Drone)', () => {
    it('initializes with default spectator properties and updates with inertia', () => {
      const drone = new DroneFreecam();
      drone.active = true;
      drone.setPosition(0, 0, 10, 0, 0, 0, 90);

      drone.update(0.016, { forward: 1.0 });
      expect(drone.y).toBeGreaterThan(0);
      expect(drone.z).toBe(10);

      // Camera roll input (Q / E)
      drone.update(0.016, { rollInput: 1.0 });
      expect(drone.vRoll).toBeGreaterThan(0);

      // FOV zoom bounds (15° to 120°)
      drone.update(0.016, { fovDelta: -100 });
      expect(drone.fov).toBe(15);
      drone.update(0.016, { fovDelta: 200 });
      expect(drone.fov).toBe(120);
    });
  });

  describe('DollyPath (Catmull-Rom Spline Keyframing)', () => {
    it('evaluates smooth camera trajectory along keyframes', () => {
      const path = new DollyPath();
      path.addKeyframe({ time: 0, x: 0, y: 0, z: 2, yaw: 0, pitch: 0, roll: 0, fov: 90 });
      path.addKeyframe({ time: 2, x: 10, y: 20, z: 4, yaw: 45, pitch: 10, roll: 5, fov: 75 });
      path.addKeyframe({ time: 4, x: 20, y: 40, z: 2, yaw: 90, pitch: 0, roll: 0, fov: 90 });

      const kf0 = path.evaluate(0);
      expect(kf0?.x).toBeCloseTo(0);

      const kfMid = path.evaluate(1.0);
      expect(kfMid).not.toBeNull();
      expect(kfMid!.x).toBeGreaterThan(0);
      expect(kfMid!.x).toBeLessThan(10);
      expect(kfMid!.fov).toBeLessThan(90);

      const kfEnd = path.evaluate(4.5);
      expect(kfEnd?.x).toBeCloseTo(20);
    });
  });

  describe('DemoRecorder & DemoPlayer', () => {
    it('records match frames and generates kill bookmarks with nutshot/headshot detection', () => {
      const recorder = new DemoRecorder();
      recorder.start('hd_junkflea', { 1: 'Player1', 2: 'Player2' }, '1');
      expect(recorder.isRecording()).toBe(true);

      recorder.recordFrame([
        {
          id: '1',
          name: 'Player1',
          team: 0,
          x: 0,
          y: 0,
          z: 0,
          yaw: 0,
          pitch: 0,
          weapon: 3,
          hp: 100,
          sprinting: true,
        },
        {
          id: '2',
          name: 'Player2',
          team: 1,
          x: 10,
          y: 10,
          z: 0,
          yaw: 180,
          pitch: 0,
          weapon: 1,
          hp: 100,
        },
      ]);

      // Record a nutshot kill event
      recorder.recordEvent({
        type: 'kill',
        killerId: '1',
        victimId: '2',
        weapon: 'sniper',
        nutshot: true,
        streak: 2,
      });

      recorder.recordFrame([
        {
          id: '1',
          name: 'Player1',
          team: 0,
          x: 2,
          y: 2,
          z: 0,
          yaw: 0,
          pitch: 0,
          weapon: 3,
          hp: 100,
          sprinting: false,
          sliding: true,
        },
        {
          id: '2',
          name: 'Player2',
          team: 1,
          x: 10,
          y: 10,
          z: 0,
          yaw: 180,
          pitch: 0,
          weapon: 1,
          hp: 0,
        },
      ]);

      const demo = recorder.stop();
      expect(demo).not.toBeNull();
      expect(demo!.header.magic).toBe('HADEMO');
      expect(demo!.header.map).toBe('hd_junkflea');
      expect(demo!.frames.length).toBe(2);
      expect(demo!.bookmarks.length).toBe(1);
      expect(demo!.bookmarks[0].label).toContain('STREAK');

      // Now test playback in DemoPlayer
      const player = new DemoPlayer();
      player.load(demo!);
      expect(player.spectatorTargetId).toBe('1');
      expect(player.isPlaying).toBe(false);

      player.play();
      expect(player.isPlaying).toBe(true);

      player.setSpeed(0.5);
      expect(player.playbackSpeed).toBe(0.5);

      // Bookmark seek
      player.seekToBookmark(0);
      expect(player.currentTimeMs).toBeGreaterThanOrEqual(0);

      // Clean view toggle
      expect(player.cleanView).toBe(false);
      player.toggleCleanView();
      expect(player.cleanView).toBe(true);

      // Sample frame interpolation
      const sample = player.sampleCurrentFrame();
      expect(sample.players.length).toBe(2);
    });
  });

  describe('wired into the game (what the panel relies on)', () => {
    afterEach(() => vi.restoreAllMocks());

    it('refuses an empty recording with a reason instead of loading it', () => {
      // Byte for byte what every demo saved before the recorder was wired holds.
      const empty = JSON.stringify({
        header: {
          magic: 'HADEMO',
          version: 1,
          map: 'hd_dust2',
          recordedAt: 1793227697703,
          tickRate: 60,
          playerNames: { 0: 'constcorrectness' },
          durationMs: 92776.3,
        },
        frames: [],
        bookmarks: [],
      });
      expect(() => parseDemo(empty)).toThrow(DemoError);
      expect(() => parseDemo(empty)).toThrow(/no frames/);
      expect(() => parseDemo('{"header":{"magic":"NOPE"}}')).toThrow(/not a .hademo/);
      expect(() => parseDemo('not json')).toThrow(DemoError);
    });

    it('reads ids as strings and the duration from the frames when the header is short', () => {
      const demo = parseDemo(
        JSON.stringify({
          header: { magic: 'HADEMO', version: 1, map: 'hd_pit', recordedAt: 0, tickRate: 30, playerNames: {}, durationMs: 0, pov: 7 },
          frames: [
            { tick: 0, timestamp: 0, players: [{ id: 7, name: 'a', team: 0, x: 0, y: 0, z: 0, yaw: 0, pitch: 0, weapon: 2, hp: 100 }], events: [] },
            { tick: 1, timestamp: 500, players: [{ id: 7, name: 'a', team: 0, x: 1, y: 0, z: 0, yaw: 0, pitch: 0, weapon: 2, hp: 100 }], events: [] },
          ],
        }),
      );
      expect(demo.frames[0].players[0].id).toBe('7');
      expect(demo.header.pov).toBe('7');
      expect(demo.header.durationMs).toBe(500);
      expect(demo.bookmarks).toEqual([]);
    });

    it('keeps one frame per tick whatever the frame rate', () => {
      let now = 1000;
      vi.spyOn(performance, 'now').mockImplementation(() => now);
      const rec = new DemoRecorder();
      expect(rec.due()).toBe(false); // not recording
      rec.start('hd_pit', {}, 'you');
      expect(rec.due()).toBe(true);
      rec.recordFrame([]);
      now += 10; // a 100 Hz display
      expect(rec.due()).toBe(false);
      now += 30; // past 1/30 s
      expect(rec.due()).toBe(true);
    });

    it('round-trips a drawn body through a frame', () => {
      const row = {
        id: 'p3', name: 'Rook', team: 1, x: 4, y: 5, z: 6, yaw: 1.2, pitch: -0.3,
        ground: true, stale: false, rtt: 0, hp: 40, alive: true, weapon: 4,
        kills: 0, deaths: 0, bot: false, crouch: 0.5,
      };
      expect(rowFromSnapshot(snapshotFromRow(row))).toEqual(row);
    });

    it('turns the short way round in radians, not degrees', () => {
      // Just under +pi to just under -pi is a hair's turn, not a full one.
      const mid = interpolateRadians(3.1, -3.1, 0.5);
      expect(Math.abs(Math.abs(mid) - Math.PI)).toBeLessThan(0.01);
    });

    it("starts behind the recorder's eyes, and Play at the end starts over", () => {
      const player = new DemoPlayer();
      player.load(
        parseDemo(
          JSON.stringify({
            header: { magic: 'HADEMO', version: 1, map: 'hd_pit', recordedAt: 0, tickRate: 30, playerNames: {}, durationMs: 1000, pov: 'you' },
            frames: [
              { tick: 0, timestamp: 0, players: [
                { id: 'p1', name: 'b', team: 1, x: 9, y: 9, z: 0, yaw: 0, pitch: 0, weapon: 2, hp: 100 },
                { id: 'you', name: 'a', team: 0, x: 0, y: 0, z: 0, yaw: 0, pitch: 0, weapon: 4, hp: 100 },
              ], events: [] },
            ],
          }),
        ),
      );
      expect(player.spectatorTargetId).toBe('you');
      player.play();
      player.update(5); // runs off the end
      expect(player.isPlaying).toBe(false);
      expect(player.ended).toBe(true);
      player.togglePlay();
      expect(player.isPlaying).toBe(true);
      expect(player.currentTimeMs).toBe(0);
    });

    it('starts the drone exactly where the view is, facing the same way', () => {
      const cam = new DroneFreecam();
      cam.active = true;
      for (const yaw of [0, 0.7, 2.4, -1.9]) {
        const v = freecamFromView(yaw, 0.2);
        cam.setPosition(0, 0, 0, v.yaw, v.pitch);
        const back = viewFromFreecam(cam);
        expect(back.yaw).toBeCloseTo(yaw);
        expect(back.pitch).toBeCloseTo(0.2);
        // And flying forward moves along the player's forward, (cos y, sin y).
        cam.update(0.05, { forward: 1 });
        const len = Math.hypot(cam.x, cam.y);
        expect(cam.x / len).toBeCloseTo(Math.cos(yaw));
        expect(cam.y / len).toBeCloseTo(Math.sin(yaw));
      }
    });
  });
});
