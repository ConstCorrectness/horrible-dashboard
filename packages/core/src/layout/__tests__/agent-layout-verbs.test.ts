/**
 * The agent's arrangement verbs — `place_pane`, `pane_state`, `arrange_panes` —
 * driven through `executeTool`, the one entry point the agent and the REPL share.
 * Also the floating desktop's names, kept as aliases: a transcript or REPL script
 * that says `window_state snap left` must still do the nearest workbench thing.
 *
 * Views are registered synthetically (see controller.test.ts for why).
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { installFrameController, openPane } from '../controller';
import { findPaneAnywhere } from '../model';
import { layoutStore } from '../store';
import { collectAreas } from '../dock/tree';
import type { SplitNode } from '../dock/types';
import { current, groupOf, groupsFrame, loadFrame } from './fixtures';

const Stub = () => null;
let exec: (name: string, args: Record<string, unknown>) => Promise<unknown>;

beforeAll(async () => {
  registry.register({
    id: 'agent-verbs-test',
    title: 'Agent verbs test',
    panels: [
      { id: 'v.doc', title: 'Doc', component: Stub, role: 'document' },
      { id: 'v.tool', title: 'Tool', component: Stub, role: 'tool', singleton: true },
    ],
  });
  installFrameController();
  exec = (await import('../../modules/agent/tool-exec')).executeTool;
});

let a: string;
let b: string;
let c: string;

beforeEach(() => {
  layoutStore.resetForTests();
  loadFrame(groupsFrame([]), 'test');
  a = openPane('v.doc')!;
  b = openPane('v.doc')!;
  c = openPane('v.doc')!;
});

const where = (id: string) => findPaneAnywhere(current(), id)!.location;

describe('place_pane', () => {
  it('splits a pane out of its group to a side', async () => {
    const res = (await exec('place_pane', { instanceId: b, to: 'split-right' })) as {
      moved: boolean;
    };
    expect(res.moved).toBe(true);
    expect(where(b).areaId).not.toBe(where(a).areaId);
    expect((current().center as SplitNode).orientation).toBe('row');
  });

  it('docks a document and brings it back to the centre', async () => {
    await exec('place_pane', { instanceId: c, to: 'dock-bottom' });
    expect(where(c)).toMatchObject({ kind: 'dock', side: 'bottom' });
    expect(current().docks.bottom.visible).toBe(true);
    await exec('place_pane', { instanceId: c, to: 'center' });
    expect(where(c).kind).toBe('center');
  });

  it('joins a named group', async () => {
    await exec('place_pane', { instanceId: b, to: 'split-below' });
    const target = where(b).areaId;
    await exec('place_pane', { instanceId: c, to: 'group', group: target });
    expect(groupOf(c)!.id).toBe(target);
  });

  it('refuses words and ids it does not know, saying what it takes', async () => {
    expect(await exec('place_pane', { instanceId: a, to: 'left' })).toMatchObject({
      error: expect.stringContaining('dock-left'),
    });
    expect(await exec('place_pane', { instanceId: a, to: 'group', group: 'zz' })).toHaveProperty(
      'error',
    );
    expect(await exec('place_pane', { instanceId: 'nope#1', to: 'center' })).toHaveProperty(
      'error',
    );
  });
});

describe('pane_state', () => {
  it('maximizes and restores the pane group', async () => {
    await exec('place_pane', { instanceId: b, to: 'split-right' });
    await exec('pane_state', { instanceId: b, state: 'maximize' });
    expect(current().maximizedAreaId).toBe(where(b).areaId);
    // Maximize is idempotent: asking twice must not toggle it back off.
    await exec('pane_state', { instanceId: b, state: 'maximize' });
    expect(current().maximizedAreaId).toBe(where(b).areaId);
    await exec('pane_state', { instanceId: b, state: 'restore' });
    expect(current().maximizedAreaId).toBeNull();
  });

  it('hides a dock but never a centre document', async () => {
    const tool = openPane('v.tool')!;
    await exec('pane_state', { instanceId: tool, state: 'hide' });
    expect(current().docks.right.visible).toBe(false);
    expect(await exec('pane_state', { instanceId: a, state: 'hide' })).toMatchObject({ ok: false });
    await exec('pane_state', { instanceId: tool, state: 'show' });
    expect(current().docks.right.visible).toBe(true);
  });

  it('presents and unpresents', async () => {
    await exec('pane_state', { instanceId: a, state: 'present' });
    expect(current().presentedInstanceId).toBe(a);
    await exec('pane_state', { instanceId: a, state: 'unpresent' });
    expect(current().presentedInstanceId).toBeNull();
  });
});

describe('arrange_panes', () => {
  it('lays the centre out as columns, then back into tabs', async () => {
    await exec('arrange_panes', { style: 'columns' });
    expect(collectAreas(current().center)).toHaveLength(3);
    await exec('arrange_panes', { style: 'tabs' });
    expect(collectAreas(current().center)).toHaveLength(1);
  });

  it('names the styles it takes', async () => {
    expect(await exec('arrange_panes', { style: 'cascade' })).toMatchObject({
      error: expect.stringContaining('columns'),
    });
  });
});

describe('floating-desktop aliases', () => {
  it('window_state snap left is a split to the left', async () => {
    await exec('window_state', { instanceId: c, state: 'snap', snap: 'left' });
    const root = current().center as SplitNode;
    expect(root.orientation).toBe('row');
    expect(collectAreas(root)[0].tabs.map((t) => t.instanceId)).toEqual([c]);
  });

  it('open_window gives a pane a group of its own', async () => {
    await exec('open_window', { instanceId: a });
    expect(groupOf(a)!.tabs).toHaveLength(1);
  });

  it('arrange_windows cascade gathers into tabs', async () => {
    await exec('arrange_panes', { style: 'columns' });
    await exec('arrange_windows', { style: 'cascade' });
    expect(collectAreas(current().center)).toHaveLength(1);
  });

  it('window_state minimize on a docked tool hides its dock', async () => {
    const tool = openPane('v.tool')!;
    await exec('window_state', { instanceId: tool, state: 'minimize' });
    expect(current().docks.right.visible).toBe(false);
  });
});
