import { describe, expect, it } from 'vitest';

import { describeRunError } from '../run-error';

const available = () => true;
const unavailable = () => false;

describe('describeRunError', () => {
  it('blames the environment only when the probe cannot create a context', () => {
    const msg = describeRunError(new Error('Error creating WebGL context.'), 'three', unavailable);
    expect(msg).toMatch(/WebGL is unavailable here/);
    expect(msg).toContain('Error creating WebGL context.');
  });

  it('does not claim WebGL is disabled when the probe succeeds', () => {
    const msg = describeRunError(new Error('Error creating WebGL context.'), 'three', available);
    expect(msg).not.toMatch(/unavailable/);
    expect(msg).toMatch(/WebGL works in this environment/);
    expect(msg).toContain("getContext('2d')");
  });

  it('keeps the real error for scripts that merely mention "context"', () => {
    let probed = false;
    const msg = describeRunError(new ReferenceError('context is not defined'), 'canvas', () => {
      probed = true;
      return false;
    });
    expect(msg).toBe('Script error: context is not defined');
    expect(probed).toBe(false);
  });

  it('reports unrelated errors verbatim in WebGL modes', () => {
    const msg = describeRunError(new TypeError('x.foo is not a function'), 'babylon', unavailable);
    expect(msg).toBe('Script error: x.foo is not a function');
  });
});
