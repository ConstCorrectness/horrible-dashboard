/**
 * Shell indicators are collected from manifests like every other contribution —
 * and they matter more than most, because the canonical one says "your screen is
 * being broadcast" and is the only place that says it with the pane closed.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../registry';

const Stub = () => null;

beforeEach(() => registry.resetForTests());
afterEach(() => registry.resetForTests());

describe('registry.shellIndicators', () => {
  it('is empty when nothing contributes one', () => {
    registry.register({ id: 'plain', title: 'Plain' });
    expect(registry.shellIndicators).toEqual([]);
  });

  it('collects every module’s indicators in registration order', () => {
    registry.register({
      id: 'first',
      title: 'First',
      shellIndicators: [{ id: 'first.live', component: Stub }],
    });
    registry.register({
      id: 'second',
      title: 'Second',
      shellIndicators: [
        { id: 'second.a', component: Stub },
        { id: 'second.b', component: Stub },
      ],
    });
    expect(registry.shellIndicators.map((i) => i.id)).toEqual([
      'first.live',
      'second.a',
      'second.b',
    ]);
  });

  it('does not duplicate on re-registration (StrictMode)', () => {
    const manifest = {
      id: 'again',
      title: 'Again',
      shellIndicators: [{ id: 'again.live', component: Stub }],
    };
    registry.register(manifest);
    registry.register(manifest);
    expect(registry.shellIndicators).toHaveLength(1);
  });
});
