import { useCallback, useMemo, useRef, useState } from 'react';

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
    case 'boolean': {
      const on = Boolean(value);
      control = (
        // A switch, not a checkbox: this takes effect the moment it is clicked,
        // and there is no form to submit. `role`/`aria-checked` carry the state to
        // assistive tech and also drive the styling, so the two cannot drift.
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label={decl.title}
          className="switch"
          onClick={() => commit(!on)}
        />
      );
      break;
    }
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
        <div className="select-wrap">
          <select value={String(value)} onChange={(e) => commit(e.target.value)}>
            {(decl.enumValues ?? []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </div>
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
    <div className={`setting-row${overridden ? ' is-modified' : ''}`}>
      <div className="setting-label">
        <label>
          {decl.title}
          <code className="setting-key">{decl.key}</code>
        </label>
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

/** Everything a search term is allowed to match. The key is included because it is
 *  what docs, plugin manifests and `dash` call the setting by. */
function matches(decl: SettingDecl, owner: string, needle: string): boolean {
  if (!needle) return true;
  const haystack = `${decl.title} ${decl.description ?? ''} ${decl.key} ${owner}`;
  return haystack.toLowerCase().includes(needle);
}

/** A group in the rendered page: declared settings, or one contributed section. */
interface Group {
  id: string;
  title: string;
  decls: SettingDecl[];
  /** Set for a contributed section; `decls` is empty in that case. */
  Section?: React.ComponentType;
}

/**
 * The settings page: every setting declared by a module or plugin, grouped by the
 * contributor that declared it.
 *
 * Modelled on VS Code's Settings editor — a search box over a category list and a
 * scrolling body — and it opens the same way, as a **document pane** (a center
 * tab) rather than a dock panel. It was a right-dock tool pane, which gave two
 * columns of content a ~20rem slot and made every row wrap.
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
  const [query, setQuery] = useState('');
  const [active, setActive] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const groupRefs = useRef(new Map<string, HTMLElement>());

  const settings = registry.settings;
  const sections = registry.settingsSections;
  const needle = query.trim().toLowerCase();

  const { groups, total, shown } = useMemo(() => {
    // Group by owning module title, preserving declaration order within a group.
    const byOwner = new Map<string, SettingDecl[]>();
    for (const decl of settings) {
      const owner = registry.settingOwner(decl.key) ?? 'Other';
      const list = byOwner.get(owner) ?? [];
      list.push(decl);
      byOwner.set(owner, list);
    }

    const out: Group[] = [];
    let count = 0;
    for (const [owner, decls] of byOwner) {
      const kept = decls.filter((d) => matches(d, owner, needle));
      if (kept.length === 0) continue;
      count += kept.length;
      out.push({ id: `group:${owner}`, title: owner, decls: kept });
    }

    // A contributed section renders an opaque component, so there is nothing to
    // search *inside* it — it can only be matched by its own title. Filtering it
    // out on any other term is the honest behaviour: claiming to have searched
    // content we cannot read would hide settings that are really there.
    for (const section of sections) {
      if (needle && !section.title.toLowerCase().includes(needle)) continue;
      out.push({
        id: `section:${section.id}`,
        title: section.title,
        decls: [],
        Section: section.component,
      });
    }

    return { groups: out, total: settings.length, shown: count };
  }, [settings, sections, needle]);

  // Falls back to the first group rather than nothing: the body starts at the top,
  // so before a single scroll event the first group *is* the one you are looking
  // at, and a nav with no entry marked reads as a nav that doesn't track.
  const activeId = active ?? groups[0]?.id ?? null;

  const jumpTo = useCallback((id: string) => {
    setActive(id);
    groupRefs.current.get(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, []);

  // Scroll spy: the nav highlights whichever group's heading is nearest the top of
  // the body. Driven off scroll rather than IntersectionObserver because the groups
  // are re-created on every search keystroke, and re-observing a changing set of
  // elements costs more than this comparison does.
  const onScroll = useCallback(() => {
    const body = bodyRef.current;
    if (!body) return;
    const top = body.getBoundingClientRect().top;
    let best: string | null = null;
    for (const [id, el] of groupRefs.current) {
      if (el.getBoundingClientRect().top - top <= 24) best = id;
    }
    setActive(best ?? groups[0]?.id ?? null);
  }, [groups]);

  return (
    <div className="settings-page">
      <div className="settings-head">
        <h1 className="settings-title">Settings</h1>
        <input
          type="search"
          className="settings-search"
          placeholder="Search settings"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="settings-count">
          {needle ? `${shown} of ${total}` : `${total} settings`}
        </span>
      </div>

      <div className="settings-shell">
        <nav className="settings-nav">
          {groups.map((group) => (
            <button
              key={group.id}
              className={group.id === activeId ? 'is-active' : undefined}
              title={group.title}
              onClick={() => jumpTo(group.id)}
            >
              {group.title}
            </button>
          ))}
        </nav>

        <div className="settings-body" ref={bodyRef} onScroll={onScroll}>
          {groups.length === 0 && (
            <p className="settings-empty">
              {total === 0
                ? 'No settings yet — modules and plugins contribute their own here.'
                : `No setting matches “${query.trim()}”.`}
            </p>
          )}

          {groups.map((group) => {
            const basic = group.decls.filter((d) => !d.advanced);
            const advanced = group.decls.filter((d) => d.advanced);
            const Section = group.Section;
            return (
              <section
                key={group.id}
                className="settings-group"
                ref={(el) => {
                  if (el) groupRefs.current.set(group.id, el);
                  else groupRefs.current.delete(group.id);
                }}
              >
                <h3>{group.title}</h3>
                {Section ? (
                  <div className="settings-card settings-card--section">
                    <Section />
                  </div>
                ) : (
                  <div className="settings-card">
                    {basic.map((decl) => (
                      <SettingRow key={decl.key} decl={decl} />
                    ))}
                    {advanced.length > 0 && (
                      <details className="settings-fold">
                        {/* The count is on the summary on purpose: a fold that
                            doesn't say how much it hides is a fold people never
                            open. */}
                        <summary>Advanced ({advanced.length})</summary>
                        {advanced.map((decl) => (
                          <SettingRow key={decl.key} decl={decl} />
                        ))}
                      </details>
                    )}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
