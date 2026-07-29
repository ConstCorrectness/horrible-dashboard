import { describe, expect, it } from 'vitest';

import type { KeyContext } from '../context';
import { bindingsFor, explainBinding, resolveKey, type ResolvedBinding } from '../resolve';
import { parseSpec } from '../spec';

function ctx(over: Partial<KeyContext> = {}): KeyContext {
  return {
    paneFocus: null,
    paneInstance: null,
    capture: null,
    captureView: null,
    textInput: false,
    dialogOpen: false,
    fullscreenArea: false,
    shellView: 'workspace',
    platform: 'win',
    host: 'browser',
    ...over,
  };
}

let seq = 0;
function bind(key: string, command: string, extra: Partial<ResolvedBinding> = {}): ResolvedBinding {
  return {
    key,
    chord: parseSpec(key),
    command,
    source: 'default',
    order: seq++,
    ...extra,
  };
}

function press(
  k: string,
  mods: Partial<Pick<KeyboardEvent, 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>> = {},
  code = '',
): KeyboardEvent {
  return {
    key: k,
    code,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...mods,
  } as KeyboardEvent;
}

describe('precedence', () => {
  it('a satisfied pane-scoped when beats a plain global (the old scope rule)', () => {
    const bindings = [
      bind('n', 'region.toggle:right', { when: "paneFocus == 'editor.buffer'" }),
      bind('n', 'global.n'),
    ];
    expect(resolveKey(press('n'), ctx({ paneFocus: 'editor.buffer' }), bindings)).toMatchObject({
      command: 'region.toggle:right',
    });
    expect(resolveKey(press('n'), ctx(), bindings)).toMatchObject({ command: 'global.n' });
  });

  it('override beats a scoped binding', () => {
    const bindings = [
      bind('mod+k', 'terminal.clear', { when: "paneFocus == 'terminal.instance'" }),
      bind('mod+k', 'shell.commandPalette', { override: true }),
    ];
    expect(
      resolveKey(press('k', { ctrlKey: true }), ctx({ paneFocus: 'terminal.instance' }), bindings),
    ).toMatchObject({ command: 'shell.commandPalette' });
  });

  it('a user binding beats both a default and an override default', () => {
    const bindings = [
      bind('mod+k', 'shell.commandPalette', { override: true }),
      bind('mod+k', 'my.thing', { source: 'user' }),
    ];
    expect(resolveKey(press('k', { ctrlKey: true }), ctx(), bindings)).toMatchObject({
      command: 'my.thing',
    });
  });

  it('paneInstance is more specific than paneFocus', () => {
    const bindings = [
      bind('n', 'byView', { when: "paneFocus == 'editor.buffer'" }),
      bind('n', 'byInstance', { when: "paneInstance == 'editor.buffer#2'" }),
    ];
    const where = ctx({ paneFocus: 'editor.buffer', paneInstance: 'editor.buffer#2' });
    expect(resolveKey(press('n'), where, bindings)).toMatchObject({ command: 'byInstance' });
  });

  it('priority breaks a tie between equally specific clauses', () => {
    const bindings = [
      bind('n', 'first', { when: "paneFocus == 'x'" }),
      bind('n', 'second', { when: "paneFocus == 'x'", priority: 1 }),
    ];
    expect(resolveKey(press('n'), ctx({ paneFocus: 'x' }), bindings)).toMatchObject({
      command: 'second',
    });
  });

  it('falls back to registration order', () => {
    const bindings = [bind('n', 'first'), bind('n', 'second')];
    expect(resolveKey(press('n'), ctx(), bindings)).toMatchObject({ command: 'first' });
  });

  it('reports none when nothing matches', () => {
    expect(resolveKey(press('q'), ctx(), [bind('n', 'x')])).toEqual({ kind: 'none' });
  });
});

describe('the capture gate', () => {
  const bindings = [
    bind('t', 'region.toggle:left'),
    bind('mod+k', 'shell.commandPalette'),
    bind('code:KeyW', 'game.forward', { when: "paneFocus == 'hassault.play'" }),
    bind('mod+shift+q', 'shell.releaseCapture', { capturePassthrough: true }),
  ];
  const playing = ctx({
    paneFocus: 'hassault.play',
    capture: 'full',
    captureView: 'hassault.play',
  });

  it("full capture suppresses the shell's plain-letter and modified bindings alike", () => {
    // This is the bug the whole protocol exists for: today `t` toggles a region
    // strip while you are pointer-locked in a game.
    expect(resolveKey(press('t'), playing, bindings)).toEqual({ kind: 'none' });
    expect(resolveKey(press('k', { ctrlKey: true }), playing, bindings)).toEqual({ kind: 'none' });
  });

  it('the capturing pane still receives its own bindings', () => {
    expect(resolveKey(press('w', {}, 'KeyW'), playing, bindings)).toMatchObject({
      command: 'game.forward',
    });
  });

  it('capturePassthrough survives full capture', () => {
    expect(
      resolveKey(press('q', { ctrlKey: true, shiftKey: true }), playing, bindings),
    ).toMatchObject({ command: 'shell.releaseCapture' });
  });

  it('keyboard capture swallows bare keys but leaves modified chords alone', () => {
    const typing = ctx({
      paneFocus: 'editor.buffer',
      capture: 'keyboard',
      captureView: 'editor.buffer',
    });
    expect(resolveKey(press('t'), typing, bindings)).toEqual({ kind: 'none' });
    expect(resolveKey(press('k', { ctrlKey: true }), typing, bindings)).toMatchObject({
      command: 'shell.commandPalette',
    });
  });

  it('pointer capture suppresses nothing on the keyboard', () => {
    const pointer = ctx({ capture: 'pointer', captureView: 'visualizer.pane' });
    expect(resolveKey(press('t'), pointer, bindings)).toMatchObject({
      command: 'region.toggle:left',
    });
  });
});

describe('chord sequences', () => {
  const bindings = [bind('mod+k mod+s', 'keymap.open'), bind('mod+k', 'shell.commandPalette')];

  it('holds the prefix when only a longer chord matches', () => {
    const res = resolveKey(press('k', { ctrlKey: true }), ctx(), [bindings[0]]);
    expect(res.kind).toBe('pending');
  });

  it('completes the sequence on the second stroke', () => {
    const prefix = [press('k', { ctrlKey: true })];
    expect(resolveKey(press('s', { ctrlKey: true }), ctx(), bindings, prefix)).toMatchObject({
      command: 'keymap.open',
    });
  });

  it('a completed shorter binding wins over an unfinished longer one', () => {
    expect(resolveKey(press('k', { ctrlKey: true }), ctx(), bindings)).toMatchObject({
      command: 'shell.commandPalette',
    });
  });

  it('abandons the sequence when the second stroke matches nothing', () => {
    const prefix = [press('k', { ctrlKey: true })];
    expect(resolveKey(press('z'), ctx(), bindings, prefix)).toEqual({ kind: 'none' });
  });
});

describe('explainBinding', () => {
  it('names the clause that failed', () => {
    const b = bind('n', 'x', { when: "paneFocus == 'editor.buffer'" });
    expect(explainBinding(b, [b], ctx())).toEqual({
      reason: 'when-false',
      when: "paneFocus == 'editor.buffer'",
    });
  });

  it('names the pane that captured the keyboard', () => {
    const b = bind('t', 'x');
    const where = ctx({ capture: 'full', captureView: 'hassault.play' });
    expect(explainBinding(b, [b], where)).toEqual({ reason: 'captured', by: 'hassault.play' });
  });

  it('names the binding that shadowed it', () => {
    const loser = bind('mod+k', 'loser');
    const winner = bind('mod+k', 'winner', { source: 'user' });
    expect(explainBinding(loser, [loser, winner], ctx())).toMatchObject({
      reason: 'shadowed',
      by: { command: 'winner' },
    });
  });

  it('reports an active binding as active', () => {
    const b = bind('mod+k', 'x');
    expect(explainBinding(b, [b], ctx())).toEqual({ reason: 'active' });
  });
});

describe('bindingsFor', () => {
  it('lists a command bindings best-first', () => {
    const a = bind('mod+k', 'palette');
    const b = bind('f1', 'palette', { source: 'user' });
    expect(bindingsFor('palette', [a, b], ctx()).map((x) => x.key)).toEqual(['f1', 'mod+k']);
  });
});
