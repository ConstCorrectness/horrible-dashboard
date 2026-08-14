/**
 * The settings page is a centre tab, not a dock panel.
 *
 * Both halves of that are silent when broken: a `role` change alone leaves every
 * *already saved* workspace with the pane still pinned in the right dock, and the
 * page renders fine there — just squeezed into ~20rem, which is what made it a
 * document pane in the first place.
 */
import { beforeAll, describe, expect, it } from 'vitest';

import { isDockable } from '../layout/controller';
import { createDock, createEmptyFrame } from '../layout/model';
import { deserialize, serialize } from '../layout/serialize';
import type { FrameState } from '../layout/types';
import { settingsModule } from '../modules/settings';
import { registry } from '../registry';

describe('settings pane placement', () => {
  beforeAll(() => {
    registry.register(settingsModule);
  });

  it('opens as a document pane', () => {
    const decl = registry.panels.find((p) => p.id === 'settings.home');
    expect(decl?.role).toBe('document');
    expect(decl?.singleton).toBe(true);
  });

  it('is not dockable, so no opener can put it back in a rail', () => {
    expect(isDockable('settings.home')).toBe(false);
  });

  it('evicts a dock entry saved while it was still a tool pane', () => {
    // A workspace persisted by the previous build. `role` is a default for new
    // opens and does nothing to this, so without the load-time filter the pane
    // stays in the right dock forever.
    const frame: FrameState = createEmptyFrame();
    frame.docks.right = {
      ...createDock('right'),
      visible: true,
      tools: [{ instanceId: 'settings.home#1', viewId: 'settings.home' }],
      activeTool: 'settings.home#1',
    };

    const known = new Set([...registry.panels, ...registry.widgets].map((v) => v.id));
    const blob = serialize(frame);

    // Restored as-is when nothing says the view is undockable...
    expect(deserialize(blob, known)!.docks.right.tools.map((t) => t.viewId)).toContain(
      'settings.home',
    );

    // ...and dropped once it is, which is what `undockableViews()` reports now
    // that `isDockable('settings.home')` is false.
    const migrated = deserialize(blob, known, new Set(['settings.home']))!;
    expect(migrated.docks.right.tools).toHaveLength(0);
    expect(migrated.docks.right.activeTool).toBeNull();
  });
});
