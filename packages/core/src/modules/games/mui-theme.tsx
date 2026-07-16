import { type ReactNode, useMemo } from 'react';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import ScopedCssBaseline from '@mui/material/ScopedCssBaseline';

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
 * The games palette, as literals.
 *
 * MUI's theme is a JS object built once at module scope — it cannot read the CSS
 * custom properties that `.games-theme` inherits down the tree, so these values have
 * to be duplicated here. They mirror the `.games-theme` block in games.css (the warm
 * arcade palette), NOT the app's cool `:root`: every consumer of `GamesMui`
 * (PlaySection, ProfilePanel, PlazaPanel, ChallengeCards) lives inside the games
 * pane, so the games palette is the correct source. **Change one, change the other.**
 */
const TOKENS = {
  bg: '#0d0a08',
  bgRaised: '#14110d',
  bgHover: '#1e1913',
  border: 'rgba(250, 250, 250, 0.12)',
  text: '#fafafa',
  textDim: 'rgba(250, 250, 250, 0.55)',
  accent: '#f97316',
};

const gamesTheme = createTheme({
  palette: {
    mode: 'dark',
    // contrastText is pinned rather than left to MUI's luminance calculation: orange
    // sits near its light/dark threshold, and near-black on orange is the intent.
    primary: { main: TOKENS.accent, contrastText: TOKENS.bg },
    background: { default: TOKENS.bg, paper: TOKENS.bgRaised },
    text: { primary: TOKENS.text, secondary: TOKENS.textDim },
    divider: TOKENS.border,
    action: { hover: TOKENS.bgHover },
  },
  // Small radii — the look leans on hairlines, not soft corners.
  shape: { borderRadius: 8 },
  typography: {
    // Inherit the app's font stack rather than MUI's Roboto default.
    fontFamily: 'inherit',
    fontSize: 13,
  },
  components: {
    MuiCard: {
      defaultProps: { variant: 'outlined' },
      styleOverrides: {
        root: { backgroundImage: 'none', borderColor: TOKENS.border },
      },
    },
    MuiButton: {
      defaultProps: { size: 'small' },
      styleOverrides: { root: { textTransform: 'none', fontWeight: 700 } },
    },
    MuiChip: { defaultProps: { size: 'small' } },
  },
});

export function GamesMui({ children }: { children: ReactNode }) {
  // createTheme is cheap but memoize to keep referential identity stable.
  const theme = useMemo(() => gamesTheme, []);
  return (
    <ThemeProvider theme={theme}>
      <ScopedCssBaseline sx={{ bgcolor: 'transparent' }}>{children}</ScopedCssBaseline>
    </ThemeProvider>
  );
}
