import { useCallback, useEffect, useState, type CSSProperties } from 'react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../api';
import { registry } from '../../../registry';
import type { EditorService } from '../../editor/service';
import { fetchGamesCatalog } from '../games-api';

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
    apiGet<LoadoutModel>(`/games/loadout/${gameId}`)
      .then((l) => {
        setLoadout(l);
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
      setStatus('saved ✓');
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

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <strong>Custom tools</strong>
        <button
          type="button"
          onClick={() => update({ tools: [...loadout.tools, newTool(loadout.tools.length + 1)] })}
        >
          + Add tool
        </button>
      </div>

      {loadout.tools.map((t, i) => (
        <div
          key={i}
          style={{
            border: '1px solid var(--border)',
            borderRadius: '4px',
            padding: '0.5rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.35rem',
          }}
        >
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            <input
              value={t.name}
              onChange={(e) => updateTool(i, { name: e.target.value })}
              placeholder="tool_name"
              style={{ fontFamily: 'monospace', flex: '0 0 12rem' }}
            />
            <input
              value={t.description}
              onChange={(e) => updateTool(i, { description: e.target.value })}
              placeholder="description"
              style={{ flex: 1 }}
            />
            <button
              type="button"
              onClick={() => update({ tools: loadout.tools.filter((_, j) => j !== i) })}
            >
              ✕
            </button>
          </div>
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
      ))}
    </div>
  );
}
