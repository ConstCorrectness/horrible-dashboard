/**
 * The model dropdown in the Agent pane's session bar: which model answers *this*
 * agent's turns, switchable without leaving the conversation.
 *
 * **It sets the provider and the model together**, and that is the whole design.
 * The two are separate settings server-side (`agent.<id>.provider` and
 * `agent.<id>.model`, falling back to `agent.orchestrator.*` — see
 * backend/modules/agent/roster.py), and the settings page edits them as two
 * controls because that is what it is for. Here they are one choice, because a
 * model name means nothing on a server that does not have it: picking
 * `minimax/minimax-m3:free` while the provider still says Ollama is not a partial
 * setup, it is a broken one, and it fails mid-turn rather than at the click. An
 * option therefore carries both halves and writing one without the other is not
 * expressible.
 *
 * Options come from `/agent/status`, grouped by provider — so a hosted provider
 * appears here the moment a key is saved (see ApiKeysSettings), and an unreachable
 * provider contributes nothing because it reports no models.
 */
import { useEffect, useState } from 'react';

import { resetSetting, setSetting, useSetting } from '../../settings';
import { getAgentStatus, type AgentStatus, type DetectedProvider } from './api';

/** `provider::model`. A single option value, because the two are chosen together. */
function encode(kind: string, model: string): string {
  return `${kind}::${model}`;
}

export function ModelPicker({
  agentId,
  status,
  disabled,
}: {
  agentId: string;
  /** The pane's own status, reused so the bar does not re-probe every provider.
   * Null while it is still loading or the backend is down. */
  status: AgentStatus | null;
  disabled?: boolean;
}) {
  // `main` predates the roster and keeps the original settings namespace.
  const prefix = agentId === 'main' ? 'agent.orchestrator' : `agent.${agentId}`;
  const PROVIDER_KEY = `${prefix}.provider`;
  const MODEL_KEY = `${prefix}.model`;

  const provider = useSetting<string>(PROVIDER_KEY) ?? '';
  const model = useSetting<string>(MODEL_KEY) ?? '';

  // Falls back to its own fetch only when the pane could not hand one over, so the
  // picker still works in a pane that mounted while the backend was restarting.
  const [own, setOwn] = useState<AgentStatus | null>(null);
  useEffect(() => {
    if (status) return;
    void getAgentStatus()
      .then(setOwn)
      .catch(() => {
        /* backend down — the picker renders nothing rather than an empty list */
      });
  }, [status]);

  const live = status ?? own;
  if (!live) return null;

  const providers = live.providers ?? [];
  const configuredLabel = live.model
    ? `Configured (${live.model})`
    : agentId === 'main'
      ? 'Configured model'
      : 'Orchestrator model';

  // The saved override may name a model the live list does not have — a provider
  // that went down, or a model id typed on the settings page. Keeping it as an
  // option is what stops the select from silently rendering as something else.
  const effective = provider && model ? encode(provider, model) : '';
  const known = providers.some((p) => p.kind === provider && p.models.includes(model));

  const onChange = (value: string): void => {
    if (value === '') {
      void resetSetting(PROVIDER_KEY);
      void resetSetting(MODEL_KEY);
      return;
    }
    const sep = value.indexOf('::');
    void setSetting(PROVIDER_KEY, value.slice(0, sep));
    void setSetting(MODEL_KEY, value.slice(sep + 2));
  };

  return (
    <select
      className="agent-model-picker"
      value={effective}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Model"
      title={
        effective
          ? `${provider} · ${model}`
          : 'Model for this agent — blank uses the one you configured during onboarding'
      }
      disabled={disabled}
    >
      <option value="">{configuredLabel}</option>
      {effective && !known && <option value={effective}>{`${model} (${provider})`}</option>}
      {providers
        .filter((p) => p.models.length > 0)
        .map((p) => (
          <ProviderGroup key={p.kind} provider={p} />
        ))}
    </select>
  );
}

function ProviderGroup({ provider }: { provider: DetectedProvider }) {
  return (
    <optgroup label={provider.label}>
      {[...new Set(provider.models)].map((m) => (
        <option key={m} value={encode(provider.kind, m)}>
          {m}
        </option>
      ))}
    </optgroup>
  );
}
