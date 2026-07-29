/**
 * The game's own key map: storage round trips, and the rules the pause menu
 * relies on being enforced here rather than in the UI.
 */
import { describe, expect, it } from 'vitest';

import {
  ACTIONS,
  DEFAULT_CONTROLS,
  RESERVED_CODES,
  SLOTS,
  boundTo,
  codeMap,
  defaultControls,
  describeControls,
  isDefaultControls,
  keyLabel,
  parseControls,
  serializeControls,
  setBinding,
} from '../controls';

describe('defaults', () => {
  it('binds every declared action', () => {
    for (const doc of ACTIONS) {
      expect(DEFAULT_CONTROLS[doc.action]?.length ?? 0).toBeGreaterThan(0);
    }
  });

  it('hands out an independent copy, so editing one map cannot poison the next', () => {
    const a = defaultControls();
    a.forward.push('KeyZ');
    expect(defaultControls().forward).not.toContain('KeyZ');
    expect(DEFAULT_CONTROLS.forward).not.toContain('KeyZ');
  });

  it('binds no key to two actions', () => {
    const map = codeMap(defaultControls());
    const codes = ACTIONS.flatMap((a) => DEFAULT_CONTROLS[a.action]);
    expect(map.size).toBe(codes.length);
  });
});

describe('storage', () => {
  it('stores nothing when nothing was changed', () => {
    expect(serializeControls(defaultControls())).toBe('{}');
    expect(isDefaultControls(defaultControls())).toBe(true);
  });

  it('round trips a rebind', () => {
    const next = setBinding(defaultControls(), 'jump', 0, 'KeyE');
    const raw = serializeControls(next);
    expect(parseControls(raw).jump).toEqual(['KeyE']);
    expect(isDefaultControls(next)).toBe(false);
  });

  it('stores only what differs, so later default changes still reach the player', () => {
    const next = setBinding(defaultControls(), 'jump', 0, 'KeyE');
    expect(JSON.parse(serializeControls(next))).toEqual({ jump: ['KeyE'] });
  });

  it('falls back to defaults on anything unreadable', () => {
    for (const raw of [undefined, '', 'not json', '[]', '42', 'null', '{"forward":"KeyW"}']) {
      expect(parseControls(raw).forward).toEqual(DEFAULT_CONTROLS.forward);
    }
  });

  it('drops junk inside an otherwise good document rather than losing the lot', () => {
    const parsed = parseControls('{"jump":["KeyE",7,""],"nonsense":["KeyQ"]}');
    expect(parsed.jump).toEqual(['KeyE']);
    expect(parsed.reload).toEqual(DEFAULT_CONTROLS.reload);
    expect('nonsense' in parsed).toBe(false);
  });

  it('refuses a reserved key that reached storage by hand', () => {
    // Escape is how you get back to the menu that would let you fix a mistake, so
    // an edited file must not be able to take it away.
    expect(parseControls('{"jump":["Escape"]}').jump).toEqual([]);
  });
});

describe('setBinding', () => {
  it('takes a key away from whatever held it, rather than firing both', () => {
    const next = setBinding(defaultControls(), 'jump', 0, 'KeyW');
    expect(next.jump).toEqual(['KeyW']);
    expect(next.forward).toEqual(['ArrowUp']);
    expect(codeMap(next).get('KeyW')).toBe('jump');
  });

  it('leaves an action alone when a key is rebound onto itself', () => {
    const next = setBinding(defaultControls(), 'forward', 0, 'KeyW');
    expect(next.forward).toEqual(DEFAULT_CONTROLS.forward);
  });

  it('clears a slot, promoting the alternate into it', () => {
    const next = setBinding(defaultControls(), 'forward', 0, null);
    expect(next.forward).toEqual(['ArrowUp']);
    expect(codeMap(next).has('KeyW')).toBe(false);
  });

  it('can leave an action unbound entirely', () => {
    let next = setBinding(defaultControls(), 'noclip', 0, null);
    next = setBinding(next, 'noclip', 0, null);
    expect(next.noclip).toEqual([]);
  });

  it('holds at most the declared number of keys', () => {
    let next = defaultControls();
    for (const code of ['KeyG', 'KeyH', 'KeyJ']) {
      next = setBinding(next, 'jump', 0, code);
    }
    expect(next.jump.length).toBeLessThanOrEqual(SLOTS);
  });

  it('refuses every reserved key', () => {
    const before = defaultControls();
    for (const code of RESERVED_CODES) {
      expect(setBinding(before, 'jump', 0, code)).toBe(before);
    }
  });

  it('does not mutate the map it was given', () => {
    const before = defaultControls();
    setBinding(before, 'jump', 0, 'KeyE');
    expect(before.jump).toEqual(DEFAULT_CONTROLS.jump);
  });
});

describe('lookups', () => {
  it('resolves a code to the action the frame loop reads', () => {
    const map = codeMap(defaultControls());
    expect(map.get('KeyW')).toBe('forward');
    expect(map.get('ArrowLeft')).toBe('left');
    expect(map.get('Digit3')).toBe('weapon3');
    expect(map.has('KeyP')).toBe(false);
  });

  it('reports what a key was bound to, for the rebind hint', () => {
    expect(boundTo(defaultControls(), 'Tab')).toBe('scores');
    expect(boundTo(defaultControls(), 'KeyP')).toBe(null);
  });

  it('labels keys as something worth printing on a cap', () => {
    expect(keyLabel('KeyW')).toBe('W');
    expect(keyLabel('Digit4')).toBe('4');
    expect(keyLabel('ArrowUp')).toBe('↑');
    expect(keyLabel('Space')).toBe('Space');
    expect(keyLabel('F7')).toBe('F7');
  });

  it("describes the player's own keys, not the shipped ones", () => {
    expect(describeControls(defaultControls())).toContain('WASD');
    const next = setBinding(defaultControls(), 'forward', 0, 'KeyI');
    expect(describeControls(next)).toContain('IASD');
  });
});
