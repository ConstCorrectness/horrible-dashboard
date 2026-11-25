import { describe, expect, it } from 'vitest';

import { SUITE_SECTIONS } from '../ids';
import {
  isSuiteSection,
  NATURAL_COMPANIONS,
  nextCloseTabParams,
  nextOpenParams,
  parseStageState,
} from '../stage';

describe('interp IDE stage model', () => {
  it('always includes the active and split sections in openTabs without duplicates', () => {
    const state = parseStageState(
      { openTabs: ['home', 'lens', 'bogus', 'lens'], splitSection: 'model' },
      SUITE_SECTIONS.stepper,
    );
    expect(state.openTabs).toEqual(['home', 'lens', 'stepper', 'model']);
    expect(state.splitSection).toBe('model');
    expect(state.splitPrimarySide).toBe('left');
  });

  it('ignores a splitSection identical to the active section', () => {
    const state = parseStageState({ splitSection: 'model' }, SUITE_SECTIONS.model);
    expect(state.splitSection).toBeNull();
  });

  it('pairs two sections side-by-side while preserving unrelated pane params', () => {
    const next = nextOpenParams(
      { turnId: 'turn-42', openTabs: ['home'] },
      SUITE_SECTIONS.home,
      SUITE_SECTIONS.evals,
      { splitWith: SUITE_SECTIONS.runs, extraParams: { modelPath: '/tmp/a.gguf' } },
    );
    expect(next).toEqual({
      turnId: 'turn-42',
      modelPath: '/tmp/a.gguf',
      openTabs: ['home', 'evals', 'runs'],
      splitSection: 'runs',
      splitPrimarySide: 'left',
    });
  });

  it('swaps active and split without collapsing when navigating onto the companion slot', () => {
    const start = {
      openTabs: ['stepper', 'model'],
      splitSection: 'model',
      splitPrimarySide: 'left',
    };
    const next = nextOpenParams(start, SUITE_SECTIONS.stepper, SUITE_SECTIONS.model);
    expect(next.splitSection).toBe('stepper');
    expect(next.splitPrimarySide).toBe('right');
  });

  it('promotes the split companion when closing the active tab', () => {
    const { params, nextActive } = nextCloseTabParams(
      { openTabs: ['home', 'stepper', 'model'], splitSection: 'model' },
      SUITE_SECTIONS.stepper,
      SUITE_SECTIONS.stepper,
    );
    expect(nextActive).toBe('model');
    expect(params.openTabs).toEqual(['home', 'model']);
    expect(params.splitSection).toBeUndefined();
  });

  it('maps natural companions only to valid distinct sections', () => {
    for (const [from, target] of Object.entries(NATURAL_COMPANIONS)) {
      expect(isSuiteSection(from)).toBe(true);
      expect(target && isSuiteSection(target.section)).toBe(true);
      expect(target?.section).not.toBe(from);
    }
  });
});
