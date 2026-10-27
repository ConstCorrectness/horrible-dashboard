import { describe, expect, it } from 'vitest';

import { GLOSSARY_ENTRIES, RESEARCH_PANE_GUIDE } from '../ResearchGuide';
import { interpretabilityModule } from '../index';

describe('RESEARCH_PANE_GUIDE', () => {
  it('covers all seven stages of the research lifecycle', () => {
    const stageIds = RESEARCH_PANE_GUIDE.map((s) => s.id);
    expect(stageIds).toEqual([
      'papers',
      'datasets',
      'context',
      'architecture',
      'activations',
      'evals',
      'training',
    ]);
  });

  it('provides complete metadata for each research stage', () => {
    for (const stage of RESEARCH_PANE_GUIDE) {
      expect(stage.title).toBeTruthy();
      expect(stage.icon).toBeTruthy();
      expect(stage.category).toBeTruthy();
      expect(stage.paneId).toMatch(/^[a-z0-9_]+\.[a-z0-9_]+$/);
      expect(stage.question.length).toBeGreaterThan(15);
      expect(stage.when.length).toBeGreaterThan(15);
      expect(stage.tip.length).toBeGreaterThan(15);
    }
  });

  it('maps the three core interpretability stages onto the suite', () => {
    const contextStage = RESEARCH_PANE_GUIDE.find((s) => s.id === 'context');
    const archStage = RESEARCH_PANE_GUIDE.find((s) => s.id === 'architecture');
    const actStage = RESEARCH_PANE_GUIDE.find((s) => s.id === 'activations');

    for (const stage of [contextStage, archStage, actStage]) {
      expect(stage?.paneId).toBe('interpretability.suite');
    }
    expect(contextStage?.sectionId).toBe('context');
    expect(archStage?.sectionId).toBe('model');
    expect(actStage?.sectionId).toBe('lens');
    expect(actStage?.altSectionId).toBe('stepper');
  });
});

describe('GLOSSARY_ENTRIES', () => {
  it('defines crucial beginner concepts for model inspection', () => {
    const requiredKeys = [
      'gqa',
      'mha',
      'mqa',
      'kv_cache',
      'swiglu',
      'moe',
      'rope',
      'quants',
      'tool_budget',
      'context_window',
    ];

    for (const key of requiredKeys) {
      const entry = GLOSSARY_ENTRIES[key];
      expect(entry).toBeDefined();
      expect(entry.title).toBeTruthy();
      expect(entry.explanation).toBeTruthy();
    }
  });
});

describe('interpretabilityModule commands', () => {
  it('registers the interpretability.guide command for the workflow guide', () => {
    const cmd = interpretabilityModule.commands?.find((c) => c.id === 'interpretability.guide');
    expect(cmd).toBeDefined();
    expect(cmd?.title).toContain('Workflow Guide');
  });
});
