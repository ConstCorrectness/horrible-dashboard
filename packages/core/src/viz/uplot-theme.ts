/**
 * Theme colours for uPlot, resolved to real values.
 *
 * uPlot draws to a **canvas**, and a `CanvasRenderingContext2D` cannot resolve a
 * CSS custom property. Assigning `ctx.strokeStyle = 'var(--accent)'` does not throw
 * — the setter silently ignores any value it cannot parse and the previous colour
 * stands. So `stroke: 'var(--accent, #539bf5)'` in a uPlot config is not a themed
 * colour with a fallback; it is the fallback, always, and the `var()` never did
 * anything. That is exactly how `training/panels/MetricsPane.tsx` came to draw its
 * series in uPlot's default colour on every theme.
 *
 * This resolves the tokens through `getComputedStyle` once per call, so the value
 * that reaches the canvas is the one the theme actually declared. Charts re-read it
 * on a theme change — see `subscribeThemeColors`.
 */

export interface ChartColors {
  accent: string;
  accent2: string;
  axis: string;
  grid: string;
  text: string;
}

const TOKENS: Record<keyof ChartColors, string> = {
  accent: '--accent',
  accent2: '--accent-2',
  axis: '--text-dim',
  grid: '--border',
  text: '--text',
};

/**
 * Read the tokens off the document root.
 *
 * The fallbacks are `currentColor`-ish neutrals rather than a second palette: if a
 * token is missing the honest outcome is a grey chart that looks wrong, not a chart
 * quietly painted in another product's brand colour.
 */
export function chartColors(el?: Element | null): ChartColors {
  const target = el ?? (typeof document !== 'undefined' ? document.documentElement : null);
  if (!target || typeof getComputedStyle !== 'function') {
    return { accent: 'gray', accent2: 'gray', axis: 'gray', grid: 'gray', text: 'gray' };
  }
  const style = getComputedStyle(target);
  const out = {} as ChartColors;
  for (const [key, token] of Object.entries(TOKENS) as [keyof ChartColors, string][]) {
    out[key] = style.getPropertyValue(token).trim() || 'gray';
  }
  return out;
}

/**
 * Call `onChange` whenever the theme changes, so a canvas can repaint.
 *
 * The theme is a `data-theme` attribute on the root element (see `theme.ts`), which
 * a `MutationObserver` can watch directly — no store subscription, so this works for
 * any canvas anywhere without a dependency on who owns the setting.
 */
export function subscribeThemeColors(onChange: (colors: ChartColors) => void): () => void {
  if (typeof MutationObserver !== 'function' || typeof document === 'undefined') return () => {};
  const root = document.documentElement;
  const observer = new MutationObserver(() => onChange(chartColors(root)));
  observer.observe(root, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  return () => observer.disconnect();
}
