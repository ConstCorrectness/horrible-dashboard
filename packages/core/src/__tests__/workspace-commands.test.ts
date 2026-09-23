/**
 * Every workspace preset is a palette command by name.
 *
 * `workspace.switch:1..9` switches by position — nobody types "#6" meaning "AI
 * Research" — and the launcher only renders on the home surface. The synthesized
 * `Workspace: <name>` entries make the presets reachable from the one surface that
 * is always there. Synthetic manifest: a real one pulls in panes that need jsdom.
 */
import { describe, expect, it, vi } from 'vitest';

import { registry } from '../registry';

describe('workspace commands', () => {
  it('synthesizes one named command per preset, routed through switchWorkspace', async () => {
    registry.register({
      id: 'test-workspace-commands',
      title: 'Test',
      frames: [
        {
          id: 'lab-bench',
          name: 'Lab Bench',
          description: 'A test preset.',
          icon: 'x',
          frame: { center: { tabs: [] } },
        },
      ],
    });
    const cmd = registry.commands.find((c) => c.id === 'workspace.open:lab-bench');
    expect(cmd?.title).toBe('Workspace: Lab Bench');

    const switcher = vi.fn();
    registry.setWorkspaceSwitcher(switcher);
    await registry.runCommand('workspace.open:lab-bench');
    expect(switcher).toHaveBeenCalledWith('lab-bench');
  });

  it('lists none while workspaces are off, the same rule as the launcher and mod+1..9', () => {
    registry.setWorkspaceGate(() => false);
    expect(registry.commands.some((c) => c.id.startsWith('workspace.open:'))).toBe(false);
    registry.setWorkspaceGate(() => true);
    expect(registry.commands.some((c) => c.id === 'workspace.open:lab-bench')).toBe(true);
  });
});
