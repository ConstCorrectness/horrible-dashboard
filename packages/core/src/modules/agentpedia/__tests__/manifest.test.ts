/**
 * The agentpedia manifest, and the two rules about it that are silent when broken.
 *
 * A registration smoke test is a module convention (see `.claude/skills/new-module`),
 * but the keybinding assertions are the ones with teeth: an arrow key bound without
 * `!textInput` scrubs the round out from under anyone typing in a filter box, and
 * one bound to the suite without its section scrubs a turn from inside the lens.
 */
import { describe, expect, it } from 'vitest';

import { agentpediaModule } from '../index';
import { bindStepper, stepperAction } from '../actions';
import { AGENTPEDIA_TABS } from '../tabs';

describe('agentpedia manifest', () => {
  it('has no pane of its own: it is the suite Runs section, with four tabs', () => {
    expect(agentpediaModule.panels ?? []).toHaveLength(0);
    expect(AGENTPEDIA_TABS.map((t) => t.id)).toEqual(['runs', 'harness', 'forks', 'duels']);
  });

  it('gives every command an id under its own namespace', () => {
    const ids = (agentpediaModule.commands ?? []).map((c) => c.id);
    expect(ids).toContain('agentpedia.open');
    expect(ids.every((id) => id.startsWith('agentpedia.'))).toBe(true);
  });

  it('scopes the scrubbing keys to the Runs section and out of text inputs', () => {
    const keys = agentpediaModule.keybindings ?? [];
    expect(keys.map((k) => k.key).sort()).toEqual(['left', 'right']);
    for (const binding of keys) {
      expect(binding.when).toContain("paneFocus == 'interpretability.suite'");
      expect(binding.when).toContain("paneSection == 'runs'");
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
    // section-scoped anyway, and a palette entry that throws is worse than one
    // that does nothing.
    bindStepper(null);
    expect(() => stepperAction('nextRound')).not.toThrow();
  });
});
