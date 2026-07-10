/**
 * Agent-tool reference / validation stub (dev-only — registered only under
 * `import.meta.env.DEV`). The smallest possible exercise of the full agent tool
 * surface: a read tool, a gated side-effecting tool, and a `getAgentContext`
 * snapshot. Used to validate the manifest → gate → approval → relay → handler
 * path end to end before real modules (editor/terminal/files) build on it. See
 * docs/architecture/agent-tools.md.
 */
import { useSyncExternalStore } from 'react';

import { useAgentContext } from '../../agent-context';
import { type ModuleManifest } from '../../registry';

// A trivial piece of state the gated tool mutates and the read tool/context
// report — so a turn's effect is observable in the widget and to the agent.
let value = '(unset)';
const listeners = new Set<() => void>();

function setValue(next: string): void {
  value = next;
  for (const listener of listeners) listener();
}

const store = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): string {
    return value;
  },
};

function StubWidget() {
  const v = useSyncExternalStore(store.subscribe, store.getSnapshot);
  useAgentContext(() => ({ value: v }));
  return (
    <div className="ws-panel-pad">
      <h3>Agent stub</h3>
      <p>
        value: <code>{v}</code>
      </p>
      <p className="setting-desc">
        Dev-only reference for the agent tool surface. Ask the agent to “set the stub value to
        hello”.
      </p>
    </div>
  );
}

export const stubModule: ModuleManifest = {
  id: 'stub',
  title: 'Agent stub',
  widgets: [
    {
      id: 'stub.panel',
      title: 'Agent stub',
      component: StubWidget,
      role: 'widget',
      icon: '🧪',
      agentTools: [
        {
          name: 'stub.getValue',
          description: 'Read the current stub value.',
          sideEffect: false,
          handler: () => ({ value }),
        },
        {
          name: 'stub.setValue',
          description: 'Set the stub value to the given text.',
          params: {
            type: 'object',
            properties: { value: { type: 'string', description: 'New value' } },
            required: ['value'],
          },
          sideEffect: true,
          specifierTemplate: '{value}',
          handler: (args) => {
            setValue(String(args.value ?? ''));
            return { ok: true, value };
          },
        },
      ],
    },
  ],
};
