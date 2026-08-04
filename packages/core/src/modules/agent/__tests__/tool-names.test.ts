import { describe, expect, it } from 'vitest';

import { LAYOUT_VERBS, nearestToolNames, toolKey } from '../tool-names';

const KNOWN = [...LAYOUT_VERBS, 'files.read', 'files.delete', 'social.list_friends'];

describe('toolKey', () => {
  it('collapses case and separators so naming styles converge', () => {
    const same = ['open_pane', 'openPane', 'open-pane', 'Open_Pane'].map(toolKey);
    expect(new Set(same).size).toBe(1);
  });
});

describe('nearestToolNames', () => {
  it('recovers the common small-model slips', () => {
    // Wrong separator style.
    expect(nearestToolNames('openPane', KNOWN)).toContain('open_pane');
    // A plausible-but-wrong name for a real verb.
    expect(nearestToolNames('list_panes', KNOWN)).toContain('list_open_panes');
    // Single-character typo.
    expect(nearestToolNames('focus_pan', KNOWN)).toContain('focus_pane');
    // Dotted namespaces work the same way.
    expect(nearestToolNames('files.remove', KNOWN)).toContain('files.delete');
  });

  it('ranks an exact normalized match first', () => {
    expect(nearestToolNames('GetLayout', KNOWN)[0]).toBe('get_layout');
  });

  it('returns nothing for input with no plausible neighbour', () => {
    expect(nearestToolNames('quantum_flux_capacitor', KNOWN)).toEqual([]);
    expect(nearestToolNames('', KNOWN)).toEqual([]);
  });

  it('caps the suggestions, so the hint stays cheaper than the catalog', () => {
    expect(nearestToolNames('pane', KNOWN).length).toBeLessThanOrEqual(3);
    expect(nearestToolNames('pane', KNOWN, 1).length).toBeLessThanOrEqual(1);
  });

  it('never suggests a name that is not in the known set', () => {
    for (const suggestion of nearestToolNames('open_pain', KNOWN)) {
      expect(KNOWN).toContain(suggestion);
    }
  });
});
