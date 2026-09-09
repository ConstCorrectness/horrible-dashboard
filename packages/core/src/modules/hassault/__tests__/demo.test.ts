import { describe, it, expect } from 'vitest';
import {
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
      recorder.start('hd_junkflea', { 1: 'Player1', 2: 'Player2' });
      expect(recorder.isRecording()).toBe(true);

      recorder.recordFrame([
        {
          id: 1,
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
          id: 2,
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
        killerId: 1,
        victimId: 2,
        weapon: 'sniper',
        nutshot: true,
        streak: 2,
      });

      recorder.recordFrame([
        {
          id: 1,
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
          id: 2,
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
      expect(player.spectatorTargetId).toBe(1);
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
});
