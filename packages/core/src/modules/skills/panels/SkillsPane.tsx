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
 */
import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { DataList, DataRow, RollingNumber } from '../../../DataList';
import { dialogs } from '../../../dialogs';
import { usePaneSection } from '../../../layout/use-sections';
import {
  copySkill,
  deleteSkill,
  exportSkill,
  listSkills,
  setSkillEnabled,
  skillCost,
  type Skill,
  type SkillCost,
} from '../api';
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

function SkillRow({
  skill,
  tokens,
  index,
  onChanged,
  onEdit,
}: {
  skill: Skill;
  tokens: number | undefined;
  index: number;
  onChanged: () => void;
  onEdit: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const broken = Boolean(skill.error);
  const toggleable = !broken && !skill.shadowed;

  const remove = async () => {
    const ok = await dialogs.confirm({
      title: `Delete “${skill.name}”?`,
      message: `Deletes ${skill.path}. This cannot be undone.`,
      confirmLabel: 'Delete skill',
      danger: true,
    });
    if (ok) await act(() => deleteSkill(skill.name));
  };

  return (
    <DataRow
      kind={skillKind(skill)}
      hideMark={!broken && !skill.shadowed}
      index={index}
      title={skill.name}
      meta={tokens !== undefined ? [`${tokens} tok/turn`] : undefined}
      metaTone={broken ? 'fail' : undefined}
      badge={
        <>
          {skill.scope === 'project' && <Chip>project</Chip>}
          {skill.shadowed && <Chip kind="warn">shadowed</Chip>}
          {broken && <Chip kind="fail">error</Chip>}
          {!skill.enabled && toggleable && <Chip>off</Chip>}
        </>
      }
      actions={
        <>
          {/* Disabling is the lever that matters: the honest answer to "my context
              is full" is switching off the skills that never fire. A real checkbox,
              labelled — the swatch-and-title trick used elsewhere leaves a control
              that announces as unnamed. */}
          <label
            style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)' }}
            title={
              broken
                ? 'This skill has an error and cannot be enabled'
                : skill.shadowed
                  ? 'Shadowed by your own skill of this name'
                  : 'Include its description in every turn'
            }
          >
            <input
              type="checkbox"
              checked={skill.enabled && toggleable}
              disabled={busy || !toggleable}
              aria-label={`Enable ${skill.name}`}
              onChange={(e) => act(() => setSkillEnabled(skill.name, e.target.checked))}
            />
          </label>
          <Button size="sm" disabled={busy} onClick={onEdit}>
            {skill.scope === 'project' ? 'View' : 'Edit'}
          </Button>
          {skill.scope === 'project' ? (
            <Button size="sm" disabled={busy} onClick={() => act(() => copySkill(skill.name))}>
              Copy to mine
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                disabled={busy}
                title="Copy into .claude/skills so Claude Code picks it up"
                onClick={() => act(() => exportSkill(skill.name))}
              >
                Export
              </Button>
              <Button intent="danger" size="sm" disabled={busy} onClick={remove}>
                Delete
              </Button>
            </>
          )}
        </>
      }
      footnotes={
        <>
          {broken && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>{skill.error}</div>
          )}
          {skill.shadowed && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--warn)' }}>
              Your own skill of this name is used instead — this one never reaches the agent.
            </div>
          )}
          {error && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--danger)' }}>{error}</div>
          )}
        </>
      }
    >
      {skill.description || <em>no description — the model has nothing to decide by</em>}
    </DataRow>
  );
}

export function SkillsPane() {
  const { section, setSection } = usePaneSection();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [cost, setCost] = useState<SkillCost | null>(null);
  const [dirs, setDirs] = useState({ userDir: '', projectDir: '' });
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

  const perSkill = new Map((cost?.skills ?? []).map((s) => [s.name, s.tokens]));
  const active = skills.filter((s) => s.enabled && !s.error && !s.shadowed).length;

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

      <div style={{ padding: 'var(--space-5)', overflow: 'auto', flex: 1 }}>
        <div
          style={{
            fontSize: 'var(--fs-meta)',
            color: 'var(--text-dim)',
            marginBottom: 'var(--space-5)',
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
              marginBottom: 'var(--space-4)',
            }}
          >
            {error}
          </div>
        )}

        {loading ? (
          <div style={{ color: 'var(--text-dim)', fontSize: 'var(--fs-body)' }}>Loading…</div>
        ) : skills.length === 0 ? (
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
        ) : (
          <DataList label="Skills">
            {skills.map((s, i) => (
              <SkillRow
                key={`${s.scope}:${s.name}`}
                skill={s}
                index={i}
                tokens={perSkill.get(s.name)}
                onChanged={refresh}
                onEdit={() => openEditor(s)}
              />
            ))}
          </DataList>
        )}

        <div
          style={{
            fontSize: 'var(--fs-micro)',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-faint)',
            marginTop: 'var(--space-6)',
            lineHeight: 1.6,
          }}
        >
          Yours: {dirs.userDir}
          <br />
          Project: {dirs.projectDir}
        </div>
      </div>
    </div>
  );
}
