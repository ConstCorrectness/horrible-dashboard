/**
 * Turning a thrown script error into the message the pane shows. Kept pure (the
 * probe is passed in) so it is testable without a DOM.
 *
 * The old handler matched any message containing "context" or "webgl" and replaced
 * it with "WebGL is disabled in this environment", discarding the real error. That
 * was a guess, not a measurement: a canvas that already holds a 2D context refuses
 * a WebGL one on a machine with a perfectly good GPU, and an unrelated script bug
 * that merely mentions `context` read as a hardware problem. So the environment is
 * only blamed when a fresh canvas genuinely cannot create a WebGL context, and the
 * underlying error is always shown.
 */
import type { VisualizerMode } from './bridge';

const WEBGL_HINT = /webgl|context/i;

/**
 * Whether this environment can create a WebGL context at all, tested on a
 * throwaway canvas (never the pane's — asking it for a context would claim it).
 * The probe context is released immediately so it does not count against the
 * browser's per-page context limit.
 */
export function probeWebGL(): boolean {
  if (typeof document === 'undefined') return false;
  const probe = document.createElement('canvas');
  const gl = probe.getContext('webgl2') ?? probe.getContext('webgl');
  if (!gl) return false;
  gl.getExtension('WEBGL_lose_context')?.loseContext();
  return true;
}

export function describeRunError(
  err: unknown,
  mode: VisualizerMode,
  probe: () => boolean = probeWebGL,
): string {
  const detail = err instanceof Error ? err.message : String(err);
  const isWebGLMode = mode === 'three' || mode === 'babylon';
  if (!isWebGLMode || !WEBGL_HINT.test(detail)) {
    return `Script error: ${detail}`;
  }
  if (!probe()) {
    return (
      `WebGL is unavailable here: a fresh canvas could not create a WebGL context ` +
      `(hardware acceleration may be disabled, or this is a headless/VM browser). ` +
      `Switch to 'canvas' or 'pygame' mode, or enable hardware acceleration. ` +
      `Underlying error: ${detail}`
    );
  }
  return (
    `WebGL works in this environment, but the script failed: ${detail}. ` +
    `If it mentions a context, check that the script (or the linked editor buffer — ` +
    `Source defaults to the active one) doesn't call canvas.getContext('2d'): a canvas ` +
    `keeps its first context type, so a WebGL renderer cannot attach after it.`
  );
}
