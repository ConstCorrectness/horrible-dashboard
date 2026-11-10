// @vitest-environment happy-dom
/**
 * A tool that decides its gate per call (`AgentToolDecl.gate`): the manifest tells
 * the backend so (`gated`), and the relay answers the backend's question from the
 * tool's own `gate`. The backend half is backend/tests/test_agent_gate_probe.py.
 */
import { afterEach, describe, expect, it } from 'vitest';

import { registry } from '../../../registry';
import { gateDynamicTool, serializeManifest } from '../manifest';

const ID = 'gate-test';

afterEach(() => {
  registry.resetForTests();
});

function register(): void {
  registry.register({
    id: ID,
    title: 'Gate test',
    panels: [
      {
        id: `${ID}.view`,
        title: 'Gate test',
        role: 'tool',
        component: () => null,
        agentTools: [
          {
            name: 'gatetest.act',
            description: 'act',
            sideEffect: true,
            gate: (args) => ({
              sideEffect: args.kind === 'submit',
              specifier: `${String(args.kind)}:example.com`,
            }),
            handler: () => ({ ok: true }),
          },
          {
            name: 'gatetest.plain',
            description: 'plain',
            sideEffect: true,
            handler: () => ({ ok: true }),
          },
        ],
      },
    ],
  });
}

describe('per-call gates', () => {
  it('flags gated tools in the manifest, and only those', () => {
    register();
    const tools = serializeManifest();
    expect(tools.find((t) => t.name === 'gatetest.act')?.gated).toBe(true);
    expect(tools.find((t) => t.name === 'gatetest.plain')).not.toHaveProperty('gated');
  });

  it("answers the backend's question from the tool's gate", async () => {
    register();
    await expect(gateDynamicTool('gatetest.act', { kind: 'navigate' })).resolves.toEqual({
      sideEffect: false,
      specifier: 'navigate:example.com',
      rule: null,
    });
    await expect(gateDynamicTool('gatetest.act', { kind: 'submit' })).resolves.toMatchObject({
      sideEffect: true,
    });
  });

  it('refuses a tool with no gate, so the backend uses the static fields', async () => {
    register();
    await expect(gateDynamicTool('gatetest.plain', {})).rejects.toThrow(/no per-call gate/);
  });
});
