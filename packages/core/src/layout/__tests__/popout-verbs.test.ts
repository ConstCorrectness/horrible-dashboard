/**
 * Pop-out verbs: who may pop out, where a docked-back pane lands, and what a
 * build without pop-outs does with a layout that has some.
 *
 * Views are registered synthetically (see controller.test.ts for why).
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { initCapabilities } from '../../capabilities';
import { registry } from '../../registry';
import {
  canPopOut,
  dockPopoutBack,
  installFrameController,
  openPane,
  popOutPane,
} from '../controller';
import { findPaneAnywhere } from '../model';
import { layoutStore } from '../store';
import { current, groupsFrame, loadFrame } from './fixtures';

const Stub = () => null;

beforeAll(() => {
  registry.register({
    id: 'popout-verbs-test',
    title: 'Pop-out verbs test',
    panels: [
      { id: 'po.doc', title: 'Doc', component: Stub, role: 'document', popout: true },
      { id: 'po.plain', title: 'Plain', component: Stub, role: 'document' },
      {
        id: 'po.tool',
        title: 'Tool',
        component: Stub,
        role: 'tool',
        dock: 'bottom',
        singleton: true,
        popout: true,
      },
      // No `dock`: by role it would land on the right, so a dock-back that lands
      // anywhere else must have come from where the pane was.
      { id: 'po.loaded', title: 'Loaded', component: Stub, role: 'tool', popout: true },
      { id: 'po.targeted', title: 'Targeted', component: Stub, role: 'tool', popout: true },
    ],
  });
  installFrameController();
});

beforeEach(() => {
  initCapabilities(['window.multi']);
  layoutStore.resetForTests();
  loadFrame(groupsFrame([]), 'test');
});

afterEach(() => initCapabilities([]));

const where = (id: string) => findPaneAnywhere(current(), id)?.location;
const flush = () => new Promise((r) => setTimeout(r, 0));

describe('canPopOut', () => {
  it('needs the shell to open windows and the view to opt in', () => {
    expect(canPopOut('po.doc')).toBe(true);
    expect(canPopOut('po.plain')).toBe(false);
    initCapabilities([]);
    expect(canPopOut('po.doc')).toBe(false);
  });
});

describe('popOutPane / dockPopoutBack', () => {
  it('moves a pane into its own pop-out', () => {
    const a = openPane('po.doc')!;
    openPane('po.doc');
    expect(popOutPane(a)).toBe(true);
    expect(where(a)?.kind).toBe('popout');
    expect(current().popouts).toHaveLength(1);
  });

  it('refuses a view that has not opted in', () => {
    const p = openPane('po.plain')!;
    expect(popOutPane(p)).toBe(false);
    expect(where(p)?.kind).toBe('center');
  });

  it('docks a document back into the centre', () => {
    const a = openPane('po.doc')!;
    popOutPane(a);
    const id = current().popouts[0].id;
    expect(dockPopoutBack(id)).toBe(true);
    expect(where(a)?.kind).toBe('center');
    expect(current().popouts).toHaveLength(0);
  });

  it('docks a tool back into its own dock', () => {
    const t = openPane('po.tool')!;
    expect(where(t)).toMatchObject({ kind: 'dock', side: 'bottom' });
    popOutPane(t);
    dockPopoutBack(current().popouts[0].id);
    expect(where(t)).toMatchObject({ kind: 'dock', side: 'bottom' });
  });

  // A load is not a move, so the recorder never saw this pane placed: a saved or
  // v3-migrated layout. It must still dock back to the dock it was popped out of.
  it('docks a pane back where it was loaded, not where its role would put it', () => {
    const t = loadFrame(groupsFrame([], { bottom: 'po.loaded' }), 'test').docks.bottom.area.tabs[0]
      .instanceId;
    expect(where(t)).toMatchObject({ kind: 'dock', side: 'bottom' });
    popOutPane(t);
    dockPopoutBack(current().popouts[0].id);
    expect(where(t)).toMatchObject({ kind: 'dock', side: 'bottom' });
  });

  it('docks a pane back where an explicit target opened it', () => {
    const t = openPane('po.targeted', { target: { kind: 'dock', side: 'left' } })!;
    expect(where(t)).toMatchObject({ kind: 'dock', side: 'left' });
    popOutPane(t);
    dockPopoutBack(current().popouts[0].id);
    expect(where(t)).toMatchObject({ kind: 'dock', side: 'left' });
  });

  it('is a no-op for a pop-out that is already gone', () => {
    expect(dockPopoutBack('p999')).toBe(false);
  });
});

describe('a build without pop-outs', () => {
  it('docks back the pop-outs a desktop layout left behind', async () => {
    const a = openPane('po.doc')!;
    popOutPane(a);
    expect(where(a)?.kind).toBe('popout');
    const saved = current();
    initCapabilities([]);
    // The same layout, loaded where there are no pop-outs.
    loadFrame(saved, 'elsewhere');
    await flush();
    expect(where(a)?.kind).toBe('center');
    expect(current().popouts).toHaveLength(0);
  });
});
