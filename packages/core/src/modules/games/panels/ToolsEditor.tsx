import { useState, type CSSProperties } from 'react';

import type { LoadoutTemplate, LoadoutTool } from '../games-api';
import { CodeEditor } from './CodeEditor';

/**
 * The **Tools** section of Build your agent: the custom Python tools your agent can
 * call while it decides a move (`loadout.tools` — see backend/modules/games/loadout.py).
 *
 * Each tool is a name + description (both go to the model as the tool schema), a
 * `run(args, obs)` body, and the `parameters` the model may pass. The builder had no
 * UI for any of this — you could only write `my_agent` — so the shipped **templates**
 * (backend/modules/games/templates.py) were unreachable from the app. This surfaces
 * them: pick a template to load its tool definitions (and its context) as a starting
 * point, then edit them like any other code.
 *
 * `parameters` is edited as JSON because it's a JSON-Schema properties map — a form
 * would only re-encode the same thing less flexibly. Invalid JSON is kept as text and
 * flagged, never silently dropped.
 */

const card: CSSProperties = {
  background: 'var(--bg, #1c1c1c)',
  border: '1px solid var(--border, #33343a)',
  borderRadius: 8,
};

const btn: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.72rem',
  padding: '0.3rem 0.55rem',
  borderRadius: 6,
  border: '1px solid var(--border, #33343a)',
  background: 'transparent',
  color: 'var(--text-dim)',
  cursor: 'pointer',
};

const input: CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  padding: '0.35rem 0.45rem',
  fontSize: '0.76rem',
  background: 'var(--surface, #16171d)',
  color: 'var(--text)',
  border: '1px solid var(--border, #33343a)',
  borderRadius: 6,
};

const label: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.64rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'var(--text-faint, #666)',
  display: 'block',
  marginBottom: 3,
};

/** A fresh tool, pre-filled with the `run(args, obs)` contract so a new tool is
 * a working (if useless) tool rather than a syntax error. */
function blankTool(n: number): LoadoutTool {
  return {
    name: `my_tool_${n}`,
    description:
      'What this tool tells your agent — the model reads this to decide when to call it.',
    code: 'def run(args, obs):\n    """args: the model\'s arguments; obs: this seat\'s observation."""\n    return {"note": "replace me"}\n',
    parameters: {},
    required: [],
  };
}

export function ToolsEditor({
  tools,
  templates,
  onChange,
  onApplyTemplate,
}: {
  tools: LoadoutTool[];
  templates: LoadoutTemplate[];
  onChange: (tools: LoadoutTool[]) => void;
  onApplyTemplate: (t: LoadoutTemplate) => void;
}) {
  const [open, setOpen] = useState<number | null>(null);

  const patch = (i: number, p: Partial<LoadoutTool>) =>
    onChange(tools.map((t, j) => (i === j ? { ...t, ...p } : t)));

  const remove = (i: number) => {
    onChange(tools.filter((_, j) => j !== i));
    setOpen(null);
  };

  const add = () => {
    onChange([...tools, blankTool(tools.length + 1)]);
    setOpen(tools.length);
  };

  return (
    <div style={{ display: 'grid', gap: 8 }}>
      {templates.length > 0 && (
        <div style={{ display: 'grid', gap: 6 }}>
          <span style={label}>Templates — shipped tool definitions for this game</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {templates.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => onApplyTemplate(t)}
                title={t.blurb}
                style={btn}
              >
                + {t.title}
                <span style={{ color: 'var(--text-faint, #666)' }}>
                  {' '}
                  · {t.loadout.tools.length} tool{t.loadout.tools.length === 1 ? '' : 's'}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {tools.length === 0 ? (
        <div style={{ fontSize: '0.74rem', color: 'var(--text-dim)' }}>
          No tools. Your agent decides from the observation alone — which is fine for a code-first{' '}
          <code>my_agent</code>. Add a tool (or a template above) to give the model something to
          call.
        </div>
      ) : (
        tools.map((tool, i) => (
          <div key={i} style={card}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '0.4rem 0.5rem',
              }}
            >
              <button
                type="button"
                onClick={() => setOpen(open === i ? null : i)}
                style={{
                  ...btn,
                  border: 'none',
                  flex: 1,
                  textAlign: 'left',
                  color: 'var(--text)',
                }}
              >
                {open === i ? '▾' : '▸'} {tool.name || '(unnamed)'}
                <span style={{ color: 'var(--text-faint, #666)' }}>
                  {' '}
                  · {Object.keys(tool.parameters).length} param
                  {Object.keys(tool.parameters).length === 1 ? '' : 's'}
                </span>
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                title="Remove this tool"
                style={{ ...btn, color: 'var(--danger, #f87171)' }}
              >
                ✕
              </button>
            </div>
            {open === i && (
              <div style={{ display: 'grid', gap: 8, padding: '0 0.5rem 0.5rem' }}>
                <div>
                  <span style={label}>Name</span>
                  <input
                    value={tool.name}
                    onChange={(e) => patch(i, { name: e.target.value })}
                    style={{ ...input, fontFamily: 'var(--font-mono, monospace)' }}
                  />
                </div>
                <div>
                  <span style={label}>Description — the model reads this</span>
                  <textarea
                    value={tool.description}
                    onChange={(e) => patch(i, { description: e.target.value })}
                    style={{ ...input, minHeight: 46, resize: 'vertical', fontFamily: 'inherit' }}
                  />
                </div>
                <div>
                  <span style={label}>run(args, obs)</span>
                  <CodeEditor
                    value={tool.code}
                    onChange={(v) => patch(i, { code: v })}
                    language="python"
                    minHeight="180px"
                    placeholder={'def run(args, obs):\n    return {}'}
                  />
                </div>
                <ParametersField
                  tool={tool}
                  onChange={(p) => patch(i, { parameters: p.parameters, required: p.required })}
                />
              </div>
            )}
          </div>
        ))
      )}

      <div>
        <button type="button" onClick={add} style={btn}>
          + Add tool
        </button>
      </div>
    </div>
  );
}

/** The JSON-Schema `parameters` map + `required` list. Edited as JSON text so a
 * half-typed edit stays on screen; the parsed value only propagates when valid. */
function ParametersField({
  tool,
  onChange,
}: {
  tool: LoadoutTool;
  onChange: (p: Pick<LoadoutTool, 'parameters' | 'required'>) => void;
}) {
  const [text, setText] = useState(() =>
    JSON.stringify({ parameters: tool.parameters, required: tool.required }, null, 2),
  );
  const [error, setError] = useState<string | null>(null);

  const edit = (v: string) => {
    setText(v);
    try {
      const parsed = JSON.parse(v) as Partial<LoadoutTool>;
      onChange({
        parameters: (parsed.parameters ?? {}) as LoadoutTool['parameters'],
        required: parsed.required ?? [],
      });
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div>
      <span style={label}>Parameters — JSON Schema the model fills in</span>
      <textarea
        value={text}
        onChange={(e) => edit(e.target.value)}
        spellCheck={false}
        style={{
          ...input,
          minHeight: 90,
          resize: 'vertical',
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.72rem',
          borderColor: error ? 'var(--danger, #f87171)' : 'var(--border, #33343a)',
        }}
      />
      {error && (
        <div style={{ fontSize: '0.68rem', color: 'var(--danger, #f87171)', marginTop: 3 }}>
          {error} — last valid value kept
        </div>
      )}
    </div>
  );
}
