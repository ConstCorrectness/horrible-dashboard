/**
 * Settings-page section for the agent permission system: the default mode and the
 * allow/ask/deny rule lists. Rules are arrays, so this reads/writes the backend
 * settings store directly (`/api/settings`) rather than the scalar-typed settings
 * store. "Always allow" from an approval prompt appends to the allow list
 * server-side; reopening (or Refresh) shows it here. See
 * docs/architecture/agent-tools.md.
 */
import { useEffect, useState, type FormEvent } from 'react';

import { apiGet, apiPut } from '../../api';

const MODES: { value: string; label: string; hint: string }[] = [
  { value: 'default', label: 'Default', hint: 'Prompt on every side effect not already allowed' },
  { value: 'plan', label: 'Plan', hint: 'Read-only: deny every side effect' },
  { value: 'acceptEdits', label: 'Accept edits', hint: 'Auto-allow saves & safe creation' },
  {
    value: 'autonomous',
    label: 'Autonomous',
    hint: 'Allow all but ask/deny rules + circuit breakers',
  },
];

const LISTS: { key: 'allow' | 'ask' | 'deny'; label: string }[] = [
  { key: 'allow', label: 'Allow' },
  { key: 'ask', label: 'Ask' },
  { key: 'deny', label: 'Deny' },
];

const MODE_KEY = 'agent.permissions.mode';
const listKey = (name: string) => `agent.permissions.${name}`;

interface SettingsValues {
  values: Record<string, unknown>;
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

type ListName = 'allow' | 'ask' | 'deny';
type Rules = Record<ListName, string[]>;
type Drafts = Record<ListName, string>;

export function PermissionsSettings() {
  const [mode, setMode] = useState('default');
  const [rules, setRules] = useState<Rules>({ allow: [], ask: [], deny: [] });
  const [drafts, setDrafts] = useState<Drafts>({ allow: '', ask: '', deny: '' });

  const load = async (): Promise<void> => {
    try {
      const res = await apiGet<SettingsValues>('/settings');
      const v = res.values ?? {};
      setMode(typeof v[MODE_KEY] === 'string' ? (v[MODE_KEY] as string) : 'default');
      setRules({
        allow: asList(v[listKey('allow')]),
        ask: asList(v[listKey('ask')]),
        deny: asList(v[listKey('deny')]),
      });
    } catch {
      // Backend down — leave defaults.
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const saveMode = async (m: string): Promise<void> => {
    setMode(m);
    await apiPut(`/settings/${MODE_KEY}`, { value: m });
  };

  const saveList = async (name: ListName, list: string[]): Promise<void> => {
    setRules((r) => ({ ...r, [name]: list }));
    await apiPut(`/settings/${listKey(name)}`, { value: list });
  };

  const addRule = (name: ListName) => (e: FormEvent) => {
    e.preventDefault();
    const draft = drafts[name].trim();
    if (!draft || rules[name].includes(draft)) return;
    void saveList(name, [...rules[name], draft]);
    setDrafts((d) => ({ ...d, [name]: '' }));
  };

  return (
    <div className="permissions-settings">
      <div className="setting-row">
        <div className="setting-label">
          <label>Default mode</label>
          <p className="setting-desc">{MODES.find((m) => m.value === mode)?.hint}</p>
        </div>
        <div className="setting-control">
          <select value={mode} onChange={(e) => void saveMode(e.target.value)}>
            {MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {LISTS.map(({ key, label }) => (
        <div key={key} className="permissions-list">
          <h4>
            {label} rules <button onClick={() => void load()}>Refresh</button>
          </h4>
          {rules[key].length === 0 && (
            <p className="setting-desc">No {label.toLowerCase()} rules.</p>
          )}
          <ul>
            {rules[key].map((rule) => (
              <li key={rule}>
                <code>{rule}</code>
                <button
                  title="Remove rule"
                  onClick={() =>
                    void saveList(
                      key,
                      rules[key].filter((r) => r !== rule),
                    )
                  }
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          <form onSubmit={addRule(key)}>
            <input
              value={drafts[key]}
              placeholder="e.g. terminal.exec(npm run *)"
              onChange={(e) => setDrafts((d) => ({ ...d, [key]: e.target.value }))}
            />
            <button type="submit">Add</button>
          </form>
        </div>
      ))}
    </div>
  );
}
