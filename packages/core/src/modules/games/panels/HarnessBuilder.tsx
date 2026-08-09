import type { CSSProperties } from 'react';

import type { LlmHarness, LoadoutTemplate } from '../games-api';
import { CodeEditor } from './CodeEditor';
import { ToolsEditor } from './ToolsEditor';

/**
 * **The LLM-agent editor** — `my_agent`, the system prompt, and the tools the model
 * may call. The counterpart to `CodedBuilder`; see the note there for why the two
 * are separate components rather than branches of one.
 *
 * The folds stay collapsed by default because the code editor is the primary thing:
 * empty `agent_code` means the declarative harness *is* the agent, so a player who
 * only ever writes a prompt still has a working one.
 */
export function HarnessBuilder({
  harness,
  patch,
  templates,
  onApplyTemplate,
  agentError,
  showContext,
  setShowContext,
  showTools,
  setShowTools,
  btn,
}: {
  harness: LlmHarness;
  patch: (p: Partial<LlmHarness>) => void;
  templates: LoadoutTemplate[];
  onApplyTemplate: (t: LoadoutTemplate) => void;
  agentError: string | null;
  showContext: boolean;
  setShowContext: (fn: (s: boolean) => boolean) => void;
  showTools: boolean;
  setShowTools: (fn: (s: boolean) => boolean) => void;
  btn: CSSProperties;
}) {
  const fold: CSSProperties = {
    ...btn,
    border: 'none',
    borderTop: '1px solid var(--border, #33343a)',
    borderRadius: 0,
    textAlign: 'left',
    color: 'var(--text-dim)',
  };

  return (
    <>
      <div style={{ padding: 10, flex: 1, minHeight: 0 }}>
        <CodeEditor
          value={harness.agent_code}
          onChange={(v) => patch({ agent_code: v })}
          language="python"
          minHeight="320px"
          placeholder={'def my_agent(obs, config):\n    return obs["legal_actions"][0]["id"]'}
        />
      </div>
      {agentError && (
        <div
          style={{
            margin: '0 10px 10px',
            padding: '0.5rem 0.6rem',
            borderRadius: 6,
            fontFamily: 'var(--font-mono, monospace)',
            fontSize: '0.72rem',
            color: 'var(--danger, #f87171)',
            background: 'color-mix(in srgb, var(--danger, #f87171) 12%, transparent)',
            border: '1px solid color-mix(in srgb, var(--danger, #f87171) 40%, transparent)',
          }}
        >
          {agentError}
        </div>
      )}
      <button type="button" onClick={() => setShowContext((s) => !s)} style={fold}>
        {showContext ? '▾' : '▸'} Context (system prompt) — the default agent’s instructions
      </button>
      {showContext && (
        <div style={{ padding: 10 }}>
          <textarea
            value={harness.context}
            onChange={(e) => patch({ context: e.target.value })}
            placeholder="System prompt for the default agent (your context + tools drive the model)…"
            style={{
              width: '100%',
              minHeight: 90,
              resize: 'vertical',
              boxSizing: 'border-box',
              padding: '0.5rem',
              fontSize: '0.8rem',
              fontFamily: 'inherit',
              background: 'var(--bg, #1c1c1c)',
              color: 'var(--text)',
              border: '1px solid var(--border, #33343a)',
              borderRadius: 6,
            }}
          />
        </div>
      )}
      <button type="button" onClick={() => setShowTools((s) => !s)} style={fold}>
        {showTools ? '▾' : '▸'} Tools ({harness.tools.length}) — Python your agent can call
      </button>
      {showTools && (
        <div style={{ padding: 10 }}>
          <ToolsEditor
            tools={harness.tools}
            templates={templates}
            onChange={(tools) => patch({ tools })}
            onApplyTemplate={onApplyTemplate}
          />
        </div>
      )}
    </>
  );
}
