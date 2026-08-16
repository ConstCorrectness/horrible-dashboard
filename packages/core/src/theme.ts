/**
 * Theme selection.
 *
 * The themes themselves are pure CSS — one block of tokens each in
 * packages/ui/src/themes.css, selected by `data-theme` on `<html>`. This module is
 * only the *switch*: it owns the list of theme ids, applies one to the document,
 * and keeps it in step with the `settings.theme` setting.
 *
 * Switching is deliberately one attribute write rather than a React context: every
 * styled surface in the app already reads its colours from custom properties, so
 * the browser restyles the whole tree itself and no component re-renders. The one
 * exception is a consumer that reads token values into JavaScript (MUI, canvases,
 * anything drawing to a 2D context) — those cannot see a CSS variable and must
 * subscribe via `useThemeId` and re-read through `readThemeTokens`.
 *
 * See docs/architecture/theming.mdx.
 */
import { getSetting, settingsStore, useSetting } from './settings';

export interface ThemeDecl {
  /** Matches a `:root[data-theme='<id>']` block in packages/ui/src/themes.css. */
  id: string;
  title: string;
  description: string;
}

/**
 * Every selectable theme. Adding one here without the matching CSS block leaves the
 * app unstyled-ish rather than erroring, so add the CSS first.
 */
export const THEMES: ThemeDecl[] = [
  {
    id: 'midnight',
    title: 'Midnight',
    description: 'The original look. Near-black, hairline borders, small radii, flat surfaces.',
  },
  {
    id: 'studio',
    title: 'Studio',
    description:
      'Softer borders, larger radii and real elevation, with editorial serif display type.',
  },
  {
    id: 'glass',
    title: 'Glass',
    description:
      'Translucent, blurred chrome so the desktop backdrop shows through. Window controls on the left.',
  },
  {
    id: 'hud',
    title: 'HUD',
    description:
      'Terminal cyberpunk: near-black, square corners, mono chrome, and a glow instead of a shadow.',
  },
  {
    id: 'daylight',
    title: 'Daylight',
    description: 'A light, opaque productivity theme with visible elevation.',
  },
  {
    id: 'retro',
    title: 'Retro',
    description: 'Beveled grey 90s chrome, square corners, and a teal desktop.',
  },
];

export const THEME_SETTING_KEY = 'settings.theme';

/** Also the value `:root` carries in themes.css, so the two cannot disagree. */
export const DEFAULT_THEME = 'midnight';

export function isKnownTheme(id: string): boolean {
  return THEMES.some((t) => t.id === id);
}

/** The effective theme id: the user's setting when it names a theme we still ship. */
export function currentThemeId(): string {
  const id = getSetting<string>(THEME_SETTING_KEY);
  return id && isKnownTheme(id) ? id : DEFAULT_THEME;
}

/**
 * Write the theme onto the document.
 *
 * An unknown id falls back to the default rather than being written through: a
 * theme can be removed (or a layout restored from a machine that had a plugin
 * theme installed), and `data-theme` naming a block that doesn't exist would leave
 * the app on `:root`'s values while Settings claimed otherwise.
 */
export function applyTheme(id: string): void {
  document.documentElement.dataset.theme = isKnownTheme(id) ? id : DEFAULT_THEME;
}

/**
 * Apply the persisted theme and keep it applied. Call once at boot **after**
 * `loadSettings` (so the first paint is already themed and there is no flash) and
 * before the first render.
 *
 * Registering the subscription here, at boot, is what guarantees the document
 * attribute is updated *before* any component's own settings subscriber runs:
 * listeners fire in insertion order, so a JS-side token reader like `GamesMui`
 * — which subscribes on mount, long after this — never reads the outgoing theme's
 * values on the render that follows a switch.
 */
export function initTheme(): () => void {
  applyTheme(currentThemeId());
  return settingsStore.subscribe(() => applyTheme(currentThemeId()));
}

/** Reactive theme id, for the JS-side consumers that have to rebuild on a switch. */
export function useThemeId(): string {
  const id = useSetting<string>(THEME_SETTING_KEY);
  return id && isKnownTheme(id) ? id : DEFAULT_THEME;
}

/**
 * Read theme tokens out of the live document, for consumers that cannot use CSS
 * variables. Returns the resolved value of each requested custom property with the
 * leading `--` omitted from the keys.
 *
 * Resolved, not declared: an alias like `--surface` is defined as `var(--bg-raised)`
 * and `getComputedStyle` collapses that to the actual colour, which is the only
 * form a canvas or a MUI palette can use.
 */
export function readThemeTokens<K extends string>(names: readonly K[]): Record<K, string> {
  const styles = getComputedStyle(document.documentElement);
  const out = {} as Record<K, string>;
  for (const name of names) out[name] = styles.getPropertyValue(`--${name}`).trim();
  return out;
}
