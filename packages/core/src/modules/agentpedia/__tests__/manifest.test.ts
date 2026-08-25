/**
 * The agentpedia manifest, and the two rules about it that are silent when broken.
 *
 * A registration smoke test is a module convention (see `.claude/skills/new-module`),
 * but the keybinding assertions are the ones with teeth: an arrow key bound without
 * `!textInput` scrubs the round out from under anyone typing in a filter box, and
 * that is a bug you only find by having typed in the filter box.
 */
import { describe, expect, it } from 'vitest';

import { agentpediaModule } from '../index';
import { bindStepper, stepperAction } from '../actions';

describe('agentpedia manifest', () => {
  it('declares one singleton document pane with three sections', () => {
    expect(agentpediaModule.panels).toHaveLength(1);
    const [pane] = agentpediaModule.panels ?? [];
    expect(pane.id).toBe('agentpedia.hub');
    expect(pane.role).toBe('document');
    expect(pane.singleton).toBe(true);
    expect(pane.sections?.map((s) => s.id)).toEqual(['runs', 'harness', 'forks']);
    // Exactly one default, or the pane opens on whichever the host picks first.
    expect(pane.sections?.filter((s) => s.default)).toHaveLength(1);
  });

  it('gives every command an id under its own namespace', () => {
    const ids = (agentpediaModule.commands ?? []).map((c) => c.id);
    expect(ids).toContain('agentpedia.open');
    expect(ids.every((id) => id.startsWith('agentpedia.'))).toBe(true);
  });

  it('scopes the scrubbing keys to the pane and out of text inputs', () => {
    const keys = agentpediaModule.keybindings ?? [];
    expect(keys.map((k) => k.key).sort()).toEqual(['left', 'right']);
    for (const binding of keys) {
      expect(binding.when).toContain("paneFocus == 'agentpedia.hub'");
      expect(binding.when).toContain('!textInput');
    }
  });

  it('routes the scrubbing commands through the published handle', () => {
    const called: string[] = [];
    bindStepper({
      prevRound: () => called.push('prev'),
      nextRound: () => called.push('next'),
    });
    stepperAction('nextRound');
    stepperAction('prevRound');
    expect(called).toEqual(['next', 'prev']);

    // Unmounted, the commands are no-ops rather than throwing — the bindings are
    // pane-scoped anyway, and a palette entry that throws is worse than one that
    // does nothing.
    bindStepper(null);
    expect(() => stepperAction('nextRound')).not.toThrow();
  });
});
