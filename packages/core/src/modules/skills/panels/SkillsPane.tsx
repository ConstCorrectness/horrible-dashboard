import { useCallback, useEffect, useState } from 'react';

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
 * The skills pane: what the agent knows how to do, and what knowing it costs.
 *
 * The header is not decoration. A skill's description is injected into every single
 * turn, so a directory of ten skills is a permanent tax on the same context budget the
 * tool schemas compete for — and unlike tools, nothing else in the app would ever show
 * it. Putting the per-turn total at the top, before the list, is the pane's main job;
 * the editor underneath is the easy part.
 */

function Chip({ text, tone }: { text: string; tone: 'warn' | 'danger' | 'dim' }) {
  const color =
    tone === 'danger'
      ? 'var(--danger, #f85149)'
      : tone === 'warn'
        ? 'var(--warn, #d29922)'
        : 'var(--text-dim)';
  return (
    <span
      style={{
        fontSize: '0.62rem',
        padding: '0 0.3rem',
        borderRadius: 3,
        border: '1px solid var(--border)',
        color,
      }}
    >
      {text}
    </span>
  );
}

function SkillRow({
  skill,
  tokens,
  onChanged,
  onEdit,
}: {
  skill: Skill;
  tokens: number | undefined;
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
  const inactive = broken || skill.shadowed || !skill.enabled;

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: '0.5rem 0.65rem',
        marginBottom: '0.4rem',
        opacity: inactive ? 0.65 : 1,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        {/* Disabling is the lever that matters: the honest answer to "my context is
            full" is switching off the skills that never fire. */}
        <input
          type="checkbox"
          checked={skill.enabled && !broken && !skill.shadowed}
          disabled={busy || broken || skill.shadowed}
          onChange={(e) => act(() => setSkillEnabled(skill.name, e.target.checked))}
          title={broken ? 'This skill has an error' : 'Include in every turn'}
        />
        <strong>{skill.name}</strong>
        {skill.scope === 'project' && <Chip text="project" tone="dim" />}
        {skill.shadowed && <Chip text="shadowed" tone="warn" />}
        {broken && <Chip text="error" tone="danger" />}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
          {tokens !== undefined && (
            <span style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }} title="Per turn">
              {tokens} tok/turn
            </span>
          )}
          <button disabled={busy} onClick={onEdit}>
            {skill.scope === 'project' ? 'View' : 'Edit'}
          </button>
          {skill.scope === 'project' ? (
            <button disabled={busy} onClick={() => act(() => copySkill(skill.name))}>
              Copy to mine
            </button>
          ) : (
            <>
              <button
                disabled={busy}
                title="Copy into .claude/skills so Claude Code picks it up"
                onClick={() => act(() => exportSkill(skill.name))}
              >
                Export
              </button>
              <button disabled={busy} onClick={() => act(() => deleteSkill(skill.name))}>
                Delete
              </button>
            </>
          )}
        </span>
      </div>

      <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: '0.2rem' }}>
        {skill.description || <em>no description</em>}
      </div>

      {broken && (
        <div style={{ fontSize: '0.7rem', color: 'var(--danger, #f85149)', marginTop: '0.2rem' }}>
          {skill.error}
        </div>
      )}
      {skill.shadowed && (
        <div style={{ fontSize: '0.7rem', color: 'var(--warn, #d29922)', marginTop: '0.2rem' }}>
          Your own skill of this name is used instead — this one never reaches the agent.
        </div>
      )}
      {error && (
        <div style={{ fontSize: '0.7rem', color: 'var(--danger, #f85149)', marginTop: '0.2rem' }}>
          {error}
        </div>
      )}
    </div>
  );
}

export function SkillsPane() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [cost, setCost] = useState<SkillCost | null>(null);
  const [dirs, setDirs] = useState({ userDir: '', projectDir: '' });
  const [editing, setEditing] = useState<Skill | 'new' | null>(null);
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

  if (editing) {
    return (
      <div style={{ padding: '0.75rem', height: '100%', overflow: 'auto' }}>
        <SkillEditor
          skill={editing === 'new' ? null : editing}
          onDone={() => {
            setEditing(null);
            void refresh();
          }}
        />
      </div>
    );
  }

  const perSkill = new Map((cost?.skills ?? []).map((s) => [s.name, s.tokens]));

  return (
    <div style={{ padding: '0.75rem', height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <div style={{ fontSize: '0.8rem' }}>
          {cost ? (
            <>
              <strong>{cost.catalogTokens.toLocaleString()}</strong> tokens on <em>every</em> turn
              {!cost.exact && (
                <span style={{ color: 'var(--warn, #d29922)' }}> · estimated (no tokenizer)</span>
              )}
            </>
          ) : (
            <span style={{ color: 'var(--text-dim)' }}>Measuring…</span>
          )}
        </div>
        <button style={{ marginLeft: 'auto' }} onClick={() => setEditing('new')}>
          New skill
        </button>
      </div>

      <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginBottom: '0.5rem' }}>
        Each skill's <em>description</em> is injected into every turn so the agent knows it exists;
        the instructions are only read when it calls <code>use_skill</code>. Switch off the ones
        that never fire.
      </div>

      {loading && <div style={{ color: 'var(--text-dim)' }}>Loading…</div>}
      {error && <div style={{ color: 'var(--danger, #f85149)' }}>{error}</div>}
      {!loading && skills.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>
          No skills yet. A skill is reusable instructions for a kind of task — the same SKILL.md
          format Claude Code reads.
        </div>
      )}

      {skills.map((s) => (
        <SkillRow
          key={`${s.scope}:${s.name}`}
          skill={s}
          tokens={perSkill.get(s.name)}
          onChanged={refresh}
          onEdit={() => setEditing(s)}
        />
      ))}

      <div style={{ fontSize: '0.66rem', color: 'var(--text-dim)', marginTop: '0.6rem' }}>
        Yours: <code>{dirs.userDir}</code>
        <br />
        Project: <code>{dirs.projectDir}</code>
      </div>
    </div>
  );
}
