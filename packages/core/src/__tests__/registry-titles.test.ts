/**
 * Pane titles must be distinguishable.
 *
 * The sibling of `layout/__tests__/workspace-names.test.ts`, one level down and
 * for the same reason: a title is the only thing separating one pane from
 * another in the `Open: <title>` palette rows, in a tab that has no `title`
 * param, and in `show`'s exact-title pass — which resolves a duplicate by
 * registration order, so the agent opens the wrong pane and reports success.
 * `notebook.editor` and `training.notebook` were both called "Notebook" and
 * nothing anywhere said so.
 *
 * A warning, not a rejection: unlike a workspace name (suffixed at creation by a
 * caller that can wait), a manifest is registered at boot by a plugin that must
 * still load. Views are synthetic — importing a real manifest that reaches the
 * editor opens a WebSocket at import time and dies without jsdom.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type ModuleManifest, registry } from '../registry';

const Stub = () => null;

function manifest(id: string, views: Array<{ id: string; title: string }>): ModuleManifest {
  return {
    id,
    title: id,
    panels: views.map((v) => ({ ...v, component: Stub, role: 'document' as const })),
  };
}

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  registry.resetForTests();
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  warn.mockRestore();
  registry.resetForTests();
});

describe('duplicate pane titles', () => {
  it('warns when a second module claims a title already taken', () => {
    registry.register(manifest('titles-a', [{ id: 'a.doc', title: 'Notebook' }]));
    expect(warn).not.toHaveBeenCalled();

    registry.register(manifest('titles-b', [{ id: 'b.doc', title: 'Notebook' }]));
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain('b.doc');
    expect(String(warn.mock.calls[0][0])).toContain('a.doc');
  });

  it('treats case as the same title — they render identically', () => {
    registry.register(manifest('titles-a', [{ id: 'a.doc', title: 'Notebook' }]));
    registry.register(manifest('titles-b', [{ id: 'b.doc', title: 'notebook' }]));
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('catches a collision inside one manifest too', () => {
    registry.register(
      manifest('titles-a', [
        { id: 'a.one', title: 'Notebook' },
        { id: 'a.two', title: 'Notebook' },
      ]),
    );
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('says nothing when every title is distinct', () => {
    registry.register(manifest('titles-a', [{ id: 'a.doc', title: 'Notebook Editor' }]));
    registry.register(manifest('titles-b', [{ id: 'b.doc', title: 'Training Notebook' }]));
    expect(warn).not.toHaveBeenCalled();
  });

  it('does not warn when a module is re-registered (StrictMode)', () => {
    const m = manifest('titles-a', [{ id: 'a.doc', title: 'Notebook' }]);
    registry.register(m);
    registry.register(m);
    expect(warn).not.toHaveBeenCalled();
  });
});
