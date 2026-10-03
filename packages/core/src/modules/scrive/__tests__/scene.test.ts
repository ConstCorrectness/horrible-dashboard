/** `{r3f}` scenes: the frame's document and the tweakable params. */
import { describe, expect, it } from 'vitest';

import { paramValues, parseParams, sceneSrcdoc } from '../render/SceneFrame';

describe('sceneSrcdoc', () => {
  const doc = sceneSrcdoc('dark');
  const csp =
    /content="([^"]*)"/.exec(doc.slice(doc.indexOf('Content-Security-Policy')))?.[1] ?? '';

  it('lets the frame load nothing over the network', () => {
    expect(csp).toContain("default-src 'none'");
    // Every source list names only inline/eval/data/blob — no host, no 'self'.
    for (const directive of csp.split(';')) {
      expect(directive).not.toMatch(/https?:|'self'|\*/);
    }
    expect(doc).not.toMatch(/<script[^>]+src=/);
  });

  it('waits for the runtime to be posted in, from its parent only', () => {
    expect(doc).toContain('e.source !== parent');
    expect(doc).toContain('"runtime"');
    expect(doc).toContain('type: "frame"');
  });

  it('carries the page’s colour scheme, and nothing else from outside', () => {
    expect(doc).toContain('<meta name="color-scheme" content="dark">');
    expect(sceneSrcdoc('dark"><script>x</script>')).not.toContain('<script>x');
  });
});

describe('params', () => {
  it('reads the :params: JSON, and says what is wrong with a bad one', () => {
    expect(parseParams(undefined)).toEqual({});
    expect(parseParams('{"n": 3, "on": true}')).toEqual({ n: 3, on: true });
    expect(parseParams('[1]')).toBe(':params: must be a JSON object');
    expect(parseParams('{nope')).toMatch(/^:params: is not JSON/);
  });

  it('hands the scene plain values: a range spec contributes its value', () => {
    expect(
      paramValues({ n: 3, spread: { value: 1.5, min: 0, max: 3 }, color: '#ffffff', on: false }),
    ).toEqual({ n: 3, spread: 1.5, color: '#ffffff', on: false });
  });
});
