/**
 * Record a screen, window or tab into a WebM for the clip editor: the share
 * module's capture (`startCapture`, which already knows which shells can capture
 * and says why when they cannot) feeding a MediaRecorder.
 *
 * The browser's own "Stop sharing" bar ends the recording too — a recorder that
 * kept claiming to record a capture the person had already stopped would hand
 * back a file with a frozen tail.
 */
import { onCaptureEnded, startCapture, stopCapture } from '../../share/capture';

const TYPES = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'];

function pickType(): string {
  if (typeof MediaRecorder === 'undefined') {
    throw new Error('This shell cannot record video (no MediaRecorder).');
  }
  return TYPES.find((t) => MediaRecorder.isTypeSupported(t)) ?? '';
}

export interface Recording {
  /** Resolves with the recording once it stops, by `stop()` or the browser's bar. */
  done: Promise<Blob>;
  stop: () => void;
  startedAt: number;
}

/** Start recording. Must be called from a click (every engine requires a gesture). */
export async function recordScreen(): Promise<Recording> {
  const type = pickType();
  const stream = await startCapture();
  const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };
  const done = new Promise<Blob>((resolve, reject) => {
    recorder.onstop = () => {
      stopCapture(stream);
      if (!chunks.length) reject(new Error('The recording is empty.'));
      else resolve(new Blob(chunks, { type: 'video/webm' }));
    };
    recorder.onerror = () => {
      stopCapture(stream);
      reject(new Error('The recording failed.'));
    };
  });
  const stop = () => {
    if (recorder.state !== 'inactive') recorder.stop();
  };
  const unhook = onCaptureEnded(stream, stop);
  void done.finally(unhook).catch(() => {});
  // A chunk a second, so a crash mid-recording loses a second rather than all of it.
  recorder.start(1000);
  return { done, stop, startedAt: Date.now() };
}

/** `screen-20261002-171530.webm` */
export function recordingName(prefix = 'screen', at = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${prefix}-${at.getFullYear()}${p(at.getMonth() + 1)}${p(at.getDate())}-${p(
    at.getHours(),
  )}${p(at.getMinutes())}${p(at.getSeconds())}.webm`;
}
