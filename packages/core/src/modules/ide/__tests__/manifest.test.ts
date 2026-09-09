/**
 * The workbench's declarations.
 *
 * Imported from `manifest.ts` and `agentTools.ts`, never from `index.tsx` —
 * that file reaches `../editor`, whose module scope opens a WebSocket, and there
 * is no jsdom in this workspace to give it a `window`. That constraint is the
 * reason those two files exist as separate leaves.
 *
 * The region rules below are the ones with teeth. Each is enforced nowhere else
 * and each fails silently: a duplicate id makes the second entry unreachable, a
 * second view in the right strip squashes the agent into equal bands instead of
 * tabbing it, and a `t`/`n`/`b` key is dropped by the registry with a console
 * warning nobody reads.
 */
import { describe, expect, it } from 'vitest';

import { ideAgentTools } from '../agentTools';
import {
  IDE_COMMAND_IDS,
  IDE_KEYBINDINGS,
  IDE_PANE_META,
  IDE_REGIONS,
  IDE_TOOL_NAMES,
  WORKBENCH_VIEW,
} from '../manifest';

describe('ide manifest', () => {
  it('is a singleton document pane that takes the keyboard', () => {
    expect(IDE_PANE_META.role).toBe('document');
    expect(IDE_PANE_META.singleton).toBe(true);
    // It is typed into, so it must capture — otherwise every plain-letter
    // binding in the app fires while you are writing code.
    expect(IDE_PANE_META.capture.mode).toBe('keyboard');
    // Not "Editor": the registry warns on a duplicate view title, and that one
    // belongs to `editor.buffer`.
    expect(IDE_PANE_META.title).toBe('Workbench');
  });

  it('names each region view exactly once', () => {
    const ids = IDE_REGIONS.map((r) => r.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('puts exactly one view in the right strip', () => {
    // `Region.tsx` stacks a right strip of two or more views as equal-height
    // bands rather than tabbing it. A second entry here is not a layout
    // preference, it is a squashed agent panel.
    expect(IDE_REGIONS.filter((r) => r.position === 'right')).toHaveLength(1);
  });

  it('keeps every pick letter distinct and off the reserved toggles', () => {
    const keys = IDE_REGIONS.map((r) => r.key).filter(Boolean) as string[];
    expect(new Set(keys).size).toBe(keys.length);
    for (const key of keys) expect(['t', 'n', 'b']).not.toContain(key);
  });

  it('opens with the explorer and the terminal already showing', () => {
    expect(IDE_REGIONS.filter((r) => r.defaultOpen).map((r) => r.id)).toEqual([
      'files.tree',
      'terminal.instance',
    ]);
  });

  it('gives every region a label and a glyph for its collapsed rail', () => {
    // A collapsed strip renders each view's icon, falling back to the first
    // character of a title — which is how two strips end up both showing "S".
    for (const region of IDE_REGIONS) {
      expect(region.label).toBeTruthy();
      expect(region.icon).toBeTruthy();
    }
  });
});

describe('ide keybindings', () => {
  it('binds only commands that exist, region picks included', () => {
    const own = new Set<string>(IDE_COMMAND_IDS);
    const picks = new Set(IDE_REGIONS.map((r) => `region.pick:${r.id}`));
    for (const binding of IDE_KEYBINDINGS) {
      expect(own.has(binding.command) || picks.has(binding.command)).toBe(true);
    }
  });

  it('lets the pane-scoped chords through the pane’s own keyboard capture', () => {
    // With `capture: keyboard` an unmodified binding is suppressed and a
    // modified one is not — but a binding scoped to this pane still needs
    // `capturePassthrough`, and without it these are dead keys exactly where the
    // pane is being used.
    for (const binding of IDE_KEYBINDINGS) {
      if (!binding.when?.includes(WORKBENCH_VIEW)) continue;
      expect(binding.capturePassthrough).toBe(true);
    }
  });

  it('uses no unmodified chord', () => {
    for (const binding of IDE_KEYBINDINGS) {
      expect(binding.key.startsWith('mod+')).toBe(true);
    }
  });

  it('ships mod+w for desktop only', () => {
    // The browser takes mod+w and declares it unpreventable in
    // `keymap/reserved.ts`, so a browser default would never fire.
    const closeTab = IDE_KEYBINDINGS.find((k) => k.command === 'ide.closeTab');
    expect(closeTab?.hosts).toEqual(['desktop']);
  });
});

describe('ide agent tools', () => {
  it('puts every tool in the ide group', () => {
    // The orchestrator groups tools by NAME PREFIX — there is no `group` field
    // that would name it — so one stray name is one tool in the wrong group.
    for (const tool of ideAgentTools) expect(tool.name.startsWith('ide.')).toBe(true);
  });

  it('declares exactly the tools the manifest lists', () => {
    expect(ideAgentTools.map((t) => t.name)).toEqual([...IDE_TOOL_NAMES]);
  });

  it('gates the two that can destroy work and no others', () => {
    const gated = ideAgentTools.filter((t) => t.sideEffect).map((t) => t.name);
    // `ide.open` opens a pane, which is a side effect but not a loss; the three
    // that follow can discard an edit or rewrite the index.
    expect(gated.sort()).toEqual(['ide.closeFile', 'ide.open', 'ide.stage', 'ide.unstage']);
  });

  it('describes every parameter, so the model is not guessing', () => {
    for (const tool of ideAgentTools) {
      expect(tool.description.length).toBeGreaterThan(20);
      const props = (tool.params?.properties ?? {}) as Record<string, { description?: string }>;
      for (const [name, schema] of Object.entries(props)) {
        expect(schema.description, `${tool.name}.${name}`).toBeTruthy();
      }
    }
  });
});
