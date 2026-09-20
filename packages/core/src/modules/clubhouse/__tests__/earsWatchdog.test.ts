import { describe, expect, it } from 'vitest';

import { earsActions } from '../earsWatchdog';

describe('the ears watchdog', () => {
  it('does nothing while everything is alive', () => {
    expect(earsActions({ contextState: 'running', recorderState: 'recording' })).toEqual({
      resumeContext: false,
      restartRecorder: false,
    });
  });

  it('rebuilds a recorder that errored away', () => {
    // The silent death: `flushChunk` returns early on an inactive recorder and
    // nothing else ever calls `startRecordingChunk`, so the VAD loop ticks forever
    // over a recorder that will never produce another chunk.
    expect(earsActions({ contextState: 'running', recorderState: null }).restartRecorder).toBe(
      true,
    );
    expect(
      earsActions({ contextState: 'running', recorderState: 'inactive' }).restartRecorder,
    ).toBe(true);
  });

  it('resumes a context the OS suspended', () => {
    expect(earsActions({ contextState: 'suspended', recorderState: 'recording' })).toEqual({
      resumeContext: true,
      restartRecorder: false,
    });
    // Safari's, during a phone call. Reads as silence exactly like `suspended`.
    expect(earsActions({ contextState: 'interrupted', recorderState: 'recording' })).toEqual({
      resumeContext: true,
      restartRecorder: false,
    });
  });

  it('treats the two failures as independent, because a device change causes both', () => {
    expect(earsActions({ contextState: 'suspended', recorderState: null })).toEqual({
      resumeContext: true,
      restartRecorder: true,
    });
  });

  it('leaves a paused recorder alone', () => {
    // Nothing here pauses one, so a paused recorder is somebody else's state and
    // stopping it would race whatever set it.
    expect(
      earsActions({ contextState: 'running', recorderState: 'paused' }).restartRecorder,
    ).toBe(false);
  });

  it('does nothing before the room has audio at all', () => {
    expect(earsActions({ contextState: null, recorderState: null })).toEqual({
      resumeContext: false,
      restartRecorder: false,
    });
  });
});
