import { useCallback, useEffect, useState, type CSSProperties } from 'react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../api';
import { registry } from '../../../registry';
import type { EditorService } from '../../editor/service';
import { fetchGamesCatalog } from '../games-api';
import { DryRunSection } from './DryRunSection';

/** The editor's buffer surface, looked up lazily (the editor module registers it
 * at load). Undefined only if the editor module never loaded — the harness panel
 * then simply hides its "edit in editor" affordances. */
const editor = (): EditorService | undefined => registry.getService<EditorService>('editor');

const labelStyle: CSSProperties = {
  display: 'block',
  fontSize: '0.7rem',
  color: 'var(--text-dim)',
};

/**
 * The **agent harness editor** — where the skill of this game actually lives. A
 * player writes their agent's strategy `context` and a set of custom tools (real
 * Python `run(args, obs)` functions) that the agent may call while deciding a move.
 * Better tools ⇒ a better agent. Tools run only on this node and only ever see this
 * seat's observation. See docs/modules/games.mdx (agent harness).
 */

interface ToolDef {
  name: string;
  description: string;
  code: string;
  parameters: Record<string, unknown>;
  required: string[];
}
interface ModelConfig {
  provider: 'anthropic' | 'openai' | 'ollama';
  model: string;
  endpoint?: string | null;
  api_key_name?: string | null;
}
interface LoadoutModel {
  game_id: string;
  context: string;
  tools: ToolDef[];
  /** null = borrow the agent module's configured model. */
  model: ModelConfig | null;
}
interface VersionInfo {
  id: string;
  label: string;
  created_at: number;
  active: boolean;
}
type VersionStats = Record<string, { win: number; loss: number; draw: number }>;

// `default` is the fallback harness used when a game has no game-specific loadout.
const DEFAULT_GAMES = [
  { id: 'tictactoe', name: 'Tic-Tac-Toe' },
  { id: 'default', name: 'default' },
];

const STARTER_CODE = `def run(args, obs):
    # obs = your seat's observation (e.g. obs["board"] for tic-tac-toe).
    # args = the arguments the model passed. Return anything JSON-serializable.
    return {"note": "describe what this tool computes"}
`;

function newTool(n: number): ToolDef {
  return {
    name: `helper_${n}`,
    description: 'What this tool computes, so the agent knows when to call it.',
    code: STARTER_CODE,
    parameters: {},
    required: [],
  };
}

// Mirrors backend/modules/games/loadout.py `tool_name_error` — same rule, checked
// live in the editor so a bad name is flagged before Save.
const TOOL_NAME_RE = /^[A-Za-z_][A-Za-z0-9_.-]*$/;

function toolNameError(name: string, taken: string[]): string | null {
  if (!name) return 'tool name is empty';
  if (!TOOL_NAME_RE.test(name))
    return 'must start with a letter or _ and use only letters, digits, _ . -';
  if (name.startsWith('game.')) return 'the game.* namespace is reserved for built-in tools';
  if (taken.includes(name)) return `duplicate tool name "${name}"`;
  return null;
}

const PARAM_TYPES = ['string', 'number', 'boolean', 'object', 'array'];

/** Row editor for a tool's `parameters`/`required` — the argument schema the
 * MODEL fills in when calling the tool (`args` in `run(args, obs)`). Edits patch
 * only `type`/`description` so hand-authored extras (e.g. `enum`) survive. */
function ParamsEditor({
  tool,
  onChange,
}: {
  tool: ToolDef;
  onChange: (patch: Partial<ToolDef>) => void;
}) {
  const entries = Object.entries(tool.parameters) as [string, Record<string, unknown>][];

  const rename = (oldName: string, newName: string) => {
    const parameters: Record<string, unknown> = {};
    for (const [k, v] of entries) parameters[k === oldName ? newName : k] = v;
    onChange({
      parameters,
      required: tool.required.map((r) => (r === oldName ? newName : r)),
    });
  };

  const patchParam = (name: string, patch: Record<string, unknown>) => {
    const current = (tool.parameters[name] ?? {}) as Record<string, unknown>;
    onChange({ parameters: { ...tool.parameters, [name]: { ...current, ...patch } } });
  };

  const remove = (name: string) => {
    const parameters = { ...tool.parameters };
    delete parameters[name];
    onChange({ parameters, required: tool.required.filter((r) => r !== name) });
  };

  const setRequired = (name: string, on: boolean) => {
    const rest = tool.required.filter((r) => r !== name);
    onChange({ required: on ? [...rest, name] : rest });
  };

  const add = () => {
    let n = entries.length + 1;
    while (`arg_${n}` in tool.parameters) n += 1;
    patchParam(`arg_${n}`, { type: 'string', description: '' });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        <span style={labelStyle}>Arguments the model passes (args)</span>
        <button type="button" onClick={add} style={{ fontSize: '0.72rem' }}>
          + arg
        </button>
        {entries.length === 0 && (
          <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
            none — the model calls this tool bare
          </span>
        )}
      </div>
      {entries.map(([name, spec], i) => (
        <div key={i} style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
          <input
            value={name}
            onChange={(e) => rename(name, e.target.value)}
            placeholder="arg name"
            style={{ fontFamily: 'monospace', flex: '0 0 8rem' }}
          />
          <select
            value={String(spec.type ?? 'string')}
            onChange={(e) => patchParam(name, { type: e.target.value })}
          >
            {PARAM_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <input
            value={String(spec.description ?? '')}
            onChange={(e) => patchParam(name, { description: e.target.value })}
            placeholder="what the model should pass here"
            style={{ flex: 1 }}
          />
          <label
            style={{ display: 'flex', alignItems: 'center', gap: '0.2rem', fontSize: '0.72rem' }}
          >
            <input
              type="checkbox"
              checked={tool.required.includes(name)}
              onChange={(e) => setRequired(name, e.target.checked)}
            />
            required
          </label>
          <button type="button" onClick={() => remove(name)}>
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

/** Static explainer: how the loadout actually drives a turn. Content mirrors
 * backend/modules/games/policy.py — update both if the loop changes. */
function HarnessExplainer() {
  return (
    <details className="games-harness-help" style={{ fontSize: '0.8rem' }}>
      <summary style={{ cursor: 'pointer' }}>ℹ️ How the harness works</summary>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.35rem',
          padding: '0.4rem 0 0.2rem 1rem',
          color: 'var(--text-dim)',
        }}
      >
        <div>
          <strong>The loop.</strong> On your agent's turn the server sends an observation and the
          legal actions. Your <em>strategy context</em> goes into the model's system prompt, and
          every tool below is offered to it. The model may call your tools for up to{' '}
          <strong>6 rounds</strong> to analyze the position, then must commit a move with the
          built-in <code>game.chooseAction</code>. In a real match any failure quietly falls back to
          a random legal move — the Dry run below shows the failure instead.
        </div>
        <div>
          <strong>Multiple tools are encouraged.</strong> Every tool is advertised on every round
          and the model picks which (if any) to call — it can chain them, e.g. a scanner first, then
          a fork finder. The model won't know your intended order: teach it in the context
          (&quot;call X first, then Y&quot;).
        </div>
        <div>
          <strong>The contract.</strong> Each tool is Python defining <code>run(args, obs)</code>:{' '}
          <code>args</code> = the arguments the model passed, <code>obs</code> = this seat's
          observation. Return anything JSON-serializable; raising shows the model{' '}
          <code>{'{"error": ...}'}</code>. A tool that doesn't compile is simply absent in a match —
          Save reports it here.
        </div>
        <div>
          <strong>Arguments.</strong> The &quot;args&quot; rows declare what the model fills in when
          calling (name, type, description; <em>required</em> makes it mandatory). No rows = the
          tool is called bare and should read everything from <code>obs</code>.
        </div>
        <div>
          <strong>The model.</strong> Each harness can bring its own model (part of the skill — the
          ladder records it), or borrow the agent module's configured model (&quot;agent
          default&quot;).
        </div>
      </div>
    </details>
  );
}

/** Which model drives this harness — part of the loadout, so part of the skill.
 * API keys go into the node's key store write-only; only names come back. */
function ModelSection({
  model,
  onChange,
}: {
  model: ModelConfig | null;
  onChange: (m: ModelConfig | null) => void;
}) {
  const [keyNames, setKeyNames] = useState<string[]>([]);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyValue, setNewKeyValue] = useState('');
  const [note, setNote] = useState('');

  const loadKeys = useCallback(() => {
    apiGet<{ names: string[] }>('/games/keys')
      .then((r) => setKeyNames(r.names))
      .catch(() => setKeyNames([]));
  }, []);
  useEffect(() => loadKeys(), [loadKeys]);

  const addKey = async () => {
    if (!newKeyName || !newKeyValue) return;
    await apiPut(`/games/keys/${encodeURIComponent(newKeyName)}`, { value: newKeyValue });
    setNote(`key "${newKeyName}" stored on this node (write-only)`);
    setNewKeyName('');
    setNewKeyValue('');
    loadKeys();
  };

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: '4px',
        padding: '0.5rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.35rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong>Model</strong>
        <select
          value={model?.provider ?? 'agent'}
          onChange={(e) => {
            const p = e.target.value;
            if (p === 'agent') onChange(null);
            else
              onChange({
                provider: p as ModelConfig['provider'],
                model: model?.model ?? '',
                endpoint: null,
                api_key_name: model?.api_key_name ?? null,
              });
          }}
        >
          <option value="agent">agent default (node's local model)</option>
          <option value="ollama">Ollama</option>
          <option value="openai">OpenAI-compatible</option>
          <option value="anthropic">Anthropic</option>
        </select>
        {model && (
          <>
            <input
              value={model.model}
              onChange={(e) => onChange({ ...model, model: e.target.value })}
              placeholder={model.provider === 'anthropic' ? 'claude-sonnet-5' : 'model name'}
              style={{ fontFamily: 'monospace', flex: '0 0 14rem' }}
            />
            <input
              value={model.endpoint ?? ''}
              onChange={(e) => onChange({ ...model, endpoint: e.target.value || null })}
              placeholder="endpoint (default)"
              style={{ fontFamily: 'monospace', flex: '0 0 12rem' }}
            />
            {model.provider !== 'ollama' && (
              <select
                value={model.api_key_name ?? ''}
                onChange={(e) => onChange({ ...model, api_key_name: e.target.value || null })}
              >
                <option value="">no API key</option>
                {keyNames.map((n) => (
                  <option key={n} value={n}>
                    🔑 {n}
                  </option>
                ))}
              </select>
            )}
          </>
        )}
      </div>
      {model && model.provider !== 'ollama' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
          <span style={labelStyle}>Add key:</span>
          <input
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="key name"
            style={{ flex: '0 0 8rem' }}
          />
          <input
            value={newKeyValue}
            onChange={(e) => setNewKeyValue(e.target.value)}
            placeholder="paste key (stored node-side, never shown again)"
            type="password"
            style={{ flex: 1, minWidth: '10rem' }}
          />
          <button type="button" onClick={() => void addKey()}>
            Store
          </button>
          <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>{note}</span>
        </div>
      )}
    </div>
  );
}

export function LoadoutPanel() {
  const [gameId, setGameId] = useState('tictactoe');
  const [games, setGames] = useState(DEFAULT_GAMES);
  const [loadout, setLoadout] = useState<LoadoutModel | null>(null);
  const [sampleObs, setSampleObs] = useState(
    '{"board": [null,null,null,null,null,null,null,null,null]}',
  );
  const [results, setResults] = useState<Record<number, string>>({});
  const [status, setStatus] = useState('');
  // Tool code opened as an editor buffer, keyed by `${gameId}:${tool name}` so the
  // link survives list reorders (delete/add) but not renames.
  const [editorUris, setEditorUris] = useState<Record<string, string>>({});
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [stats, setStats] = useState<VersionStats>({});
  const [versionLabel, setVersionLabel] = useState('');
  // Per-tool problems from the last Save (`/games/loadout/validate`): a broken
  // tool is silently absent in a live match, so surface it here instead.
  const [diagnostics, setDiagnostics] = useState<Record<string, string>>({});
  // Which tool cards are expanded (index-aligned with loadout.tools; adjusted on
  // add/delete). Loaded tools start collapsed; new tools open; a Save that finds
  // problems force-opens the offenders.
  const [openTools, setOpenTools] = useState<boolean[]>([]);

  const loadVersions = useCallback(() => {
    apiGet<{ versions: VersionInfo[]; stats: VersionStats }>(`/games/loadout/${gameId}/versions`)
      .then((r) => {
        setVersions(r.versions);
        setStats(r.stats);
      })
      .catch(() => setVersions([]));
  }, [gameId]);
  useEffect(() => loadVersions(), [loadVersions]);

  useEffect(() => {
    // Catalog games + the AgentTown persona (the town isn't a table game, but its
    // resident's personality is this loadout's context) + the `default` fallback.
    fetchGamesCatalog().then((catalog) =>
      setGames([
        ...catalog,
        { id: 'town', name: 'AgentTown persona' },
        { id: 'default', name: 'default' },
      ]),
    );
  }, []);

  useEffect(() => {
    setStatus('loading…');
    setDiagnostics({});
    apiGet<LoadoutModel>(`/games/loadout/${gameId}`)
      .then((l) => {
        setLoadout(l);
        setOpenTools(l.tools.map(() => false));
        setStatus('');
      })
      .catch((e) => setStatus(String(e)));
  }, [gameId]);

  if (!loadout) {
    return <div style={{ padding: '0.6rem', fontSize: '0.85rem' }}>{status || 'loading…'}</div>;
  }

  const update = (patch: Partial<LoadoutModel>) => setLoadout({ ...loadout, ...patch });
  const updateTool = (i: number, patch: Partial<ToolDef>) =>
    update({ tools: loadout.tools.map((t, j) => (j === i ? { ...t, ...patch } : t)) });

  const save = async () => {
    setStatus('saving…');
    try {
      await apiPut(`/games/loadout/${gameId}`, { ...loadout, game_id: gameId });
      // Save never blocks on problems (WIP harnesses are normal; matches degrade
      // gracefully), but the diagnostics land next to each tool.
      let suffix = '';
      try {
        const v = await apiPost<{
          ok: boolean;
          tools: { name: string; ok: boolean; error: string | null }[];
        }>('/games/loadout/validate', { ...loadout, game_id: gameId });
        const bad: Record<string, string> = {};
        for (const t of v.tools) if (!t.ok && t.error) bad[t.name] = t.error;
        setDiagnostics(bad);
        // A collapsed card hides its problem — force the offenders open.
        setOpenTools((prev) => loadout.tools.map((t, i) => prev[i] || t.name in bad));
        const n = Object.keys(bad).length;
        if (n > 0) suffix = ` — ${n} tool${n > 1 ? 's have' : ' has'} problems`;
      } catch {
        setDiagnostics({});
      }
      setStatus(`saved ✓${suffix}`);
      loadVersions();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const saveAsVersion = async () => {
    setStatus('branching…');
    try {
      const r = await apiPost<{ version_id: string }>(`/games/loadout/${gameId}/versions`, {
        label: versionLabel,
        loadout: { ...loadout, game_id: gameId },
      });
      setStatus(`saved as ${versionLabel || r.version_id} ✓`);
      setVersionLabel('');
      loadVersions();
    } catch (e) {
      setStatus(String(e));
    }
  };

  const activate = async (versionId: string) => {
    await apiPut(`/games/loadout/${gameId}/active`, { version_id: versionId });
    const l = await apiGet<LoadoutModel>(`/games/loadout/${gameId}`);
    setLoadout(l);
    loadVersions();
  };

  const removeVersion = async (versionId: string) => {
    await apiDelete(`/games/loadout/${gameId}/versions/${versionId}`);
    const l = await apiGet<LoadoutModel>(`/games/loadout/${gameId}`);
    setLoadout(l);
    loadVersions();
  };

  const active = versions.find((v) => v.active);

  // Open a tool's code as a real Python buffer in the editor module (syntax
  // highlighting, LSP), then pull the edited content back into the loadout.
  const editInEditor = async (i: number) => {
    const svc = editor();
    if (!svc) return;
    const t = loadout.tools[i];
    const uri = await svc.openBufferFromContent({
      content: t.code,
      language: 'python',
      title: `harness · ${gameId} · ${t.name}`,
    });
    setEditorUris((prev) => ({ ...prev, [`${gameId}:${t.name}`]: uri }));
  };

  const pullFromEditor = async (i: number) => {
    const svc = editor();
    if (!svc) return;
    const t = loadout.tools[i];
    const uri = editorUris[`${gameId}:${t.name}`];
    if (!uri) return;
    const content = await svc.getBufferContent(uri);
    if (content !== null) {
      updateTool(i, { code: content });
      setResults({ ...results, [i]: 'pulled from editor — Save to persist' });
    } else {
      setResults({ ...results, [i]: 'editor buffer is gone' });
    }
  };

  const test = async (i: number) => {
    let obs: unknown = {};
    try {
      obs = JSON.parse(sampleObs);
    } catch {
      setResults({ ...results, [i]: 'sample observation is not valid JSON' });
      return;
    }
    try {
      const r = await apiPost<{ ok: boolean; result: unknown; error: string | null }>(
        '/games/test-tool',
        { code: loadout.tools[i].code, args: {}, obs },
      );
      setResults({ ...results, [i]: r.ok ? `→ ${JSON.stringify(r.result)}` : `error: ${r.error}` });
    } catch (e) {
      setResults({ ...results, [i]: String(e) });
    }
  };

  return (
    <div
      style={{
        padding: '0.6rem',
        fontSize: '0.85rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        overflow: 'auto',
        height: '100%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span>Harness for</span>
        <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
          {games.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button type="button" onClick={save}>
          Save
        </button>
        <span style={{ color: 'var(--text-dim)' }}>{status}</span>
      </div>

      <HarnessExplainer />

      <label>
        <span style={labelStyle}>Strategy context (injected into the agent's system prompt)</span>
        <textarea
          value={loadout.context}
          onChange={(e) => update({ context: e.target.value })}
          spellCheck={false}
          placeholder="e.g. Prefer the center, then corners. Block the opponent's two-in-a-row."
          style={{ width: '100%', minHeight: '3rem', fontFamily: 'inherit' }}
        />
      </label>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <strong>Custom tools</strong>
        <button
          type="button"
          onClick={() => {
            update({ tools: [...loadout.tools, newTool(loadout.tools.length + 1)] });
            setOpenTools((prev) => [...prev, true]);
          }}
        >
          + Add tool
        </button>
      </div>

      {loadout.tools.map((t, i) => {
        const nameError = toolNameError(
          t.name,
          loadout.tools.slice(0, i).map((x) => x.name),
        );
        const diagnostic = diagnostics[t.name];
        const problem = nameError ?? diagnostic;
        return (
          <details
            key={i}
            className="games-tool-card"
            open={openTools[i] ?? false}
            onToggle={(e) => {
              const open = (e.target as HTMLDetailsElement).open;
              setOpenTools((prev) => prev.map((o, j) => (j === i ? open : o)));
            }}
            style={{
              border: '1px solid var(--border)',
              borderRadius: '4px',
              padding: '0.15rem 0.5rem 0.35rem',
            }}
          >
            <summary>
              <code>{t.name || '(unnamed tool)'}</code>
              {t.description && <span className="games-tool-summary-desc">{t.description}</span>}
              <span
                className="games-tool-status"
                title={problem ?? 'no problems found at the last save'}
                style={problem ? { color: '#e5534b' } : { color: 'var(--text-dim)' }}
              >
                {problem ? '⚠' : '✓'}
              </span>
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                <input
                  value={t.name}
                  onChange={(e) => updateTool(i, { name: e.target.value })}
                  placeholder="tool_name"
                  style={{
                    fontFamily: 'monospace',
                    flex: '0 0 12rem',
                    ...(nameError ? { border: '1px solid #e5534b' } : {}),
                  }}
                />
                <input
                  value={t.description}
                  onChange={(e) => updateTool(i, { description: e.target.value })}
                  placeholder="description"
                  style={{ flex: 1 }}
                />
                <button
                  type="button"
                  onClick={() => {
                    update({ tools: loadout.tools.filter((_, j) => j !== i) });
                    setOpenTools((prev) => prev.filter((_, j) => j !== i));
                  }}
                >
                  ✕
                </button>
              </div>
              {nameError && (
                <div style={{ color: '#e5534b', fontSize: '0.72rem' }}>⚠ {nameError}</div>
              )}
              {!nameError && diagnostic && (
                <div style={{ color: '#e5534b', fontSize: '0.72rem' }}>⚠ {diagnostic}</div>
              )}
              <textarea
                value={t.code}
                onChange={(e) => updateTool(i, { code: e.target.value })}
                spellCheck={false}
                style={{
                  width: '100%',
                  minHeight: '6rem',
                  fontFamily: 'monospace',
                  fontSize: '0.75rem',
                }}
              />
              <ParamsEditor tool={t} onChange={(patch) => updateTool(i, patch)} />
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <button type="button" onClick={() => test(i)}>
                  Test
                </button>
                {editor() && (
                  <>
                    <button type="button" onClick={() => void editInEditor(i)}>
                      Edit in editor ↗
                    </button>
                    {editorUris[`${gameId}:${t.name}`] && (
                      <button type="button" onClick={() => void pullFromEditor(i)}>
                        ↙ Pull
                      </button>
                    )}
                  </>
                )}
                <code style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
                  {results[i] ?? ''}
                </code>
              </div>
            </div>
          </details>
        );
      })}

      <DryRunSection
        gameId={gameId}
        loadout={loadout}
        engineGames={games.filter((g) => g.id !== 'town' && g.id !== 'default')}
      />

      {/* Everything that isn't day-to-day authoring lives behind one fold. */}
      <details className="games-advanced">
        <summary>
          Advanced —{' '}
          <span style={{ color: 'var(--text-dim)' }}>
            model:{' '}
            {loadout.model?.model
              ? `${loadout.model.provider} · ${loadout.model.model}`
              : 'agent default'}{' '}
            · version: {active?.label ?? 'v1 (unsaved)'}
          </span>
        </summary>
        <div
          style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', paddingTop: '0.4rem' }}
        >
          {/* Version bar — the harness progression loop: play, study, branch, requeue. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
            <span style={labelStyle}>Version</span>
            <select value={active?.id ?? ''} onChange={(e) => void activate(e.target.value)}>
              {versions.length === 0 && <option value="">v1 (unsaved)</option>}
              {versions.map((v) => {
                const s = stats[v.id];
                const record = s ? ` — ${s.win}W/${s.loss}L/${s.draw}D` : '';
                return (
                  <option key={v.id} value={v.id}>
                    {v.label}
                    {record}
                  </option>
                );
              })}
            </select>
            <input
              value={versionLabel}
              onChange={(e) => setVersionLabel(e.target.value)}
              placeholder="new version label"
              style={{ flex: '0 0 11rem' }}
            />
            <button type="button" onClick={() => void saveAsVersion()} title="Branch this harness">
              Save as new version
            </button>
            {active && versions.length > 1 && (
              <button
                type="button"
                onClick={() => void removeVersion(active.id)}
                title="Delete this version"
              >
                🗑
              </button>
            )}
            {active && stats[active.id] && (
              <span className="games-tier-chip" title="this version's record">
                {stats[active.id].win}W · {stats[active.id].loss}L · {stats[active.id].draw}D
              </span>
            )}
          </div>

          <ModelSection model={loadout.model} onChange={(m) => update({ model: m })} />

          <div>
            <span style={labelStyle}>Sample observation (JSON) — for testing tools</span>
            <textarea
              value={sampleObs}
              onChange={(e) => setSampleObs(e.target.value)}
              spellCheck={false}
              style={{
                width: '100%',
                minHeight: '2.2rem',
                fontFamily: 'monospace',
                fontSize: '0.75rem',
              }}
            />
          </div>
        </div>
      </details>
    </div>
  );
}
