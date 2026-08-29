/**
 * The skills pane: what the agent knows how to do, and what knowing it costs.
 *
 * The header is not decoration. A skill's description is injected into every single
 * turn, so a directory of ten skills is a permanent tax on the same context budget the
 * tool schemas compete for — and unlike tools, nothing else in the app would ever show
 * it. Putting the per-turn total at the top, before the list, is the pane's main job;
 * the editor is the easy part.
 *
 * Library and editor are **sections** now rather than a `useState` swap of the whole
 * pane body. The old arrangement gave the editor no address: the tab strip did not
 * know it was open, reloading dropped you back in the list, and the only way out was
 * the component's own Cancel button.
 *
 * One honest limit: the section is addressable, *which skill is loaded into it* is
 * still component state. A pane reload returns to the editor tab with nothing
 * selected, which is the "new skill" case and reads correctly — but it is not a deep
 * link to a named skill, and pretending otherwise would need params on a singleton.
 *
 * ## Why the library is a split
 *
 * Every skill used to be one full-bleed row carrying its name, its description, a
 * token figure and six controls. Nothing capped the width, so in a maximised pane the
 * Delete button ended up two thousand pixels from the name it belonged to, and the
 * name — 11px, uppercase — was smaller than the description sitting under it.
 *
 * So the row's job was split from the record's. The index holds identities and nothing
 * else (name, cost, state), which is what lets it be 280px wide; `SkillDetail` holds
 * everything that is *about* a skill, inside a reading measure. The index rows carry
 * no buttons, and that is a rule rather than a preference: `DataRow` becomes a real
 * `<button>` once it is clickable, and a `<button>` may not contain interactive
 * descendants.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { DataList, DataRow, RollingNumber } from '../../../DataList';
import { SplitPane } from '../../../SplitPane';
import { usePaneSection } from '../../../layout/use-sections';
import { listSkills, skillCost, type Skill, type SkillCost } from '../api';
import { SkillDetail } from './SkillDetail';
import { SkillEditor } from './SkillEditor';

/**
 * A skill's row verdict.
 *
 * Deliberately **not** `ok` for an enabled skill. A row's `ok` means "this passed",
 * and a skill that is merely switched on has not been shown to do anything — the
 * only real verdicts here are broken (`fail`) and shadowed-so-never-reached
 * (`warn`). Everything else is a category, which is what `info`/`idle` are for.
 */
function skillKind(skill: Skill) {
  if (skill.error) return 'fail' as const;
  if (skill.shadowed) return 'warn' as const;
  if (!skill.enabled) return 'idle' as const;
  return 'info' as const;
}

/**
 * A skill's identity across a refresh.
 *
 * Keyed by scope as well as name: a shadowed project skill shares its name with the
 * user skill hiding it, so a name alone would make the two the same row and the
 * selection would jump between them.
 */
function keyOf(skill: Skill): string {
  return `${skill.scope}:${skill.name}`;
}

export function SkillsPane() {
  const { section, setSection } = usePaneSection();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [cost, setCost] = useState<SkillCost | null>(null);
  const [dirs, setDirs] = useState({ userDir: '', projectDir: '' });
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await listSkills();
      setSkills(res.skills);
      setDirs({ userDir: res.userDir, projectDir: res.projectDir });
      setError(null);
      // Cost is a second call on purpose: it needs a tokenizer, which may not resolve,
      // and a list that failed to render because the counter was slow would be worse
      // than a list with no numbers yet.
      setCost(await skillCost());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Resolved against the current list rather than held as an object, so a refresh
  // after a toggle or a delete never leaves the detail rendering a stale copy — or a
  // skill that no longer exists. Falling back to the first row means the empty state
  // is only reachable when there genuinely are no skills.
  const selected = useMemo(() => {
    if (skills.length === 0) return null;
    return skills.find((s) => keyOf(s) === selectedKey) ?? skills[0];
  }, [skills, selectedKey]);

  const openEditor = (skill: Skill | null) => {
    setEditing(skill);
    setSection('editor');
  };

  if (section === 'editor') {
    return (
      <div style={{ padding: 'var(--space-5)', height: '100%', overflow: 'auto' }}>
        <SkillEditor
          skill={editing}
          onDone={() => {
            setEditing(null);
            setSection('library');
            void refresh();
          }}
        />
      </div>
    );
  }

  const perSkill = new Map((cost?.skills ?? []).map((s) => [s.name, s]));
  const active = skills.filter((s) => s.enabled && !s.error && !s.shadowed).length;
  const selectedCost = selected ? perSkill.get(selected.name) : undefined;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <PaneHeader
        title="Skills"
        meta={
          cost
            ? [
                <>
                  <RollingNumber value={cost.catalogTokens} /> tok/turn
                </>,
                `${active}/${skills.length} on`,
                ...(cost.exact ? [] : [<em key="est">estimated</em>]),
              ]
            : ['measuring…']
        }
        actions={
          <Button intent="primary" size="sm" onClick={() => openEditor(null)}>
            New skill
          </Button>
        }
      />

      {/* Above the split, not inside the detail column: this says how skills are paid
          for, which is true of all of them. Repeated over each one you clicked it would
          read as part of that skill. */}
      <div
        style={{
          padding: 'var(--space-3) var(--space-5)',
          borderBottom: '1px solid var(--border)',
          fontSize: 'var(--fs-meta)',
          color: 'var(--text-dim)',
          lineHeight: 1.5,
        }}
      >
        Each skill's <em>description</em> rides every turn so the agent knows it exists; the
        instructions are read only when it calls <code>use_skill</code>. Switch off the ones that
        never fire — a disabled skill costs exactly nothing.
      </div>

      {error && (
        <div
          role="alert"
          style={{
            color: 'var(--danger)',
            fontSize: 'var(--fs-body)',
            padding: 'var(--space-3) var(--space-5)',
          }}
        >
          {error}
        </div>
      )}

      {loading ? (
        <div
          style={{
            padding: 'var(--space-5)',
            color: 'var(--text-dim)',
            fontSize: 'var(--fs-body)',
          }}
        >
          Loading…
        </div>
      ) : skills.length === 0 ? (
        <div style={{ padding: 'var(--space-5)', overflow: 'auto', flex: 1 }}>
          <EmptyState
            title="No skills"
            actions={
              <Button intent="primary" size="sm" onClick={() => openEditor(null)}>
                New skill
              </Button>
            }
          >
            A skill is reusable instructions for a kind of task, in the same SKILL.md format Claude
            Code reads — so one written here works there too. Anything in the project's
            <code> .claude/skills</code> is picked up automatically.
          </EmptyState>
        </div>
      ) : (
        // `narrowBelow` measures the container, not the viewport: a skills pane docked
        // in a three-column workspace is narrow at any screen size.
        <SplitPane
          id="skills.index"
          initial={280}
          min={200}
          minOther={360}
          narrowBelow={700}
          label="Skill list width"
        >
          <div
            style={{
              height: '100%',
              overflow: 'auto',
              padding: 'var(--space-4) var(--space-3)',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <DataList label="Skills" size="lead">
              {skills.map((s, i) => (
                <DataRow
                  key={keyOf(s)}
                  index={i}
                  kind={skillKind(s)}
                  hideMark={!s.error && !s.shadowed}
                  title={s.name}
                  meta={
                    perSkill.get(s.name) !== undefined
                      ? [`${perSkill.get(s.name)?.tokens} tok`]
                      : undefined
                  }
                  metaTone={s.error ? 'fail' : undefined}
                  badge={
                    <>
                      {s.scope === 'project' && <Chip>project</Chip>}
                      {!s.enabled && !s.error && !s.shadowed && <Chip>off</Chip>}
                    </>
                  }
                  selected={selected ? keyOf(selected) === keyOf(s) : false}
                  onClick={() => setSelectedKey(keyOf(s))}
                />
              ))}
            </DataList>

            {/* Under the index, not the detail: these are where skills live, which is a
                fact about the pane rather than about whichever row is selected — and a
                path that reappeared under every skill you clicked would read as part of
                it. `marginTop: auto` floats it to the bottom of a short list. */}
            <div
              style={{
                marginTop: 'auto',
                paddingTop: 'var(--space-6)',
                fontSize: 'var(--fs-micro)',
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-faint)',
                lineHeight: 1.6,
                overflowWrap: 'anywhere',
              }}
            >
              Yours: {dirs.userDir}
              <br />
              Project: {dirs.projectDir}
            </div>
          </div>

          <div style={{ height: '100%', overflow: 'auto', padding: 'var(--space-5)' }}>
            <SkillDetail
              skill={selected}
              tokens={selectedCost?.tokens}
              bodyTokens={selectedCost?.bodyTokens}
              onChanged={refresh}
              onEdit={() => selected && openEditor(selected)}
              onNew={() => openEditor(null)}
            />
          </div>
        </SplitPane>
      )}
    </div>
  );
}
