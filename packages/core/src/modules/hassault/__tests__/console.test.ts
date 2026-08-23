import { describe, expect, it, vi } from 'vitest';
import { ConsoleExecutor } from '../console/executor';
import { consoleRegistry } from '../console/registry';

describe('hAssault Developer Console: CVar Registry', () => {
  it('initializes default client CVars', () => {
    expect(consoleRegistry.get('net.graph')).toBe(0);
    expect(consoleRegistry.get('draw.hitboxes')).toBe(false);
    expect(consoleRegistry.get('draw.fov')).toBe(75);
    expect(consoleRegistry.get('server.timescale')).toBe(1.0);
  });

  it('sets and coerces CVar values with min/max clamping', () => {
    // Number with clamp
    const setFovOk = consoleRegistry.set('draw.fov', 150);
    expect(setFovOk).toBe(true);
    expect(consoleRegistry.get('draw.fov')).toBe(110);

    const setFovLow = consoleRegistry.set('draw.fov', 20);
    expect(setFovLow).toBe(true);
    expect(consoleRegistry.get('draw.fov')).toBe(60);

    // Boolean coercion
    consoleRegistry.set('draw.wireframe', 'true');
    expect(consoleRegistry.get('draw.wireframe')).toBe(true);

    consoleRegistry.set('draw.wireframe', '0');
    expect(consoleRegistry.get('draw.wireframe')).toBe(false);
  });

  it('notifies subscribers on CVar changes', () => {
    const listener = vi.fn();
    const unsub = consoleRegistry.subscribe(listener);

    consoleRegistry.set('draw.hitboxes', true);
    expect(listener).toHaveBeenCalledWith('draw.hitboxes', true, false);

    unsub();
    consoleRegistry.set('draw.hitboxes', false);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('matches autocomplete queries with correct ranking', () => {
    const results = consoleRegistry.autocomplete('draw.');
    expect(results.length).toBeGreaterThan(0);
    expect(results.every((r) => r.name.startsWith('draw.'))).toBe(true);

    const hitboxResult = consoleRegistry.autocomplete('hitbox');
    expect(hitboxResult.some((r) => r.name.includes('hitbox'))).toBe(true);
  });
});

describe('hAssault Developer Console: Executor & History', () => {
  it('manages command history navigation', () => {
    const executor = new ConsoleExecutor();
    executor.history = [];
    executor.historyIndex = -1;

    executor.recordHistory('draw.hitboxes 1');
    executor.recordHistory('server.timescale 0.5');

    expect(executor.historyPrev()).toBe('server.timescale 0.5');
    expect(executor.historyPrev()).toBe('draw.hitboxes 1');
    expect(executor.historyNext()).toBe('server.timescale 0.5');
    expect(executor.historyNext()).toBe('');
  });

  it('handles client-side binds and aliases', async () => {
    const executor = new ConsoleExecutor();

    // Bind test
    const bindRes = await executor.execute('bind F1 "macro.run(\'warmup\')"');
    expect(bindRes.ok).toBe(true);
    expect(executor.binds.get('f1')).toBe("macro.run('warmup')");

    // Unbind test
    const unbindRes = await executor.execute('unbind F1');
    expect(unbindRes.ok).toBe(true);
    expect(executor.binds.has('f1')).toBe(false);

    // Alias test
    const aliasRes = await executor.execute('alias prac "server.cheats 1; draw.hitboxes 1"');
    expect(aliasRes.ok).toBe(true);
    expect(executor.aliases.get('prac')).toBe('server.cheats 1; draw.hitboxes 1');
  });

  it('executes client CVar changes directly without network', async () => {
    const executor = new ConsoleExecutor();

    const res = await executor.execute('net.graph 2');
    expect(res.ok).toBe(true);
    expect(res.affected_cvars['net.graph']).toBe(2);
    expect(consoleRegistry.get('net.graph')).toBe(2);

    const queryRes = await executor.execute('net.graph');
    expect(queryRes.ok).toBe(true);
    expect(queryRes.result_data).toBe(2);
  });
});
