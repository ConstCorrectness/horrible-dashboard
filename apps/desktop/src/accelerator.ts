/**
 * Our key specs (`mod+shift+k`) → Electron accelerators (`CommandOrControl+Shift+K`).
 *
 * The frontend owns the keymap (`packages/core/src/keymap/global.ts`), so the shell
 * only translates the spelling. `mod` is ctrl-or-cmd; `code:` specs are positional
 * and have no accelerator spelling at all, so they are rejected rather than silently
 * registered as the wrong key. An unknown modifier is rejected the same way.
 */
export function toAccelerator(spec: string): string | null {
  const tokens = spec.split('+');
  const key = tokens.pop();
  if (!key || key.startsWith('code:')) return null;
  const parts: string[] = [];
  for (const token of tokens) {
    switch (token.toLowerCase()) {
      case 'mod':
        parts.push('CommandOrControl');
        break;
      case 'ctrl':
      case 'control':
        parts.push('Control');
        break;
      case 'meta':
      case 'cmd':
      case 'command':
      case 'super':
        parts.push('Super');
        break;
      case 'alt':
      case 'option':
        parts.push('Alt');
        break;
      case 'shift':
        parts.push('Shift');
        break;
      default:
        return null;
    }
  }
  const named: Record<string, string> = {
    space: 'Space',
    esc: 'Escape',
    escape: 'Escape',
    left: 'Left',
    arrowleft: 'Left',
    right: 'Right',
    arrowright: 'Right',
    up: 'Up',
    arrowup: 'Up',
    down: 'Down',
    arrowdown: 'Down',
    enter: 'Enter',
    tab: 'Tab',
    backspace: 'Backspace',
    delete: 'Delete',
    home: 'Home',
    end: 'End',
    pageup: 'PageUp',
    pagedown: 'PageDown',
  };
  parts.push(named[key.toLowerCase()] ?? key.toUpperCase());
  return parts.join('+');
}
