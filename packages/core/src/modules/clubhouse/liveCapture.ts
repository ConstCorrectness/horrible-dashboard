/**
 * The agent's ears as raw PCM, for transcription that keeps up with the room.
 *
 * The old ears were a `MediaRecorder` producing WebM, and that shape could not be
 * live. A WebM blob is only decodable from its first byte, so the only way to get a
 * transcript mid-sentence was to *stop* the recorder, which cut the audio at an
 * arbitrary point and lost what fell between one recorder and the next. Nothing
 * was transcribed until the speaker paused for 750 ms — in a busy room, often never
 * — and an utterance past 30 s was silently truncated by Whisper.
 *
 * Capturing PCM instead means the utterance so far is always one array: the pane
 * can send a snapshot of it every second for a live caption, then send the whole
 * thing once the speaker stops, without ever interrupting the capture. It arrives at
 * the server as 16 kHz WAV, which the STT service reads without ffmpeg.
 *
 * Everything here but `startPcmCapture` is pure, so it is tested without audio.
 */

/** What Whisper consumes. Resampling here keeps uploads a third of 48 kHz's size. */
export const STT_SAMPLE_RATE = 16000;

/**
 * Converts a stream at the context's rate to 16 kHz, one block at a time.
 *
 * Each output sample is the mean of the input samples it spans — a crude low-pass,
 * but enough for speech, and it carries its position across blocks so a 44.1 kHz
 * context (a non-integer ratio) does not drift.
 */
export class Downsampler {
  private readonly ratio: number;
  /** Input samples consumed so far, and where the current output sample ends. */
  private consumed = 0;
  private boundary: number;
  private acc = 0;
  private count = 0;
  private last = 0;

  constructor(inputRate: number, outputRate = STT_SAMPLE_RATE) {
    this.ratio = inputRate / outputRate;
    this.boundary = this.ratio;
  }

  process(input: Float32Array): Float32Array {
    const out = new Float32Array(Math.ceil(input.length / this.ratio) + 1);
    let n = 0;
    for (let i = 0; i < input.length; i++) {
      this.acc += input[i];
      this.count++;
      this.consumed++;
      // `while`, so an input rate *below* 16 kHz repeats samples instead of
      // producing audio at the wrong speed.
      while (this.consumed >= this.boundary) {
        if (this.count > 0) {
          this.last = this.acc / this.count;
          this.acc = 0;
          this.count = 0;
        }
        out[n++] = this.last;
        this.boundary += this.ratio;
      }
    }
    return out.subarray(0, n);
  }
}

/**
 * The current utterance, plus a short pre-roll of what came just before it.
 *
 * The VAD decides a run of speech has started a tick *after* it started, so without
 * the pre-roll the first syllable of every sentence was clipped.
 */
export class UtteranceBuffer {
  /** Incremented on every `begin`, so a late reply can tell it is stale. */
  id = 0;
  active = false;
  /** Whether the VAD heard speech in this utterance (vs. only its silent tail). */
  hasSpeech = false;
  private chunks: Float32Array[] = [];
  private length = 0;
  private readonly prerollSamples: number;

  constructor(
    private readonly rate = STT_SAMPLE_RATE,
    prerollMs = 400,
  ) {
    this.prerollSamples = Math.round((rate * prerollMs) / 1000);
  }

  push(samples: Float32Array): void {
    if (samples.length === 0) return;
    this.chunks.push(samples);
    this.length += samples.length;
    if (!this.active) {
      // Idle: keep only the most recent pre-roll's worth.
      while (this.chunks.length > 1 && this.length - this.chunks[0].length >= this.prerollSamples) {
        this.length -= this.chunks.shift()!.length;
      }
    }
  }

  /** Start an utterance; the pre-roll becomes its beginning. */
  begin(): void {
    this.id++;
    this.active = true;
    this.hasSpeech = false;
  }

  get durationMs(): number {
    return (this.length / this.rate) * 1000;
  }

  /** Everything buffered so far, as one array. Does not end the utterance. */
  snapshot(): Float32Array {
    const out = new Float32Array(this.length);
    let offset = 0;
    for (const chunk of this.chunks) {
      out.set(chunk, offset);
      offset += chunk.length;
    }
    return out;
  }

  /** End the utterance and hand back its audio. The buffer goes back to pre-roll. */
  end(): Float32Array {
    const audio = this.snapshot();
    this.active = false;
    this.hasSpeech = false;
    this.chunks = [];
    this.length = 0;
    return audio;
  }
}

/** 16-bit mono PCM WAV — the one format the STT service reads without ffmpeg. */
export function encodeWav(samples: Float32Array, rate = STT_SAMPLE_RATE): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

/**
 * Batches the render thread's 128-frame quanta into ~40 ms posts: one message per
 * quantum is 375 a second, which is pure overhead on the main thread.
 */
const WORKLET_SOURCE = `
class SttTap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) {
          this.port.postMessage(this.buf);
          this.buf = new Float32Array(2048);
          this.n = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('clubhouse-stt-tap', SttTap);
`;

/** Contexts that already have the processor registered; `addModule` is per context. */
const workletReady = new WeakMap<BaseAudioContext, Promise<void>>();

export interface PcmCapture {
  /** Stop delivering frames and disconnect. Safe to call twice. */
  stop(): void;
}

/**
 * Tap `input` and deliver its samples (at the context's rate) to `onFrames`.
 *
 * An AudioWorklet where there is one; a `ScriptProcessorNode` where the worklet
 * module will not load. The fallback is deprecated and runs on the main thread, but
 * it exists everywhere — and ears that fail to start are a deaf agent.
 */
export async function startPcmCapture(
  ctx: AudioContext,
  input: AudioNode,
  onFrames: (frames: Float32Array) => void,
): Promise<PcmCapture> {
  try {
    let ready = workletReady.get(ctx);
    if (!ready) {
      const url = URL.createObjectURL(
        new Blob([WORKLET_SOURCE], { type: 'application/javascript' }),
      );
      ready = ctx.audioWorklet.addModule(url).finally(() => URL.revokeObjectURL(url));
      workletReady.set(ctx, ready);
      // A failed load must not be cached, or the fallback becomes permanent.
      ready.catch(() => workletReady.delete(ctx));
    }
    await ready;
    // No outputs: a node without outputs is a sink, so the graph renders it
    // without a connection to the speakers.
    const node = new AudioWorkletNode(ctx, 'clubhouse-stt-tap', {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCount: 1,
      channelCountMode: 'explicit',
    });
    node.port.onmessage = (e: MessageEvent<Float32Array>) => onFrames(e.data);
    input.connect(node);
    let stopped = false;
    return {
      stop() {
        if (stopped) return;
        stopped = true;
        node.port.onmessage = null;
        try {
          input.disconnect(node);
        } catch {
          /* already disconnected */
        }
      },
    };
  } catch (err) {
    console.warn('AudioWorklet unavailable for STT capture; using ScriptProcessor:', err);
  }

  const processor = ctx.createScriptProcessor(4096, 1, 1);
  // A ScriptProcessor only runs while connected onward, so it feeds a muted gain.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  processor.onaudioprocess = (e) => onFrames(new Float32Array(e.inputBuffer.getChannelData(0)));
  input.connect(processor);
  processor.connect(sink);
  sink.connect(ctx.destination);
  let stopped = false;
  return {
    stop() {
      if (stopped) return;
      stopped = true;
      processor.onaudioprocess = null;
      for (const node of [processor, sink]) {
        try {
          node.disconnect();
        } catch {
          /* already disconnected */
        }
      }
      try {
        input.disconnect(processor);
      } catch {
        /* already disconnected */
      }
    },
  };
}
