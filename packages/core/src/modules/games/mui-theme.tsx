import { type ReactNode, useMemo } from 'react';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import ScopedCssBaseline from '@mui/material/ScopedCssBaseline';

import { readThemeTokens, useThemeId } from '../../theme';

/**
 * MUI theming for the games module. The rest of the app is styled with plain CSS
 * driven by `:root` custom properties (see packages/ui/src/styles.css); this maps
 * tokens onto an MUI dark theme so MUI cards/buttons/chips sit natively in the
 * surrounding look. We use `ScopedCssBaseline` (NOT the global `CssBaseline`) so
 * the reset only applies inside the games subtree and never clobbers the app's own
 * global styles.
 *
 * Wrap any games panel that uses MUI in <GamesMui>…</GamesMui>. Emotion injects its
 * styles once globally, so nesting these providers across panels is cheap.
 */

/**
 * The tokens this theme needs, read off the live document rather than duplicated
 * as literals.
 *
 * MUI's theme is a plain JS object and cannot read a CSS custom property, so this
 * used to be a hardcoded copy of `:root` that went stale whenever the palette
 * moved. With themes selectable at runtime a copy is not merely stale-prone but
 * wrong by construction, so the values are resolved from `<html>` and the theme is
 * rebuilt whenever the active theme changes.
 *
 * `radius` is parsed rather than passed through because MUI's `shape.borderRadius`
 * is a number of pixels, not a CSS length — handing it `"16px"` yields
 * `border-radius: 16pxpx`.
 */
const TOKEN_NAMES = [
  'bg',
  'bg-raised',
  'bg-hover',
  'border',
  'text',
  'text-dim',
  'accent',
  'radius-lg',
] as const;

function buildGamesTheme() {
  const t = readThemeTokens(TOKEN_NAMES);
  return createTheme({
    palette: {
      mode: 'dark',
      primary: { main: t.accent },
      background: { default: t.bg, paper: t['bg-raised'] },
      text: { primary: t.text, secondary: t['text-dim'] },
      divider: t.border,
      action: { hover: t['bg-hover'] },
    },
    // Follows the theme: midnight leans on hairlines and wants small corners,
    // studio holds cards apart with radius and elevation instead.
    shape: { borderRadius: Number.parseFloat(t['radius-lg']) || 8 },
    typography: {
      // Inherit the app's font stack rather than MUI's Roboto default.
      fontFamily: 'inherit',
      fontSize: 13,
    },
    components: {
      MuiCard: {
        defaultProps: { variant: 'outlined' },
        styleOverrides: {
          root: { backgroundImage: 'none', borderColor: t.border },
        },
      },
      MuiButton: {
        defaultProps: { size: 'small' },
        styleOverrides: { root: { textTransform: 'none', fontWeight: 700 } },
      },
      MuiChip: { defaultProps: { size: 'small' } },
    },
  });
}

export function GamesMui({ children }: { children: ReactNode }) {
  // Keyed on the theme id: `initTheme` writes `data-theme` before any component's
  // settings subscriber runs, so by the time this recomputes the document already
  // carries the incoming theme's values.
  const themeId = useThemeId();
  const theme = useMemo(() => buildGamesTheme(), [themeId]);
  return (
    <ThemeProvider theme={theme}>
      <ScopedCssBaseline sx={{ bgcolor: 'transparent' }}>{children}</ScopedCssBaseline>
    </ThemeProvider>
  );
}
