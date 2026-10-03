/**
 * A theme's colours as values, for what cannot read CSS variables.
 *
 * Mermaid bakes colours into its SVG (it derives shades from them, so a CSS
 * variable will not do). A published diagram is therefore drawn twice — once in the theme's
 * default palette and once in its other colour scheme — and the stylesheet shows the
 * one that matches the reader's `prefers-color-scheme` (`diagramCss`).
 *
 * A theme's `tokens.css` is a `:root { … }` block (its default palette) and
 * optionally an `@media (prefers-color-scheme: dark|light) { :root { … } }` block
 * that overrides it.
 */

export type Palette = Record<string, string>;

export interface ThemePalettes {
  base: Palette;
  /** The other colour scheme, when the theme has one: its name and full palette. */
  alt: { scheme: 'light' | 'dark'; palette: Palette } | null;
}

function declarations(block: string): Palette {
  const out: Palette = {};
  for (const match of block.matchAll(/--([\w-]+)\s*:\s*([^;}]+)/g)) out[match[1]] = match[2].trim();
  return out;
}

/** The `:root { … }` body at the start of `css` (balanced to the first `}`). */
function rootBlock(css: string): string {
  const match = /:root\s*\{([^}]*)\}/.exec(css);
  return match ? match[1] : '';
}

export function themePalettes(tokens: string): ThemePalettes {
  const media = /@media\s*\(\s*prefers-color-scheme\s*:\s*(dark|light)\s*\)\s*\{/.exec(tokens);
  const head = media ? tokens.slice(0, media.index) : tokens;
  const base = declarations(rootBlock(head));
  if (!media) return { base, alt: null };
  const rest = tokens.slice(media.index + media[0].length);
  return {
    base,
    alt: {
      scheme: media[1] as 'light' | 'dark',
      palette: { ...base, ...declarations(rootBlock(rest)) },
    },
  };
}

/** Mermaid `themeVariables` for a palette (theme `base`). */
export function mermaidVariables(palette: Palette): Record<string, string> {
  const text = palette['text'] ?? 'black';
  return {
    background: 'transparent',
    primaryColor: palette['bg-raised'] ?? palette['bg'] ?? 'white',
    primaryBorderColor: palette['accent'] ?? text,
    primaryTextColor: text,
    secondaryColor: palette['bg-inset'] ?? palette['bg-raised'] ?? 'white',
    tertiaryColor: palette['bg'] ?? 'white',
    lineColor: palette['text-dim'] ?? text,
    textColor: text,
    fontFamily: palette['font-body'] ?? 'sans-serif',
  };
}

/** Show the diagram drawn for the reader's colour scheme. */
export function diagramCss(alt: ThemePalettes['alt']): string {
  const base = '.scrive-mermaid-alt { display: none; }';
  if (!alt) return base;
  return `${base}\n@media (prefers-color-scheme: ${alt.scheme}) {\n  .scrive-mermaid-base { display: none; }\n  .scrive-mermaid-alt { display: block; }\n}`;
}
