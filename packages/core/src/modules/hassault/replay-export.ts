/**
 * A demo, recorded to an .mp4 as it plays.
 *
 * The browser's own MP4 muxer, through `MediaRecorder` with an `video/mp4`
 * type, rather than WebCodecs and a muxer of ours: Chromium has written real
 * MP4 (H.264 in an ISO BMFF container) from MediaRecorder for a while, and
 * WebCodecs is missing outside a secure context, which a pane served over a
 * custom scheme or plain http can be. A shell with no MP4 recorder is told so —
 * a WebM renamed .mp4 is not offered as one.
 *
 * Real time, at the bar's speed: the frame loop plays the demo as it would to
 * watch it and hands each drawn frame here, so 0.5x gives a slow-motion video
 * twice the demo's length. The recorder is paused whenever the demo is (or the
 * pane is hidden, where the loop stops drawing) so a pause is not a frozen
 * stretch in the file.
 *
 * Frames are composited onto a 2D canvas of our own first: the scope is DOM over
 * the WebGL canvas (`ScopeOverlay`), so recording the canvas alone would turn
 * every scoped kill back into a hip-fire one. `drawScope` is that overlay in
 * canvas strokes. Free of React and three.
 */

/** Long side of the video, at most. H.264 at 4K from a pane is a file nobody wants. */
export const EXPORT_MAX_SIDE = 1920;

/** Types tried in order: H.264 High, then whatever H.264 the shell picks, then any MP4. */
const MP4_TYPES = [
  'video/mp4;codecs=avc1.640028',
  'video/mp4;codecs=avc1.4d0028',
  'video/mp4;codecs=avc1',
  'video/mp4',
];

/** The MP4 type this shell can record, or `null` when it can record none. */
export function pickMp4Type(isTypeSupported: (type: string) => boolean): string | null {
  return MP4_TYPES.find((t) => isTypeSupported(t)) ?? null;
}

/**
 * The video's size for a canvas of `width` × `height`: scaled down to fit
 * `maxSide`, and even on both axes, which 4:2:0 H.264 requires.
 */
export function exportSize(
  width: number,
  height: number,
  maxSide = EXPORT_MAX_SIDE,
): { width: number; height: number } {
  const w = Math.max(2, width);
  const h = Math.max(2, height);
  const scale = Math.min(1, maxSide / Math.max(w, h));
  const even = (n: number) => Math.max(2, Math.floor((n * scale) / 2) * 2);
  return { width: even(w), height: even(h) };
}

/** `dust2_replay-20261029-171530.mp4` */
export function replayVideoName(map: string, at = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  const safe = (map || 'demo').replace(/[^\w.-]+/g, '_');
  return `${safe}_replay-${at.getFullYear()}${p(at.getMonth() + 1)}${p(at.getDate())}-${p(
    at.getHours(),
  )}${p(at.getMinutes())}${p(at.getSeconds())}.mp4`;
}

/**
 * The sniper's sight picture on a 2D canvas: `ScopeOverlay`'s gradients and
 * hairlines, sized off the short side exactly as its `min(vh, vw)` is.
 */
export function drawScope(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  zoom: number,
): void {
  const cx = width / 2;
  const cy = height / 2;
  // `min(31vh, 31vw)` and friends: percentages of the short side.
  const unit = Math.min(width, height) / 100;
  const surround = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.hypot(cx, cy));
  const far = Math.hypot(cx, cy);
  const stop = (r: number) => Math.min(1, (r * unit) / far);
  surround.addColorStop(0, 'rgba(0,0,0,0)');
  surround.addColorStop(stop(31), 'rgba(0,0,0,0)');
  surround.addColorStop(stop(33), 'rgba(0,0,0,0.55)');
  surround.addColorStop(stop(36), 'rgba(0,0,0,0.97)');
  surround.addColorStop(1, 'rgba(0,0,0,0.97)');
  ctx.fillStyle = surround;
  ctx.fillRect(0, 0, width, height);

  // The tube's rim.
  ctx.strokeStyle = 'rgba(0,0,0,0.85)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, cy, 33 * unit, 0, Math.PI * 2);
  ctx.stroke();

  // Hairlines with the centre gap, 44%-56% of each axis, at half opacity.
  const line = 'rgba(220,255,220,0.425)';
  ctx.fillStyle = line;
  ctx.fillRect(Math.floor(cx), 0, 1, height * 0.44);
  ctx.fillRect(Math.floor(cx), height * 0.56, 1, height * 0.44);
  ctx.fillRect(0, Math.floor(cy), width * 0.44, 1);
  ctx.fillRect(width * 0.56, Math.floor(cy), width * 0.44, 1);

  // The dot, where the shot goes.
  ctx.fillStyle = 'rgba(220,255,220,0.85)';
  ctx.beginPath();
  ctx.arc(cx, cy, 1.5, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = 'rgba(220,255,220,0.65)';
  ctx.font = `${Math.max(10, Math.round(unit * 1.4))}px monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillText(`${zoom}×`, cx, cy + 23 * unit);
}

/** Why an export could not start or finish, in words for the player. */
export class ExportError extends Error {}

/**
 * One export: a compositor canvas, its stream and the recorder on it.
 *
 * `frame` after every render the demo should contain; `setActive(false)` while
 * the demo is paused; `stop` for the file, `cancel` to throw it away.
 */
export class ReplayVideoRecorder {
  readonly width: number;
  readonly height: number;
  readonly mimeType: string;
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly stream: MediaStream;
  private readonly track: CanvasCaptureMediaStreamTrack;
  private readonly recorder: MediaRecorder;
  private readonly chunks: Blob[] = [];
  private readonly done: Promise<Blob>;
  private cancelled = false;

  constructor(sourceWidth: number, sourceHeight: number, bitrate = 12_000_000) {
    if (typeof MediaRecorder === 'undefined') {
      throw new ExportError('This shell cannot record video (no MediaRecorder).');
    }
    const type = pickMp4Type((t) => MediaRecorder.isTypeSupported(t));
    if (!type) {
      throw new ExportError(
        'This shell cannot record MP4 video. A Chromium-based browser or the desktop app can.',
      );
    }
    this.mimeType = type;
    const size = exportSize(sourceWidth, sourceHeight);
    this.width = size.width;
    this.height = size.height;
    this.canvas = document.createElement('canvas');
    this.canvas.width = this.width;
    this.canvas.height = this.height;
    const ctx = this.canvas.getContext('2d', { alpha: false });
    if (!ctx) throw new ExportError('Could not create the canvas the video is drawn on.');
    this.ctx = ctx;
    // Fill before the first frame: a stream starts with whatever the canvas
    // holds, and a transparent canvas encodes as a green flash in some players.
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, this.width, this.height);
    // 0: a frame only when `requestFrame` says, so the video has exactly the
    // frames the loop drew and none of the canvas between them.
    this.stream = this.canvas.captureStream(0);
    this.track = this.stream.getVideoTracks()[0] as CanvasCaptureMediaStreamTrack;
    this.recorder = new MediaRecorder(this.stream, {
      mimeType: type,
      videoBitsPerSecond: bitrate,
    });
    this.recorder.ondataavailable = (e) => {
      if (e.data.size) this.chunks.push(e.data);
    };
    this.done = new Promise<Blob>((resolve, reject) => {
      this.recorder.onstop = () => {
        this.stream.getTracks().forEach((t) => t.stop());
        if (this.cancelled) reject(new ExportError('Export cancelled.'));
        else if (!this.chunks.length) reject(new ExportError('The video is empty.'));
        else resolve(new Blob(this.chunks, { type: 'video/mp4' }));
      };
      this.recorder.onerror = () => {
        this.stream.getTracks().forEach((t) => t.stop());
        reject(new ExportError('The video recorder failed.'));
      };
    });
    // Swallowed here, surfaced through `stop`: a cancel nobody awaits is not an
    // unhandled rejection.
    this.done.catch(() => {});
    this.recorder.start(1000);
  }

  /**
   * One rendered frame. Call straight after the render, in the same task: the
   * WebGL drawing buffer is cleared once the browser composites it.
   */
  frame(source: CanvasImageSource, scopeZoom: number): void {
    if (this.recorder.state !== 'recording') return;
    const { ctx, width, height } = this;
    ctx.drawImage(source, 0, 0, width, height);
    if (scopeZoom > 1) drawScope(ctx, width, height, scopeZoom);
    this.track.requestFrame();
  }

  /** Pause or resume the file's clock, idempotently. */
  setActive(active: boolean): void {
    if (active && this.recorder.state === 'paused') this.recorder.resume();
    else if (!active && this.recorder.state === 'recording') this.recorder.pause();
  }

  /** Finish and hand back the .mp4. */
  stop(): Promise<Blob> {
    if (this.recorder.state !== 'inactive') this.recorder.stop();
    return this.done;
  }

  cancel(): void {
    this.cancelled = true;
    if (this.recorder.state !== 'inactive') this.recorder.stop();
  }
}

/** Hand a finished video to the browser's download, as the .hademo save does. */
export function saveVideo(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  // Not straight away: a revoked URL can cancel a download still being written.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Whether `bytes` start like an ISO BMFF file: an `ftyp` box first. */
export function looksLikeMp4(bytes: Uint8Array): boolean {
  return (
    bytes.length >= 12 && String.fromCharCode(bytes[4], bytes[5], bytes[6], bytes[7]) === 'ftyp'
  );
}
