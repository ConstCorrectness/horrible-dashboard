import { describe, expect, it } from 'vitest';

import { atPrompt, QUIET_MS } from '../prompt';

const quiet = QUIET_MS + 1;

describe('atPrompt', () => {
  it('is done when PowerShell is back at its prompt', () => {
    const screen = [
      'PS C:\\Users\\me> python "C:\\Users\\me\\main.py"',
      'long task...',
      '...done',
      'goodbye!',
      'PS C:\\Users\\me> ',
    ].join('\n');
    expect(atPrompt(screen, quiet)).toBe(true);
  });

  it('is done at a bash, zsh or root prompt', () => {
    expect(atPrompt('hi\nme@box:~/code$ ', quiet)).toBe(true);
    expect(atPrompt('hi\nbox% ', quiet)).toBe(true);
    expect(atPrompt('hi\nroot@box:/# ', quiet)).toBe(true);
  });

  it('is not done while a script pauses between prints', () => {
    // The exact case that returned early: quiet, but no prompt yet.
    const screen = 'PS C:\\Users\\me> python main.py\nlong task...';
    expect(atPrompt(screen, quiet)).toBe(false);
  });

  it('is not done while output is still arriving', () => {
    expect(atPrompt('PS C:\\Users\\me> ', QUIET_MS - 1)).toBe(false);
  });

  it('ignores trailing blank lines', () => {
    expect(atPrompt('out\nPS C:\\> \n\n', quiet)).toBe(true);
  });
});
