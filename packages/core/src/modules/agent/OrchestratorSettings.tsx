/**
 * Settings-page section for the agent **orchestrator** (the backend tool-calling
 * loop, distinct from chat/autosuggest). The model override is a dropdown of the
 * configured provider's live models — fetched from `/agent/status` rather than a
 * static enum — with a blank-equivalent "Configured agent model" choice that clears
 * the override. Temperature sits alongside it. Both read/write the scalar settings
 * store (`agent.orchestrator.*`), consumed by backend/modules/agent/orchestrator.py.
 */
import { useEffect, useState } from 'react';

import { isSettingOverridden, resetSetting, setSetting, useSetting } from '../../settings';
import { getAgentStatus } from './api';

const MODEL_KEY = 'agent.orchestrator.model';
const TEMP_KEY = 'agent.orchestrator.temperature';

export function OrchestratorSettings() {
  const model = useSetting<string>(MODEL_KEY) ?? '';
  const temperature = useSetting<number>(TEMP_KEY) ?? 0;
  const tempOverridden = isSettingOverridden(TEMP_KEY);

  const [models, setModels] = useState<string[]>([]);
  const [configuredModel, setConfiguredModel] = useState<string | null>(null);

  useEffect(() => {
    void getAgentStatus()
      .then((s) => {
        setModels(s.available_models ?? []);
        setConfiguredModel(s.model);
      })
      .catch(() => {
        // Provider/backend down — the current override still stays selectable below.
      });
  }, []);

  // Keep an override that isn't in the live list (e.g. provider offline) selectable.
  const options = model && !models.includes(model) ? [model, ...models] : models;

  const onModelChange = (value: string): void => {
    if (value === '') void resetSetting(MODEL_KEY);
    else void setSetting(MODEL_KEY, value);
  };

  return (
    <div className="orchestrator-settings">
      <div className="setting-row">
        <div className="setting-label">
          <label>Orchestrator model override</label>
          <p className="setting-desc">
            Model the orchestrator uses to drive tool calls. A stronger model (e.g. gemma4:12b)
            emits tool calls more reliably than a small one; keep chat/autosuggest on the configured
            model by leaving this on it.
          </p>
        </div>
        <div className="setting-control">
          <select value={model} onChange={(e) => onModelChange(e.target.value)}>
            <option value="">
              Configured agent model{configuredModel ? ` (${configuredModel})` : ''}
            </option>
            {options.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="setting-row">
        <div className="setting-label">
          <label>Orchestrator temperature</label>
          <p className="setting-desc">
            Sampling temperature for the tool-calling loop. Keep near 0 so the model emits
            structured tool calls instead of narrating them.
          </p>
        </div>
        <div className="setting-control">
          <input
            type="number"
            value={temperature}
            step={0.1}
            min={0}
            onChange={(e) => {
              if (e.target.value !== '') void setSetting(TEMP_KEY, e.target.valueAsNumber);
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
    </div>
  );
}
