/**
 * The flashbang's screen: the white, and the afterimage under it.
 *
 * Driven imperatively, from the render loop and from each snapshot, rather than
 * through React state. The white changes every frame for seconds at a time, and
 * re-rendering the pane at 60 Hz to move one opacity would cost more than the
 * frame it is drawn over.
 *
 * ## The afterimage
 *
 * What makes a Counter-Strike flash feel like a flash rather than a white
 * rectangle is that the last thing you saw stays burned in while the white
 * clears. That frame is copied from the WebGL canvas **in the same animation
 * frame it was drawn in**, right after `renderer.render` — the drawing buffer is
 * not preserved, so a copy taken from a later task would read black. It is why
 * a capture is *requested* by the snapshot and *taken* by `frame`.
 *
 * Copied at half resolution: it is under a white that is clearing, it is meant
 * to be blurry, and a full-size copy on a 4K canvas is a stall at exactly the
 * moment somebody is about to be shot.
 */
import type { GameAudio } from './audio';
import { FlashState, flashFieldsFrom, flashRing } from './flashbang';
import type { SelfState } from './net';

/** The two elements `FlashOverlay` renders. */
export interface FlashLayerHandle {
  white: HTMLElement;
  afterimage: HTMLCanvasElement;
}

/** How strong the burned-in frame is at the start of the fade, under the white. */
export const AFTERIMAGE_STRENGTH = 0.85;

export class FlashLayer {
  readonly state = new FlashState();
  private handle: FlashLayerHandle | null = null;
  private captureWanted = false;
  /** How strong the current afterimage is, from the flash that took it. */
  private afterPeak = 0;

  attach(handle: FlashLayerHandle | null): void {
    this.handle = handle;
    this.apply();
  }

  /**
   * Adopt a snapshot's flash, and act on any flash that has just landed.
   *
   * Also applies the opacity itself, so that with the render loop stalled — a
   * hidden tab, where `requestAnimationFrame` does not fire — the white still
   * follows the server rather than sticking at whatever it last drew.
   */
  seed(you: SelfState, audio?: GameAudio | null): void {
    this.state.seed(flashFieldsFrom(you));
    for (const landed of you.flashed ?? []) {
      this.captureWanted = true;
      this.afterPeak = Math.max(this.afterPeak, Math.min(1, landed.peak));
      const ring = flashRing(landed.peak, landed.hold, landed.fade);
      if (ring) audio?.flashRing(ring.volume, ring.seconds);
    }
    this.apply();
  }

  /** One frame: count down, take the afterimage if one is owed, draw. Call it
   * right after `renderer.render`, with the canvas that was just drawn. */
  frame(dt: number, source: HTMLCanvasElement | null): void {
    this.state.step(dt);
    if (this.captureWanted && source) {
      this.capture(source);
      this.captureWanted = false;
    }
    this.apply();
  }

  /** How strong the afterimage is right now, 0..1. */
  get afterimageOpacity(): number {
    return AFTERIMAGE_STRENGTH * this.afterPeak * this.state.remaining;
  }

  private capture(source: HTMLCanvasElement): void {
    const canvas = this.handle?.afterimage;
    if (!canvas) return;
    const w = Math.max(1, Math.floor(source.width / 2));
    const h = Math.max(1, Math.floor(source.height / 2));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    try {
      canvas.getContext('2d')?.drawImage(source, 0, 0, w, h);
    } catch {
      // A tainted or lost context: the white still works without its ghost.
    }
  }

  private apply(): void {
    const alpha = this.state.alpha;
    if (alpha <= 0 && this.state.remaining <= 0) this.afterPeak = 0;
    if (!this.handle) return;
    this.handle.white.style.opacity = alpha.toFixed(3);
    this.handle.afterimage.style.opacity = this.afterimageOpacity.toFixed(3);
  }
}
