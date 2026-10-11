/**
 * The REPL console belongs to the pane, not the mounted component.
 *
 * The regression: `ReplPane` started a kernel in a mount effect and closed it in
 * the cleanup, and a pane unmounts on every tab and desktop switch — so looking at
 * another tab killed the interpreter and every variable in it, and reset the
 * transcript and input history with it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../ws', () => ({
  sendChannel: vi.fn(),
  subscribeChannel: vi.fn(() => () => {}),
}));
vi.mock('../../agent/tool-exec', () => ({ executeTool: vi.fn() }));

import {
  closePaneSession,
  paneSession,
  paneSessionKey,
  resetPaneSessionsForTests,
} from '../../../layout/pane-lifetime';
import type { ReplCallbacks, ReplSession } from '../client';
import { ReplConsole } from '../console';

function fakeSession() {
  let callbacks: ReplCallbacks = {};
  const session = {
    start: vi.fn(),
    exec: vi.fn(),
    dispose: vi.fn(),
  };
  const make = (_id: string, cb: ReplCallbacks) => {
    callbacks = cb;
    return session as unknown as ReplSession;
  };
  return { session, make, emit: () => callbacks };
}

const KEY = paneSessionKey('ws', 'repl.console#1');

beforeEach(() => resetPaneSessionsForTests());

describe('ReplConsole', () => {
  it('survives remounts and is stopped only by a real close', () => {
    const fake = fakeSession();
    const create = () => new ReplConsole('r1', fake.make);
    const first = paneSession(KEY, create, (c) => c.dispose());
    first.exec('x = 41');

    // A tab switch: the pane remounts and asks again.
    const again = paneSession(KEY, create, (c) => c.dispose());
    expect(again).toBe(first);
    expect(fake.session.start).toHaveBeenCalledTimes(1);
    expect(fake.session.dispose).not.toHaveBeenCalled();
    expect(again.history).toEqual(['x = 41']);

    closePaneSession(KEY);
    expect(fake.session.dispose).toHaveBeenCalledTimes(1);
  });

  it('keeps the transcript on the console, merging streamed chunks', () => {
    const fake = fakeSession();
    const repl = new ReplConsole('r1', fake.make);
    repl.exec('print(1)');
    fake.emit().onStdout?.('1');
    fake.emit().onStdout?.('\n');
    fake.emit().onResult?.({ ok: true, repr: null });
    expect(repl.getState().entries).toEqual([
      { kind: 'in', text: 'print(1)' },
      { kind: 'out', text: '1\n' },
    ]);
    expect(repl.getState().busy).toBe(false);
  });

  it('notifies subscribers so a remount renders the live state', () => {
    const fake = fakeSession();
    const repl = new ReplConsole('r1', fake.make);
    const seen = vi.fn();
    repl.subscribe(seen);
    repl.exec('1 + 1');
    expect(seen).toHaveBeenCalled();
    expect(repl.getState().busy).toBe(true);
  });
});
