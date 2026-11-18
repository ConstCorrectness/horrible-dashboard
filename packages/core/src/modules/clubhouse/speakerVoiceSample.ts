/**
 * Record one room speaker's voice, for cloning in Unsloth.
 *
 * Agora delivers every speaker as their **own** remote track, so this taps only the
 * chosen person's audio: no crosstalk, no room echo, no agent voice mixed in. That is
 * what makes a room recording usable as a cloning reference at all.
 *
 * A room is mostly other people talking, so a wall-clock recording of one speaker is
 * mostly their silence. `VoiceSampleCollector` keeps only their speech (plus a short
 * pre-roll and tail so words are not clipped) and counts *speech* time toward the
 * target, which is the number a cloning model actually cares about.
 *
 * The collector is pure and tested without audio; `startSpeakerRecording` is the
 * thin impure half that wires it to the shared AudioContext.
 */
import type { ClubhouseRoomSession } from './roomSession';
import { Downsampler, encodeWav, startPcmCapture, type PcmCapture } from './liveCapture';

/** Upload rate. Above Whisper's 16 kHz because the clone keeps timbre, not just words. */
export const CLONE_SAMPLE_RATE = 24000;

export interface CollectorOptions {
  /** Frame RMS at or above this is speech. Remote tracks arrive noise-suppressed. */
  threshold?: number;
  /** Silence kept after speech, so a word's tail is not cut. */
  hangoverMs?: number;
  /** Silence kept before speech resumes, so its first syllable is not cut. */
  prerollMs?: number;
  /** Speech time after which the sample is complete. */
  targetSpeechMs?: number;
}

const FRAME_MS = 20;

export class VoiceSampleCollector {
  readonly threshold: number;
  readonly targetSpeechMs: number;
  /** RMS of the latest frame, for the meter. */
  level = 0;
  /** Frames judged to be speech, in ms. */
  speechMs = 0;

  private readonly frameLen: number;
  private readonly hangoverFrames: number;
  private readonly prerollFrames: number;
  /** Partial frame carried between pushes. */
  private carry: Float32Array = new Float32Array(0);
  private kept: Float32Array[] = [];
  private keptLen = 0;
  /** Silence frames held back in case speech resumes (the pre-roll). */
  private pending: Float32Array[] = [];
  /** Consecutive silent frames since the last speech; Infinity before any speech. */
  private silentRun = Number.POSITIVE_INFINITY;

  constructor(
    private readonly rate = CLONE_SAMPLE_RATE,
    opts: CollectorOptions = {},
  ) {
    this.threshold = opts.threshold ?? 0.012;
    this.targetSpeechMs = opts.targetSpeechMs ?? 15000;
    this.frameLen = Math.round((rate * FRAME_MS) / 1000);
    this.hangoverFrames = Math.round((opts.hangoverMs ?? 300) / FRAME_MS);
    this.prerollFrames = Math.round((opts.prerollMs ?? 160) / FRAME_MS);
  }

  get done(): boolean {
    return this.speechMs >= this.targetSpeechMs;
  }

  /** Length of what `audio()` would return, in ms. */
  get keptMs(): number {
    return (this.keptLen / this.rate) * 1000;
  }

  push(samples: Float32Array): void {
    let buf = samples;
    if (this.carry.length) {
      buf = new Float32Array(this.carry.length + samples.length);
      buf.set(this.carry);
      buf.set(samples, this.carry.length);
    }
    let i = 0;
    for (; i + this.frameLen <= buf.length; i += this.frameLen) {
      this.frame(buf.slice(i, i + this.frameLen));
    }
    this.carry = buf.slice(i);
  }

  private frame(f: Float32Array): void {
    let sum = 0;
    for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
    const rms = Math.sqrt(sum / f.length);
    this.level = rms;
    if (rms >= this.threshold) {
      this.speechMs += FRAME_MS;
      for (const p of this.pending) this.keep(p);
      this.pending = [];
      this.keep(f);
      this.silentRun = 0;
      return;
    }
    this.silentRun++;
    if (this.silentRun <= this.hangoverFrames) {
      this.keep(f);
    } else {
      this.pending.push(f);
      if (this.pending.length > this.prerollFrames) this.pending.shift();
    }
  }

  private keep(f: Float32Array): void {
    this.kept.push(f);
    this.keptLen += f.length;
  }

  /** The speech so far, peak-normalised to about -1 dBFS (gain capped at 8x). */
  audio(): Float32Array {
    const out = new Float32Array(this.keptLen);
    let offset = 0;
    let peak = 0;
    for (const f of this.kept) {
      out.set(f, offset);
      offset += f.length;
      for (let i = 0; i < f.length; i++) peak = Math.max(peak, Math.abs(f[i]));
    }
    if (peak > 0) {
      const gain = Math.min(0.89 / peak, 8);
      for (let i = 0; i < out.length; i++) out[i] *= gain;
    }
    return out;
  }
}

/** The 16 kHz WAV `/api/agent/stt` reads directly, from a clip at `rate`. */
export function sttWav(audio: Float32Array, rate = CLONE_SAMPLE_RATE): ArrayBuffer {
  return encodeWav(new Downsampler(rate, 16000).process(audio), 16000);
}

export function cloneWav(audio: Float32Array): ArrayBuffer {
  return encodeWav(audio, CLONE_SAMPLE_RATE);
}

/**
 * The speaker's live audio track, or null when they are not publishing (an audience
 * member, a muted speaker who never unmuted, or someone who already left). Agora's
 * uid is the Clubhouse user id; compared as strings because it can arrive as either.
 */
export function speakerAudioTrack(
  session: ClubhouseRoomSession,
  userId: number | string,
): MediaStreamTrack | null {
  const user = session.rtcClient?.remoteUsers.find((u) => String(u.uid) === String(userId));
  const track = user?.audioTrack?.getMediaStreamTrack();
  return track && track.readyState === 'live' ? track : null;
}

export interface SpeakerRecording {
  readonly collector: VoiceSampleCollector;
  /** Stop capturing. Safe to call twice. Never stops the track: the room still plays it. */
  stop(): void;
}

/**
 * Start collecting `userId`'s speech. `onUpdate` fires per captured block (~40 ms);
 * `onEnded` once if the track ends underneath us (they left, or we left the room).
 */
export async function startSpeakerRecording(
  session: ClubhouseRoomSession,
  userId: number | string,
  onUpdate: (c: VoiceSampleCollector) => void,
  onEnded: () => void,
): Promise<SpeakerRecording> {
  const ctx = session.audioCtx;
  const track = speakerAudioTrack(session, userId);
  if (!ctx) throw new Error('Join the room first: there is no room audio to record.');
  if (!track) throw new Error('This person is not sending audio right now.');

  const collector = new VoiceSampleCollector();
  const downsampler = new Downsampler(ctx.sampleRate, CLONE_SAMPLE_RATE);
  const source = ctx.createMediaStreamSource(new MediaStream([track]));
  let capture: PcmCapture | null = null;
  let stopped = false;
  const handleEnded = () => {
    if (stopped) return;
    stop();
    onEnded();
  };
  const stop = () => {
    if (stopped) return;
    stopped = true;
    capture?.stop();
    track.removeEventListener('ended', handleEnded);
    try {
      source.disconnect();
    } catch {
      /* already disconnected */
    }
  };
  track.addEventListener('ended', handleEnded);
  try {
    capture = await startPcmCapture(ctx, source, (frames) => {
      if (stopped) return;
      collector.push(downsampler.process(frames));
      onUpdate(collector);
    });
  } catch (err) {
    stop();
    throw err;
  }
  // Stopped while the worklet loaded (closed the dialog, left the room).
  if (stopped) capture.stop();
  return { collector, stop };
}
