// @vitest-environment happy-dom
/**
 * Tool names are the agent's whole vocabulary and nothing validates them at
 * runtime: `executeDynamicTool` resolves by `find`, so a name declared twice
 * resolves to whichever module `main.tsx` registered first and the other is
 * uncallable — no error, no log, just a tool the agent can never reach.
 *
 * That shipped. The reactive notebook and the training notebook both declared
 * `notebook.read_cell`, `run_cell`, `insert_cell`, `edit_cell`, `delete_cell`,
 * `run_all` and `kernel_status` against different stores with different session
 * arguments (`path` vs `projectId`); training registers first, so the reactive
 * notebook's seven were the dead ones. Only `list_cells` had been noticed, and
 * renaming it `nb.list_cells` fixed the symptom while dropping the tool into a
 * one-tool `nb` group no keyword could preload.
 */
import { describe, expect, it } from 'vitest';

import { notebookAgentTools } from '../../notebook/agentTools';
import { notebookAgentTools as trainingAgentTools } from '../../training/agentTools';
import { duplicateToolNames } from '../manifest';

/** The group a tool lands in: its namespace before the first dot, mirroring the
 *  backend's `_group_of`. A name with no dot is a layout verb there. */
function groupOf(name: string): string {
  return name.includes('.') ? name.split('.', 1)[0] : 'layout';
}

describe('notebook vs training tool names', () => {
  it('declares no name twice', () => {
    const all = [...notebookAgentTools, ...trainingAgentTools];
    expect([...duplicateToolNames(all).keys()]).toEqual([]);
  });

  it('puts every tool in its owning module’s group', () => {
    // The prefix *is* the group — there is no `group` field to override it — so a
    // tool prefixed with anything but its module's id is filed under a group whose
    // description, guide and preload keywords describe something else.
    expect([...new Set(notebookAgentTools.map((t) => groupOf(t.name)))]).toEqual(['notebook']);
    expect([...new Set(trainingAgentTools.map((t) => groupOf(t.name)))]).toEqual(['training']);
  });

  it('keeps the reactive notebook’s cell verbs reachable under one prefix', () => {
    // `nb.list_cells` was the tell: one tool stranded in its own group while its
    // siblings sat in `notebook`. Listing cells and then running one has to be the
    // same group or the second call needs a whole extra load_tools round.
    const names = notebookAgentTools.map((t) => t.name);
    expect(names).toContain('notebook.list_cells');
    expect(names).toContain('notebook.run_cell');
    expect(names.filter((n) => n.startsWith('nb.'))).toEqual([]);
  });

  it('addresses its own session argument in each set', () => {
    // The names diverged; the *arguments* were already different. This is what made
    // the collision more than cosmetic — a call shaped for one module was invalid
    // for the one that actually received it.
    const paramNames = (t: { params?: { properties?: Record<string, unknown> } }) =>
      Object.keys(t.params?.properties ?? {});
    expect(paramNames(notebookAgentTools[0])).toContain('path');
    expect(paramNames(trainingAgentTools[0])).toContain('projectId');
  });
});

describe('duplicateToolNames', () => {
  it('reports each repeated name with its count and ignores unique ones', () => {
    const dupes = duplicateToolNames([
      { name: 'a.one' },
      { name: 'a.one' },
      { name: 'a.one' },
      { name: 'b.two' },
    ]);
    expect([...dupes]).toEqual([['a.one', 3]]);
  });
});
