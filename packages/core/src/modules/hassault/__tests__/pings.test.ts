import { describe, expect, it } from 'vitest';
import { CALLOUT_OPTIONS, getCalloutFromDelta } from '../panels/CalloutWheel';
import { Predictor, type PingIntent } from '../net';
import { createPlayer } from '../player';
import { buildWorld } from './build-world';

describe('CalloutWheel', () => {
  it('identifies quadrants from mouse movement delta correctly', () => {
    // Center deadzone (< 15px) defaults to spotted
    expect(getCalloutFromDelta(0, 0)).toBe('spotted');
    expect(getCalloutFromDelta(5, 5)).toBe('spotted');
    expect(getCalloutFromDelta(-8, 3)).toBe('spotted');

    // Up -> spotted (negative Y)
    expect(getCalloutFromDelta(0, -50)).toBe('spotted');
    expect(getCalloutFromDelta(10, -60)).toBe('spotted');
    expect(getCalloutFromDelta(-10, -60)).toBe('spotted');

    // Right -> watch (positive X)
    expect(getCalloutFromDelta(50, 0)).toBe('watch');
    expect(getCalloutFromDelta(60, 10)).toBe('watch');
    expect(getCalloutFromDelta(60, -10)).toBe('watch');

    // Down -> danger (positive Y)
    expect(getCalloutFromDelta(0, 50)).toBe('danger');
    expect(getCalloutFromDelta(10, 60)).toBe('danger');
    expect(getCalloutFromDelta(-10, 60)).toBe('danger');

    // Left -> utility (negative X)
    expect(getCalloutFromDelta(-50, 0)).toBe('utility');
    expect(getCalloutFromDelta(-60, 10)).toBe('utility');
    expect(getCalloutFromDelta(-60, -10)).toBe('utility');
  });

  it('defines valid esports callout options for all 4 tactical kinds', () => {
    const kinds = ['spotted', 'watch', 'danger', 'utility'] as const;
    for (const kind of kinds) {
      const opt = CALLOUT_OPTIONS[kind];
      expect(opt).toBeDefined();
      expect(opt.kind).toBe(kind);
      expect(opt.label.length).toBeGreaterThan(0);
      expect(opt.color).toMatch(/rgb\(/);
      expect(opt.icon).toBeDefined();
    }
  });
});

describe('Predictor ping recording', () => {
  it('records tactical ping intent on the command frame', () => {
    const predictor = new Predictor();
    const world = buildWorld({ ssize: 16, rects: [{ x0: 0, y0: 0, x1: 15, y1: 15 }] });
    const player = createPlayer(4, 4, 0, 0);

    const ping: PingIntent = {
      kind: 'spotted',
      x: 14.5,
      y: 22.0,
      z: 1.0,
    };

    const cmdWithPing = predictor.record(
      world,
      player,
      { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false },
      0.016,
      undefined,
      undefined,
      undefined,
      false,
      undefined,
      ping,
    );

    expect(cmdWithPing.ping).toEqual(ping);

    // Subsequent command without ping does not leak or repeat it
    const cmdWithoutPing = predictor.record(
      world,
      player,
      { forward: 0, strafe: 0, jump: false, crouch: false, noclip: false },
      0.016,
    );

    expect(cmdWithoutPing.ping).toBeUndefined();
  });
});
