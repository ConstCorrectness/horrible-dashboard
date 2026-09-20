/**
 * What the ears watchdog should do about the state it just observed.
 *
 * Split out as a pure function because the watchdog itself lives inside the join
 * closure in `useClubhouseVoice`, where a test cannot reach it without a real
 * `AudioContext` and a real `MediaRecorder` — neither of which jsdom has. The rule
 * is the part worth pinning; the plumbing around it is three field reads.
 */

/** `interrupted` is Safari's state during a phone call; the DOM lib omits it. */
export type ObservedContextState = AudioContextState | 'interrupted';

export interface EarsProbe {
  /** `null` when the room has no audio context — nothing to watch yet. */
  contextState: ObservedContextState | null;
  /** `null` when the recorder errored away or was never built. */
  recorderState: RecordingState | null;
}

export interface EarsActions {
  resumeContext: boolean;
  restartRecorder: boolean;
}

/**
 * A suspended context and a dead recorder are **independent** failures and both are
 * silent: the VAD loop goes on reading an analyser full of zeros, the pane goes on
 * saying "listening", and the agent is deaf until the room is rejoined.
 *
 * They are also not exclusive — a device change can suspend the context *and* end
 * the track under the recorder — so this returns both flags rather than a verdict.
 * Resuming without rebuilding leaves a recorder that never fires again; rebuilding
 * without resuming feeds it silence.
 */
export function earsActions(probe: EarsProbe): EarsActions {
  if (probe.contextState === null) {
    // No room audio yet. Not a fault, and rebuilding here would race the join.
    return { resumeContext: false, restartRecorder: false };
  }
  return {
    resumeContext: probe.contextState !== 'running',
    // `paused` is deliberately left alone: nothing in this module pauses a recorder,
    // so one that is paused is a state we did not create and stopping it would race
    // whatever did.
    restartRecorder: probe.recorderState === null || probe.recorderState === 'inactive',
  };
}
