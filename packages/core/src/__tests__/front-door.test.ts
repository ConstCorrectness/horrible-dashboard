/**
 * The front-door guard.
 *
 * `HomeView` — the avatar, the ask bar, the connector tiles and the first-run
 * setup card — is the tier-1 entry surface, the one PRODUCT.md says must work for
 * someone who never opens a terminal. It is rendered by exactly one thing in the
 * whole app: the `splash` desktop backdrop.
 *
 * For a long time `DEFAULT_BACKDROP` was `aurora`, a purely decorative gradient.
 * The consequence was invisible in every code review and total in effect: a clean
 * install opened on a wallpaper and a taskbar, and the entire front door was
 * unreachable unless the user happened to pick "Home" out of a list of seven
 * wallpapers on a wizard step titled "Pick a look".
 *
 * This is the reachability invariant the panes already have (`RENAMED_VIEWS`,
 * the pane-consolidation work) applied to a *surface*: the default backdrop must
 * be one that can actually be worked in. Nothing else in the codebase would fail
 * if it changed back — which is precisely why it needs a test rather than a
 * comment.
 *
 * It reads `packages/ui`'s source because the backdrop registry lives there while
 * the constant lives here, and `packages/ui` has no test runner of its own. The
 * design-token guard already scans across the package boundary for the same
 * reason.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { DEFAULT_BACKDROP } from '../layout/types';

const HERE = fileURLToPath(new URL('.', import.meta.url));
const REPO = join(HERE, '..', '..', '..', '..');
const BACKDROPS = join(REPO, 'packages', 'ui', 'src', 'desktop', 'backdrops', 'index.ts');
const SPLASH = join(REPO, 'packages', 'ui', 'src', 'desktop', 'backdrops', 'Splash.tsx');

/** Ids declared with `interactive: true` — the backdrops that render the node's
 *  own state and can be worked in, rather than decoration behind windows. */
function interactiveBackdropIds(source: string): string[] {
  const ids: string[] = [];
  // Each entry runs `id: '...'` … `interactive: true` before the next `id:`.
  for (const block of source.split(/\bid:\s*'/).slice(1)) {
    const id = block.slice(0, block.indexOf("'"));
    const untilNext = block.split(/\bid:\s*'/)[0];
    if (/interactive:\s*true/.test(untilNext)) ids.push(id);
  }
  return ids;
}

describe('the front door is reachable from a clean install', () => {
  const source = readFileSync(BACKDROPS, 'utf8');

  it('has at least one interactive backdrop to be the front door', () => {
    expect(interactiveBackdropIds(source).length).toBeGreaterThan(0);
  });

  it('defaults to a backdrop the user can actually do something in', () => {
    expect(
      interactiveBackdropIds(source),
      `DEFAULT_BACKDROP is "${DEFAULT_BACKDROP}", which is decoration. A clean install ` +
        'would open on an empty wallpaper with no ask bar, no connector tiles and no ' +
        'setup card — the entire tier-1 surface unreachable. Point it at an ' +
        'interactive backdrop.',
    ).toContain(DEFAULT_BACKDROP);
  });

  it('still routes that default through the backdrop that mounts HomeView', () => {
    // Guards the other half: `splash` could stay the default while quietly
    // ceasing to render the home surface, which would pass the check above and
    // fail the user identically.
    expect(readFileSync(SPLASH, 'utf8')).toMatch(/<HomeView\b/);
    expect(source).toMatch(new RegExp(`id:\\s*'${DEFAULT_BACKDROP}'`));
  });
});
