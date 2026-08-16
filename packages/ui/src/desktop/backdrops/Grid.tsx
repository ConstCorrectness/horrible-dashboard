/**
 * A slow perspective grid with a drifting scanline — the companion to the `hud`
 * theme, and the reason `useCanvasBackdrop` exists. Nothing is sampled or
 * bundled; it is a few dozen lines of arithmetic.
 */
import { useCanvasBackdrop } from './canvas';

const TOKENS = ['bg', 'accent', 'border'] as const;

export function GridBackdrop({ params }: { params?: Record<string, unknown> }) {
  const spacing = numberParam(params?.spacing, 48, 12, 240);
  const speed = numberParam(params?.speed, 1, 0, 8);

  const ref = useCanvasBackdrop(TOKENS, ({ ctx, width, height, t, tokens }) => {
    ctx.fillStyle = tokens.bg || '#000';
    ctx.fillRect(0, 0, width, height);

    // The whole grid drifts by one cell and then repeats, so the motion is
    // continuous with no seam and needs no wrap bookkeeping.
    const offset = (t * speed * 12) % spacing;
    ctx.lineWidth = 1;
    ctx.strokeStyle = tokens.border || 'rgba(255,255,255,0.08)';
    ctx.beginPath();
    for (let x = -spacing + offset; x <= width + spacing; x += spacing) {
      // The half-pixel keeps a 1px line on the pixel grid instead of straddling
      // two rows at half opacity each, which is what makes a canvas grid look
      // blurry next to a CSS one.
      const px = Math.round(x) + 0.5;
      ctx.moveTo(px, 0);
      ctx.lineTo(px, height);
    }
    for (let y = -spacing + offset; y <= height + spacing; y += spacing) {
      const py = Math.round(y) + 0.5;
      ctx.moveTo(0, py);
      ctx.lineTo(width, py);
    }
    ctx.stroke();

    // The scanline: a soft accent band sweeping top to bottom.
    const accent = tokens.accent || '#4ea1ff';
    const band = height * 0.18;
    const y = ((t * speed * 40) % (height + band)) - band;
    const gradient = ctx.createLinearGradient(0, y, 0, y + band);
    gradient.addColorStop(0, 'transparent');
    gradient.addColorStop(0.5, accent);
    gradient.addColorStop(1, 'transparent');
    ctx.globalAlpha = 0.1;
    ctx.fillStyle = gradient;
    ctx.fillRect(0, y, width, band);
    ctx.globalAlpha = 1;
  });

  return <canvas ref={ref} className="os-backdrop-canvas" aria-hidden="true" />;
}

/** A param the user (or the agent) supplied, clamped into a sane range. */
function numberParam(value: unknown, fallback: number, min: number, max: number): number {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}
