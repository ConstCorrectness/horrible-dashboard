/**
 * The built-in desktop backdrops.
 *
 * Registered as an ordinary module contribution (`ModuleManifest.backdrops`), so
 * a plugin's backdrop and a built-in one reach the desktop by exactly the same
 * path and appear in the same picker.
 *
 * Nothing here bundles third-party imagery: `none`/`aurora`/`grid` are drawn,
 * `pulse`/`board`/`splash` render the node's own state, and `image` shows what
 * the user supplied. Same rule as the maps and the search engines.
 */
import { DEFAULT_BACKDROP, type BackdropDecl } from '@horrible/core';

import { AuroraBackdrop } from './Aurora';
import { BoardBackdrop } from './Board';
import { GridBackdrop } from './Grid';
import { ImageBackdrop } from './Image';
import { PulseBackdrop } from './Pulse';
import { SplashBackdrop } from './Splash';

/**
 * The fallback for a desktop whose stored backdrop id is not registered.
 *
 * Re-exported from the model rather than declared here: the deserializer and the
 * reducer name the same default without being able to see this list, and two
 * constants that must agree are one that eventually will not.
 */
export const DEFAULT_BACKDROP_ID = DEFAULT_BACKDROP;

export const BUILTIN_BACKDROPS: BackdropDecl[] = [
  {
    id: 'none',
    title: 'None',
    description: 'A flat surface in the theme’s background colour.',
    component: () => null,
  },
  {
    id: 'aurora',
    title: 'Aurora',
    description: 'Slow procedural gradients built from the current theme.',
    component: AuroraBackdrop,
  },
  {
    id: 'grid',
    title: 'Grid',
    description: 'A drifting grid with a scanline sweep. Pairs with the HUD theme.',
    component: GridBackdrop,
  },
  {
    id: 'image',
    title: 'Wallpaper',
    description: 'An image you supplied.',
    component: ImageBackdrop,
  },
  {
    id: 'splash',
    title: 'Home',
    description: 'The avatar, ask bar and connectors, with windows over the top.',
    component: SplashBackdrop,
    interactive: true,
  },
  {
    id: 'pulse',
    title: 'Pulse',
    description: 'Live traffic through this node, one ripple per request.',
    component: PulseBackdrop,
  },
  {
    id: 'board',
    title: 'Board',
    description: 'Widgets laid straight onto the desktop.',
    component: BoardBackdrop,
    interactive: true,
  },
];
