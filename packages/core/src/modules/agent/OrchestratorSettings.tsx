/**
 * Settings-page section for the agent **orchestrator** and the roster's
 * specialized agents. An agent selector at the top switches which agent's keys
 * the controls read/write: `main` keeps the original `agent.orchestrator.*` keys
 * (consumed by backend/modules/agent/orchestrator.py); a specialized agent uses
 * `agent.<id>.*`, which fall back to the orchestrator keys server-side
 * (backend/modules/agent/roster.py, `agent_setting`). The model override is a
 * dropdown of the configured provider's live models — fetched from
 * `/agent/status` rather than a static enum — with a blank-equivalent choice
 * that clears the override.
 */
import { useEffect, useState } from 'react';

import { isSettingOverridden, resetSetting, setSetting, useSetting } from '../../settings';
import { getAgentRoster, getAgentStatus, type RosterAgent } from './api';

const MODES = ['default', 'plan', 'acceptEdits', 'autonomous'] as const;

export function OrchestratorSettings() {
  const [roster, setRoster] = useState<RosterAgent[]>([]);
  const [agentId, setAgentId] = useState('main');
  // main predates the roster and keeps its original settings namespace.
  const prefix = agentId === 'main' ? 'agent.orchestrator' : `agent.${agentId}`;

  const PROVIDER_KEY = `${prefix}.provider`;
  const ENDPOINT_KEY = `${prefix}.endpoint`;
  const MODEL_KEY = `${prefix}.model`;
  const TEMP_KEY = `${prefix}.temperature`;
  const CTX_KEY = `${prefix}.contextSize`;
  const MAX_TOKENS_KEY = `${prefix}.maxTokens`;
  const TOP_P_KEY = `${prefix}.topP`;
  const MODE_KEY = `agent.${agentId}.permissionMode`;

  const provider = useSetting<string>(PROVIDER_KEY) ?? '';
  const endpoint = useSetting<string>(ENDPOINT_KEY) ?? '';
  const model = useSetting<string>(MODEL_KEY) ?? '';
  const temperature = useSetting<number>(TEMP_KEY);
  const tempOverridden = isSettingOverridden(TEMP_KEY);

  const contextSize = useSetting<number>(CTX_KEY);
  const ctxOverridden = isSettingOverridden(CTX_KEY);

  const maxTokens = useSetting<number>(MAX_TOKENS_KEY);
  const maxTokensOverridden = isSettingOverridden(MAX_TOKENS_KEY);

  const topP = useSetting<number>(TOP_P_KEY);
  const topPOverridden = isSettingOverridden(TOP_P_KEY);

  const mode = useSetting<string>(MODE_KEY) ?? '';

  const [models, setModels] = useState<string[]>([]);
  const [configuredModel, setConfiguredModel] = useState<string | null>(null);
  const [configuredProvider, setConfiguredProvider] = useState<string | null>(null);
  const [providers, setProviders] = useState<{ kind: string; label: string }[]>([]);

  useEffect(() => {
    void getAgentStatus()
      .then((s) => {
        setModels(s.available_models ?? []);
        setConfiguredModel(s.model);
        setConfiguredProvider(s.provider);
        setProviders((s.providers ?? []).map((p) => ({ kind: p.kind, label: p.label })));
      })
      .catch(() => {
        // Provider/backend down — the current override still stays selectable below.
      });
    void getAgentRoster()
      .then(setRoster)
      .catch(() => {
        // Roster unavailable — the section still edits the orchestrator keys.
      });
  }, []);

  const selected = roster.find((a) => a.id === agentId);
  const isMain = agentId === 'main';
  const fallbackNote = isMain ? '' : ' Blank falls back to the orchestrator’s value.';

  // Keep an override that isn't in the live list (e.g. provider offline) selectable.
  const options = model && !models.includes(model) ? [model, ...models] : models;

  const onModelChange = (value: string): void => {
    if (value === '') void resetSetting(MODEL_KEY);
    else void setSetting(MODEL_KEY, value);
  };

  return (
    <div className="orchestrator-settings">
      {roster.length > 1 && (
        <div className="setting-row">
          <div className="setting-label">
            <label>Agent</label>
            <p className="setting-desc">
              Which roster agent these settings apply to.
              {selected ? ` ${selected.description}` : ''}
            </p>
          </div>
          <div className="setting-control">
            <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              {roster.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      <div className="setting-row">
        <div className="setting-label">
          <label>Provider override</label>
          <p className="setting-desc">
            Which local-model server this agent's turns run against. Blank uses the provider you
            configured during onboarding
            {configuredProvider ? ` (${configuredProvider})` : ''}. Pointing one agent at the node's
            own llama.cpp server while the rest stay on Ollama is the reason this is per-agent — but
            note the <b>model override above must name a model that provider actually serves</b>,
            since a model name means nothing on a server that doesn't have it.
          </p>
        </div>
        <div className="setting-control">
          <select
            value={provider}
            onChange={(e) => {
              if (e.target.value === '') void resetSetting(PROVIDER_KEY);
              else void setSetting(PROVIDER_KEY, e.target.value);
            }}
          >
            <option value="">
              {isMain
                ? `Configured provider${configuredProvider ? ` (${configuredProvider})` : ''}`
                : 'Orchestrator provider'}
            </option>
            {providers.map((p) => (
              <option key={p.kind} value={p.kind}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="setting-row">
        <div className="setting-label">
          <label>Endpoint override</label>
          <p className="setting-desc">
            Base URL for this agent's provider. Blank is almost always right — it uses the
            provider's default, and a llama.cpp server this app spawned advertises its real port
            even when it had to move off the default one.
          </p>
        </div>
        <div className="setting-control">
          <input
            type="text"
            value={endpoint}
            placeholder="Provider default"
            onChange={(e) => {
              if (e.target.value.trim() !== '')
                void setSetting(ENDPOINT_KEY, e.target.value.trim());
              else void resetSetting(ENDPOINT_KEY);
            }}
          />
        </div>
      </div>

      <div className="setting-row">
        <div className="setting-label">
          <label>Model override</label>
          <p className="setting-desc">
            Model this agent uses to drive tool calls. A stronger model (e.g. gemma4:12b) emits tool
            calls more reliably than a small one.{fallbackNote}
          </p>
        </div>
        <div className="setting-control">
          <select value={model} onChange={(e) => onModelChange(e.target.value)}>
            <option value="">
              {isMain
                ? `Configured agent model${configuredModel ? ` (${configuredModel})` : ''}`
                : 'Orchestrator model'}
            </option>
            {options.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!isMain && (
        <div className="setting-row">
          <div className="setting-label">
            <label>Permission mode</label>
            <p className="setting-desc">
              The permission mode this agent's turns run under. Blank uses the agent's built-in
              default{selected?.default_mode ? ` (${selected.default_mode})` : ''}, else your
              session mode. Explicit allow/ask/deny rules always apply.
            </p>
          </div>
          <div className="setting-control">
            <select
              value={mode}
              onChange={(e) => {
                if (e.target.value === '') void resetSetting(MODE_KEY);
                else void setSetting(MODE_KEY, e.target.value);
              }}
            >
              <option value="">Agent default</option>
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      <div className="setting-row">
        <div className="setting-label">
          <label>Temperature</label>
          <p className="setting-desc">
            Sampling temperature for the tool-calling loop. Keep near 0 so the model emits
            structured tool calls instead of narrating them.{fallbackNote}
          </p>
        </div>
        <div className="setting-control">
          <input
            type="number"
            value={temperature ?? (isMain ? 0 : '')}
            step={0.1}
            min={0}
            placeholder={isMain ? undefined : 'Orchestrator value'}
            onChange={(e) => {
              if (e.target.value !== '') void setSetting(TEMP_KEY, e.target.valueAsNumber);
              else if (!isMain) void resetSetting(TEMP_KEY);
            }}
          />
          {tempOverridden && (
            <button
              className="setting-reset"
              title="Reset to default"
              onClick={() => void resetSetting(TEMP_KEY)}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      <div className="setting-row">
        <div className="setting-label">
          <label>Context size</label>
          <p className="setting-desc">
            Maximum context window (tokens) for this agent's turns. For Ollama, this maps to the
            num_ctx option.{fallbackNote}
          </p>
        </div>
        <div className="setting-control">
          <input
            type="number"
            value={contextSize ?? ''}
            min={1}
            placeholder={isMain ? 'Default' : 'Orchestrator value'}
            onChange={(e) => {
              if (e.target.value !== '') void setSetting(CTX_KEY, e.target.valueAsNumber);
              else void resetSetting(CTX_KEY);
            }}
          />
          {ctxOverridden && (
            <button
              className="setting-reset"
              title="Reset to default"
              onClick={() => void resetSetting(CTX_KEY)}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      <div className="setting-row">
        <div className="setting-label">
          <label>Max output tokens</label>
          <p className="setting-desc">
            Maximum tokens the model can generate in a single turn. Maps to max_tokens for OpenAI
            and num_predict for Ollama.{fallbackNote}
          </p>
        </div>
        <div className="setting-control">
          <input
            type="number"
            value={maxTokens ?? ''}
            min={1}
            placeholder={isMain ? 'Default' : 'Orchestrator value'}
            onChange={(e) => {
              if (e.target.value !== '') void setSetting(MAX_TOKENS_KEY, e.target.valueAsNumber);
              else void resetSetting(MAX_TOKENS_KEY);
            }}
          />
          {maxTokensOverridden && (
            <button
              className="setting-reset"
              title="Reset to default"
              onClick={() => void resetSetting(MAX_TOKENS_KEY)}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      <div className="setting-row">
        <div className="setting-label">
          <label>Top P</label>
          <p className="setting-desc">
            Top P sampling (nucleus sampling) threshold. Keep blank/default to let the provider
            decide.{fallbackNote}
          </p>
        </div>
        <div className="setting-control">
          <input
            type="number"
            value={topP ?? ''}
            step={0.05}
            min={0}
            max={1}
            placeholder={isMain ? 'Default' : 'Orchestrator value'}
            onChange={(e) => {
              if (e.target.value !== '') void setSetting(TOP_P_KEY, e.target.valueAsNumber);
              else void resetSetting(TOP_P_KEY);
            }}
          />
          {topPOverridden && (
            <button
              className="setting-reset"
              title="Reset to default"
              onClick={() => void resetSetting(TOP_P_KEY)}
            >
              Reset
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
