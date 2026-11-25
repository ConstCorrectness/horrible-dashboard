import { describe, expect, it } from 'vitest';

import { NAMED_GLYPHS } from '../../../glyphs';
import { SUITE_PLAYBOOKS } from '../playbooks';
import { isSuiteSection } from '../stage';

describe('interpretability playbooks', () => {
  it('defines distinct playbooks with valid glyphs and distinct paired sections', () => {
    const ids = new Set<string>();
    expect(SUITE_PLAYBOOKS.length).toBeGreaterThanOrEqual(5);
    for (const pb of SUITE_PLAYBOOKS) {
      expect(ids.has(pb.id), `duplicate playbook ${pb.id}`).toBe(false);
      ids.add(pb.id);
      expect(NAMED_GLYPHS[pb.glyph], `glyph ${pb.glyph}`).toBeDefined();
      expect(isSuiteSection(pb.primary), `primary ${pb.primary}`).toBe(true);
      expect(isSuiteSection(pb.splitWith), `splitWith ${pb.splitWith}`).toBe(true);
      expect(pb.primary).not.toBe(pb.splitWith);
    }
  });
});
