import { describe, expect, it } from 'vitest';

import { checkReservedSpec, RESERVED } from '../reserved';
import { tryParseSpec } from '../spec';

const browserWin = { platform: 'win', host: 'browser' } as const;
const desktopWin = { platform: 'win', host: 'desktop' } as const;
const browserMac = { platform: 'mac', host: 'browser' } as const;

describe('checkReserved', () => {
  it('flags mod+1..9 in the browser but not on the desktop', () => {
    // The live bug: workspace switching has never worked in the browser layout.
    const hit = checkReservedSpec('mod+1', browserWin);
    expect(hit).toMatchObject({ preventable: false });
    expect(hit?.owner).toMatch(/tab/i);
    expect(checkReservedSpec('mod+1', desktopWin)).toBeNull();
  });

  it('matches a positional spec against a character-spelled reservation', () => {
    expect(checkReservedSpec('mod+code:Digit1', browserWin)).toMatchObject({ preventable: false });
    expect(checkReservedSpec('mod+code:KeyW', browserWin)?.owner).toMatch(/close tab/i);
  });

  it('resolves mod per platform', () => {
    // meta+q is macOS-only; on Windows the same spec is free.
    expect(checkReservedSpec('meta+q', browserMac)).toMatchObject({ owner: 'macOS (quit app)' });
    expect(checkReservedSpec('meta+q', browserWin)).toBeNull();
    // ctrl+1 is Chrome tab switching on Windows, where mod == ctrl.
    expect(checkReservedSpec('ctrl+1', browserWin)).not.toBeNull();
    // ...but on macOS mod == cmd, so ctrl+1 is ours.
    expect(checkReservedSpec('ctrl+1', browserMac)).toBeNull();
  });

  it('flags ctrl+space on both mac and Windows, for different reasons', () => {
    expect(checkReservedSpec('ctrl+space', browserMac)?.owner).toMatch(/input source/i);
    expect(checkReservedSpec('ctrl+space', browserWin)?.owner).toMatch(/IME/i);
    expect(checkReservedSpec('ctrl+space', { platform: 'linux', host: 'browser' })).toBeNull();
  });

  it('distinguishes preventable reservations from unusable ones', () => {
    expect(checkReservedSpec('alt+left', browserWin)).toMatchObject({ preventable: true });
    expect(checkReservedSpec('f11', browserWin)).toMatchObject({ preventable: false });
  });

  it('applies OS-level reservations to the desktop shell too', () => {
    expect(checkReservedSpec('alt+f4', desktopWin)).toMatchObject({ preventable: false });
  });

  it('only inspects the first stroke of a sequence', () => {
    // Once a sequence has started we already own the keyboard.
    expect(checkReservedSpec('mod+shift+p mod+1', browserWin)).toBeNull();
  });

  it('leaves ordinary chords alone', () => {
    expect(checkReservedSpec('mod+k', browserWin)).toBeNull();
    expect(checkReservedSpec('alt+x', browserWin)).toBeNull();
  });

  it('every table entry is a parseable spec', () => {
    for (const entry of RESERVED) {
      expect(tryParseSpec(entry.key), entry.key).not.toBeNull();
    }
  });
});
