/**
 * The attention flash: what makes minimizing something that keeps working useful.
 * A pane put away mid-job (a research run, a training job, an ingest) needs a way
 * to say it is done, or "minimize it and get on with something else" means polling
 * it by hand.
 */
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { activateTaskbarEntry, requestPaneAttention, toggleDock } from '../controller';
import { layoutStore } from '../store';
import { taskbarEntries } from '../taskbar';
import { current, groupsFrame, loadFrame } from './fixtures';

const Stub = () => null;

beforeAll(() => {
  registry.register({
    id: 'attention-test',
    title: 'Attention test',
    panels: [
      { id: 'ft.a', title: 'Alpha', component: Stub, role: 'document', icon: 'A' },
      { id: 'ft.b', title: 'Beta', component: Stub, role: 'document', icon: 'B' },
    ],
  });
});

beforeEach(() => {
  layoutStore.resetForTests();
  // `ft.a` docked at the bottom, `ft.b` in the centre and focused.
  loadFrame(groupsFrame(['ft.b'], { bottom: 'ft.a' }), 'ft');
  activateTaskbarEntry('ft.b#4');
});

const entryFor = (id: string) => taskbarEntries(current()).find((e) => e.instanceId === id)!;
/** `[ft.a, ft.b]`: the docked one, then the focused one. */
const ids = () => ['ft.a#6', 'ft.b#4'];

describe('attention', () => {
  it('flags a pane that finished while out of sight', () => {
    const [a] = ids();
    toggleDock('bottom', false);
    expect(requestPaneAttention(a)).toBe(true);
    expect(entryFor(a).attention).toBe(true);
  });

  it('is cleared by looking at the pane', () => {
    const [a] = ids();
    toggleDock('bottom', false);
    requestPaneAttention(a);
    activateTaskbarEntry(a);
    expect(entryFor(a).attention).toBe(false);
  });

  it('does not flash a pane the user is already looking at', () => {
    /** A button pulsing for the thing on screen is noise, not a notification. */
    const [, b] = ids();
    expect(entryFor(b).state).toBe('focused');
    expect(requestPaneAttention(b)).toBe(false);
    expect(entryFor(b).attention).toBe(false);
  });

  it('can be raised for a pane that is on screen but not focused', () => {
    const [a, b] = ids();
    expect(requestPaneAttention(a)).toBe(true);
    expect(entryFor(a).attention).toBe(true);
    // …and the focused one is still refused.
    expect(requestPaneAttention(b)).toBe(false);
  });

  it('can be cleared explicitly by the pane that raised it', () => {
    const [a] = ids();
    requestPaneAttention(a);
    expect(requestPaneAttention(a, false)).toBe(true);
    expect(entryFor(a).attention).toBe(false);
  });

  it('is absent rather than false on the pane, so layouts do not carry stale flags', () => {
    const [a] = ids();
    requestPaneAttention(a);
    requestPaneAttention(a, false);
    expect(JSON.stringify(current())).not.toContain('"attention"');
  });
});
