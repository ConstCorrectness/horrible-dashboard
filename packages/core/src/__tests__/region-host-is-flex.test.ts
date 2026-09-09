/**
 * Every container that renders a pane's **region strips** has to be a flex
 * container, and this pins the one that is easy to miss.
 *
 * `PaneWithRegions`'s root is `.frame-pane-regions`, which sizes itself with
 * `flex: 1` and declares **no height of its own**. That is fine inside
 * `.frame-area` and `.frame-dock`, which have always been `display: flex`. It was
 * not fine inside `.os-window-body`, which was not — and the failure is about as
 * quiet as a layout bug gets: with no flex context the `flex: 1` is inert, the
 * div collapses to its content's natural height, and a floating window renders as
 * **a titlebar with nothing under it**. Measured on the hAssault pane: 44px of
 * pane header instead of 648px, with the game canvas at zero.
 *
 * It hid because of where it bit. Floating windows are the *desktop* layout's
 * normal way of showing a pane, while the browser layout puts panes in areas and
 * docks — so the whole app looked fine in a browser and every pane was empty in
 * the desktop build. And it only appeared at all once windows started rendering
 * regions: the bare `PaneHost` they held before got away with a non-flex parent
 * because `.frame-pane-host` sets `height: 100%`.
 *
 * The rule this encodes: **a region host must establish a flex context, because
 * the thing it hosts brings no height with it.**
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..');

/** The declaration block of a top-level rule whose selector is exactly `selector`. */
function ruleBody(css: string, selector: string): string | null {
  // Anchored to a line start so `.frame-pane-regions` does not also match
  // `.frame-pane-regions-middle`, and so a descendant rule is not mistaken for
  // the base one.
  const pattern = new RegExp(
    `(^|\\n)\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
  );
  return pattern.exec(css)?.[2] ?? null;
}

function declares(body: string | null, property: string, value: string): boolean {
  if (!body) return false;
  return new RegExp(`(^|;|\\n)\\s*${property}\\s*:\\s*${value}\\s*(;|$)`).test(body);
}

const desktopCss = readFileSync(join(REPO, 'packages/ui/src/desktop/desktop.css'), 'utf8');
const frameCss = readFileSync(join(REPO, 'packages/ui/src/layout/frame.css'), 'utf8');
const windowTsx = readFileSync(join(REPO, 'packages/ui/src/desktop/Window.tsx'), 'utf8');

describe('a region host establishes a flex context', () => {
  it('still describes the situation this guard is about', () => {
    // A guard on the guard. If windows stop rendering regions, or
    // `.frame-pane-regions` grows a height of its own, the assertion below stops
    // meaning anything and should be revisited rather than left passing.
    expect(windowTsx).toContain('PaneWithRegions');
    const regions = ruleBody(frameCss, '.frame-pane-regions');
    expect(regions).not.toBeNull();
    expect(declares(regions, 'flex', '1')).toBe(true);
    expect(regions).not.toMatch(/(^|;|\n)\s*height\s*:/);
  });

  it('makes .os-window-body a flex container', () => {
    const body = ruleBody(desktopCss, '.os-window-body');
    expect(
      declares(body, 'display', 'flex'),
      '.os-window-body holds PaneWithRegions, whose root sizes itself with `flex: 1` ' +
        'and has no height. Without `display: flex` here that is inert, and every ' +
        'floating window renders as a titlebar with an empty body — which is the ' +
        'entire desktop layout.',
    ).toBe(true);
  });

  it('keeps the contexts that always worked', () => {
    for (const selector of ['.frame-area', '.frame-pane-host']) {
      expect(declares(ruleBody(frameCss, selector), 'display', 'flex'), selector).toBe(true);
    }
  });
});
