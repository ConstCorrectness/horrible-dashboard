import { type ReactNode, useMemo } from 'react';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import ScopedCssBaseline from '@mui/material/ScopedCssBaseline';

/**
 * MUI theming for the games module. The rest of the app is styled with plain CSS
 * driven by `:root` custom properties (see packages/ui/src/styles.css); this maps
 * those same tokens onto an MUI dark theme so MUI cards/buttons/chips sit natively
 * in the app's look. We use `ScopedCssBaseline` (NOT the global `CssBaseline`) so
 * the reset only applies inside the games subtree and never clobbers the app's own
 * global styles.
 *
 * Wrap any games panel that uses MUI in <GamesMui>…</GamesMui>. Emotion injects its
 * styles once globally, so nesting these providers across panels is cheap.
 */

// Kept in sync with packages/ui/src/styles.css :root tokens.
const TOKENS = {
  bg: '#14161a',
  bgRaised: '#1d2026',
  bgHover: '#262a32',
  border: '#2e333d',
  text: '#d7dae0',
  textDim: '#8a909c',
  accent: '#6ea8fe',
};

const gamesTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: TOKENS.accent },
    background: { default: TOKENS.bg, paper: TOKENS.bgRaised },
    text: { primary: TOKENS.text, secondary: TOKENS.textDim },
    divider: TOKENS.border,
    action: { hover: TOKENS.bgHover },
  },
  shape: { borderRadius: 10 },
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
