import { type SettingDecl } from '../../registry';
import { registry } from '../../registry';
import {
  isSecretSet,
  isSecretSetting,
  isSettingOverridden,
  resetSetting,
  setSetting,
  useSetting,
} from '../../settings';

/** One setting row: label + description, a control by type, and a reset link. */
function SettingRow({ decl }: { decl: SettingDecl }) {
  const value = useSetting(decl.key) ?? decl.default;
  const overridden = isSettingOverridden(decl.key);
  // A secret's value is never served back, so the control is write-only: it shows
  // whether something is stored, never what. Reading `value` here would show the
  // blank the server sent and read as "not set".
  const secret = isSecretSetting(decl.key);

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
      control = secret ? (
        <input
          type="password"
          // Uncontrolled on purpose: there is no server-side value to control it
          // with, and a controlled empty input would wipe the field on every
          // unrelated re-render.
          defaultValue=""
          placeholder={isSecretSet(decl.key) ? 'saved — type to replace' : 'not set'}
          onBlur={(e) => {
            if (e.target.value !== '') commit(e.target.value);
          }}
        />
      ) : (
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
 *
 * Within a group, anything marked `advanced` drops into a collapsed **Advanced**
 * fold. That exists because a handful of contributors — the network module above
 * all — declare more infrastructure knobs (TURN credentials, STUN hosts, relay and
 * signalling URLs) than settings a working install ever touches, and a page where
 * the rare and the routine sit at the same weight teaches you to skim past both.
 * The fold is presentation only: an advanced setting is stored, read and reset like
 * any other, and an override survives the fold being shut.
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
      {[...groups.entries()].map(([owner, decls]) => {
        const basic = decls.filter((d) => !d.advanced);
        const advanced = decls.filter((d) => d.advanced);
        return (
          <section key={owner} className="settings-group">
            <h3>{owner}</h3>
            {basic.map((decl) => (
              <SettingRow key={decl.key} decl={decl} />
            ))}
            {advanced.length > 0 && (
              <details className="settings-fold">
                {/* The count is on the summary on purpose: a fold that doesn't say
                    how much it hides is a fold people never open. */}
                <summary>Advanced ({advanced.length})</summary>
                {advanced.map((decl) => (
                  <SettingRow key={decl.key} decl={decl} />
                ))}
              </details>
            )}
          </section>
        );
      })}
      {registry.settingsSections.map((section) => {
        const Section = section.component;
        return (
          <section key={section.id} className="settings-group">
            <h3>{section.title}</h3>
            <Section />
          </section>
        );
      })}
    </div>
  );
}
