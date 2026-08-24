/**
 * Workspace names must be distinguishable.
 *
 * The Start menu's Desktops band shows a workspace by name and nothing else that
 * separates one from another. Two desktops both called "Workspace" are therefore
 * two identical rows, and switching desktops becomes a guess — the user finds out
 * which one they picked only after the whole layout has been replaced.
 *
 * Suffixing rather than rejecting, because both callers are non-blocking: a
 * command that supplies a default name, and an agent tool. Failing them over a
 * collision would trade a cosmetic problem for a broken action.
 */
import { describe, expect, it } from 'vitest';

import { uniqueWorkspaceName } from '../persistence';

describe('uniqueWorkspaceName', () => {
  it('leaves a name alone when nothing else has it', () => {
    expect(uniqueWorkspaceName('Research', ['Work', 'Play'])).toBe('Research');
  });

  it('numbers a collision rather than rejecting it', () => {
    expect(uniqueWorkspaceName('Workspace', ['Workspace'])).toBe('Workspace 2');
  });

  it('keeps counting past an existing suffix', () => {
    expect(uniqueWorkspaceName('Workspace', ['Workspace', 'Workspace 2'])).toBe('Workspace 3');
  });

  it('treats case and surrounding space as the same name', () => {
    // These render identically in a menu, so they collide for this purpose even
    // though they are different strings.
    expect(uniqueWorkspaceName('workspace', ['Workspace'])).toBe('workspace 2');
    expect(uniqueWorkspaceName('  Work  ', ['Work'])).toBe('Work 2');
  });

  it('falls back to a real name when given nothing', () => {
    expect(uniqueWorkspaceName('   ', [])).toBe('Workspace');
  });
});
