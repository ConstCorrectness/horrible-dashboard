import { type SettingDecl } from '../../registry';
import { registry } from '../../registry';
import { isSettingOverridden, resetSetting, setSetting, useSetting } from '../../settings';

/** One setting row: label + description, a control by type, and a reset link. */
function SettingRow({ decl }: { decl: SettingDecl }) {
  const value = useSetting(decl.key) ?? decl.default;
  const overridden = isSettingOverridden(decl.key);

  const commit = (v: string | number | boolean) => {
    void setSetting(decl.key, v);
  };

  let control: React.ReactNode;
  switch (decl.type) {
    case 'boolean':
      control = (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => commit(e.target.checked)}
        />
      );
      break;
    case 'number':
      control = (
        <input
          type="number"
          value={Number(value)}
          onChange={(e) => {
            if (e.target.value !== '') commit(e.target.valueAsNumber);
          }}
        />
      );
      break;
    case 'enum':
      control = (
        <select value={String(value)} onChange={(e) => commit(e.target.value)}>
          {(decl.enumValues ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      );
      break;
    default:
      control = (
        <input type="text" value={String(value)} onChange={(e) => commit(e.target.value)} />
      );
  }

  return (
    <div className="setting-row">
      <div className="setting-label">
        <label>{decl.title}</label>
        {decl.description && <p className="setting-desc">{decl.description}</p>}
      </div>
      <div className="setting-control">
        {control}
        {overridden && (
          <button
            className="setting-reset"
            title="Reset to default"
            onClick={() => void resetSetting(decl.key)}
          >
            Reset
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * The settings page: every setting declared by a module or plugin, grouped by
 * the contributor that declared it (VS Code Settings editor style).
 */
export function SettingsPanel() {
  const settings = registry.settings;

  // Group by owning module title, preserving declaration order within a group.
  const groups = new Map<string, SettingDecl[]>();
  for (const decl of settings) {
    const owner = registry.settingOwner(decl.key) ?? 'Other';
    const list = groups.get(owner) ?? [];
    list.push(decl);
    groups.set(owner, list);
  }

  return (
    <div className="settings-page">
      <h2>Settings</h2>
      {settings.length === 0 && (
        <p className="dashboard-hint">
          No settings yet — modules and plugins contribute their own here.
        </p>
      )}
      {[...groups.entries()].map(([owner, decls]) => (
        <section key={owner} className="settings-group">
          <h3>{owner}</h3>
          {decls.map((decl) => (
            <SettingRow key={decl.key} decl={decl} />
          ))}
        </section>
      ))}
    </div>
  );
}
