import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { layoutStore } from '../../layout/store';
import { seedFromPreset, type FramePreset } from '../../layout/presets';
import { releaseCapture, requestCapture } from '../capture';
import { followCaptureWithEscapeLock, hostTakesEscape } from '../keyboard-lock';

// Core vitest runs without a DOM, so the two globals the lock reads are stubbed:
// `document` (fullscreen / pointer-lock state) and `navigator.keyboard`.
const preset: FramePreset = {
  id: 'test',
  name: 'Test',
  frame: { center: { split: 'row', children: [{ pane: 'editor.buffer' }] } },
};

const doc = {
  fullscreenElement: null as unknown,
  pointerLockElement: null as unknown,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
};
const keyboard = { lock: vi.fn(async () => {}), unlock: vi.fn() };

function editorInstance(): string {
  const center = layoutStore.getSnapshot().frame.center;
  if (center.kind === 'area') return center.tabs[0]!.instanceId;
  const first = center.children[0]!;
  if (first.kind !== 'area') throw new Error('unexpected layout');
  return first.tabs[0]!.instanceId;
}

function captureEditor(escape: 'passthrough' | 'release'): void {
  const id = editorInstance();
  layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: id });
  requestCapture({ mode: 'keyboard', escape, instanceId: id, viewId: 'editor.buffer' });
}

beforeEach(() => {
  vi.stubGlobal('document', doc);
  vi.stubGlobal('navigator', { keyboard });
  doc.fullscreenElement = null;
  doc.pointerLockElement = null;
  keyboard.lock.mockClear();
  keyboard.unlock.mockClear();
  releaseCapture();
  layoutStore.resetForTests();
  layoutStore.dispatch({
    type: 'LOAD_WORKSPACE',
    workspaceId: 'test',
    frame: seedFromPreset(preset, { knownViews: new Set(['editor.buffer']) }),
  });
});

afterEach(() => {
  releaseCapture();
  vi.unstubAllGlobals();
});

describe('hostTakesEscape', () => {
  it('is false in a plain window: the page owns Escape', () => {
    expect(hostTakesEscape()).toBe(false);
  });

  it('is true under pointer lock with no Keyboard Lock', () => {
    vi.stubGlobal('navigator', {});
    doc.pointerLockElement = {};
    expect(hostTakesEscape()).toBe(true);
  });

  it('is false in document fullscreen when Keyboard Lock can hold the key', () => {
    doc.fullscreenElement = {};
    expect(hostTakesEscape()).toBe(false);
  });
});

describe('followCaptureWithEscapeLock', () => {
  it('locks Escape while an editor holds a passthrough capture, and unlocks after', () => {
    doc.fullscreenElement = {};
    const stop = followCaptureWithEscapeLock();
    captureEditor('passthrough');
    expect(keyboard.lock).toHaveBeenCalledWith(['Escape']);

    releaseCapture();
    expect(keyboard.unlock).toHaveBeenCalledTimes(1);
    stop();
  });

  it('leaves the host its Escape for a release-policy capture', () => {
    doc.fullscreenElement = {};
    const stop = followCaptureWithEscapeLock();
    captureEditor('release');
    expect(keyboard.lock).not.toHaveBeenCalled();
    stop();
  });

  it('re-locks when fullscreen is entered after capture was taken', () => {
    const stop = followCaptureWithEscapeLock();
    captureEditor('passthrough');
    keyboard.lock.mockClear();

    doc.fullscreenElement = {};
    const onChange = doc.addEventListener.mock.calls.find(([type]) => type === 'fullscreenchange');
    (onChange![1] as () => void)();
    expect(keyboard.lock).toHaveBeenCalledWith(['Escape']);
    stop();
  });
});
