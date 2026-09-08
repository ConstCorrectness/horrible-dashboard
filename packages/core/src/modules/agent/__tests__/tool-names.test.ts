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
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

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

  it('puts every tool in exactly one group, and one the backend describes', () => {
    // The prefix *is* the group — there is no `group` field to override it — so a
    // tool prefixed with anything the backend has no blurb for is filed under a
    // group whose description, guide and preload keywords describe something else,
    // or nothing at all.
    expect([...new Set(notebookAgentTools.map((t) => groupOf(t.name)))]).toEqual(['notebook']);

    // The training module's cell verbs are `cells.*`, NOT `training.*`, and that is
    // deliberate rather than a stray prefix. A group is the unit `load_tools` hands
    // out: with these ten in `training` beside its fourteen project verbs and the
    // recipe module's ten, that group was 34 tools, and the trainer agent's own
    // documented loop no longer fitted in `TOOL_BUDGET`. Editing cells and managing
    // a project are different jobs and load separately now.
    //
    // `cells` rather than `notebook`, because these two modules collided under that
    // name once already — see `permission_store._RULE_RENAMES`, which carries both
    // old spellings so a saved grant survives.
    expect([...new Set(trainingAgentTools.map((t) => groupOf(t.name)))]).toEqual(['cells']);
  });

  it('names only groups the backend can describe', () => {
    /*
     * The group a frontend tool lands in is decided here and *described* there.
     * `list_tool_groups` is how a model chooses what to load, and a group with no
     * blurb is offered as a bare id — so renaming a prefix without adding one
     * silently makes a whole capability unpickable, with nothing failing.
     *
     * Read across the package boundary for the same reason `front-door.test.ts`
     * reads `packages/ui`: the two halves of this contract live in different
     * languages and only one of them can be imported.
     */
    // `process.cwd()` (the package root under vitest), not `import.meta.url`: this
    // file runs in `happy-dom`, where that is not a file URL and `fileURLToPath`
    // throws.
    const orchestrator = readFileSync(
      join(process.cwd(), '..', '..', 'backend', 'modules', 'agent', 'orchestrator.py'),
      'utf8',
    );
    const block = orchestrator.slice(orchestrator.indexOf('_GROUP_DESCRIPTIONS'));
    const described = new Set([...block.matchAll(/^ {4}"([a-z_]+)":/gm)].map((m) => m[1]));

    // A guard on the guard: a changed dict shape must not make this vacuous.
    expect(described.size).toBeGreaterThan(5);

    const prefixes = [...notebookAgentTools, ...trainingAgentTools].map((t) => groupOf(t.name));
    expect([...new Set(prefixes)].filter((g) => !described.has(g))).toEqual([]);
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
