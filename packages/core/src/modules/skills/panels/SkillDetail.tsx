/**
 * One skill, in full.
 *
 * This column exists because the list could not hold it. A skill's row used to carry
 * its name, its description, a token figure and six controls on one full-bleed line,
 * so in a maximised pane the Delete button sat two thousand pixels from the name it
 * belonged to — and the name itself was the smallest text in the row, set two and a
 * half points under the description beneath it.
 *
 * Split in two, each half gets the treatment it wanted: the index is a column of
 * identities, and everything that is *about* a skill rather than *which* skill it is
 * lives here, inside a reading measure.
 *
 * The actions live here rather than in the index for a reason that is not taste.
 * `DataRow` renders a real `<button>` once it is clickable, and a `<button>` may not
 * contain interactive descendants — the enable checkbox and five buttons inside a
 * selectable row would be invalid content that browsers and screen readers each
 * recover from differently.
 */
import { useEffect, useState, type CSSProperties } from 'react';

import { DataList, DataRow } from '../../../DataList';
import { Button, Chip, EmptyState } from '../../../Primitives';
import { dialogs } from '../../../dialogs';
import {
  copySkill,
  deleteSkill,
  exportSkill,
  readSkillFile,
  setSkillEnabled,
  type Skill,
  type SkillFile,
} from '../api';

/** Bytes, at the precision a file listing is actually read at. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const LABEL: CSSProperties = {
  fontSize: 'var(--fs-micro)',
  fontFamily: 'var(--font-mono)',
  fontWeight: 700,
  letterSpacing: 'var(--tracking-badge)',
  textTransform: 'uppercase',
  color: 'var(--text-dim)',
  marginBottom: 'var(--space-2)',
};

const NOTICE: CSSProperties = {
  fontSize: 'var(--fs-body)',
  lineHeight: 1.5,
  marginBottom: 'var(--space-3)',
};

/**
 * The skill's own files, and one of them opened.
 *
 * A skill is a directory, not a file — `copy_to_user` has always taken the siblings
 * along — but nothing ever served them, so a skill's references were invisible in the
 * app that hosts it. Listing them without being able to open one would only move the
 * problem, hence the viewer; it is read-only, because the editor writes `SKILL.md` and
 * nothing else, and a viewer that could write would raise the question of what happens
 * to a project skill's git-tracked resources.
 */
function SkillFiles({ skill }: { skill: Skill }) {
  const [open, setOpen] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Reset when the selection changes: without this, switching skills leaves the
  // previous one's file contents on screen underneath the new one's name.
  useEffect(() => {
    setOpen(null);
    setText(null);
    setError(null);
  }, [skill.name, skill.scope]);

  const show = async (file: SkillFile) => {
    if (open === file.name) {
      setOpen(null);
      return;
    }
    setOpen(file.name);
    setText(null);
    setError(null);
    try {
      const body = await readSkillFile(skill.name, file.name);
      setText(body.text);
    } catch (e) {
      // A binary file and an unreadable one both land here, and both are worth saying
      // out loud — a viewer that silently showed nothing reads as a broken pane.
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (skill.files.length === 0) return null;

  return (
    <section style={{ marginTop: 'var(--space-5)' }}>
      <div style={LABEL}>Files</div>
      <DataList label={`Files in ${skill.name}`}>
        {skill.files.map((file, i) => (
          <DataRow
            key={file.name}
            index={i}
            kind="idle"
            hideMark
            selected={open === file.name}
            onClick={() => void show(file)}
            title={<span style={{ fontFamily: 'var(--font-mono)' }}>{file.name}</span>}
            meta={[formatBytes(file.bytes)]}
          />
        ))}
      </DataList>
      {open && (
        <pre
          style={{
            margin: 'var(--space-3) 0 0',
            padding: 'var(--space-3)',
            background: 'var(--bg-inset)',
            border: '1px solid var(--border)',
            fontSize: 'var(--fs-micro)',
            fontFamily: 'var(--font-mono)',
            lineHeight: 1.55,
            maxHeight: 320,
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
          }}
        >
          {error ? (
            <span style={{ color: 'var(--danger)' }}>{error}</span>
          ) : text === null ? (
            <span style={{ color: 'var(--text-dim)' }}>Reading…</span>
          ) : (
            text
          )}
        </pre>
      )}
    </section>
  );
}

export function SkillDetail({
  skill,
  tokens,
  bodyTokens,
  onChanged,
  onEdit,
  onNew,
}: {
  skill: Skill | null;
  tokens: number | undefined;
  bodyTokens: number | undefined;
  onChanged: () => void;
  onEdit: () => void;
  onNew: () => void;
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

  if (!skill) {
    return (
      <EmptyState
        title="No skills"
        actions={
          <Button intent="primary" size="sm" onClick={onNew}>
            New skill
          </Button>
        }
      >
        A skill is reusable instructions for a kind of task, in the same SKILL.md format Claude Code
        reads — so one written here works there too. Anything in the project's
        <code> .claude/skills</code> is picked up automatically.
      </EmptyState>
    );
  }

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
    <div style={{ maxWidth: '72ch' }}>
      <h2
        style={{
          margin: 0,
          fontSize: 'var(--fs-display)',
          fontWeight: 700,
          letterSpacing: 'var(--tracking-display)',
          textTransform: 'uppercase',
          color: 'var(--text-strong)',
          overflowWrap: 'anywhere',
        }}
      >
        {skill.name}
      </h2>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 'var(--space-3)',
          marginTop: 'var(--space-2)',
          fontSize: 'var(--fs-meta)',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-dim)',
        }}
      >
        <span>{skill.scope}</span>
        {tokens !== undefined && <span>{tokens} tok/turn</span>}
        {bodyTokens !== undefined && <span>{bodyTokens} tok when used</span>}
        {skill.shadowed && <Chip kind="warn">shadowed</Chip>}
        {broken && <Chip kind="fail">error</Chip>}
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 'var(--space-2)',
          margin: 'var(--space-4) 0',
          paddingBottom: 'var(--space-4)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        {/* Disabling is the lever that matters: the honest answer to "my context is
            full" is switching off the skills that never fire. A real checkbox, with a
            visible label — the swatch-and-title trick used elsewhere leaves a control
            that announces as unnamed. */}
        <label
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--space-2)',
            marginRight: 'var(--space-3)',
            fontSize: 'var(--fs-body)',
            color: toggleable ? 'var(--text)' : 'var(--text-faint)',
          }}
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
            onChange={(e) => act(() => setSkillEnabled(skill.name, e.target.checked))}
          />
          Enabled
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
      </div>

      {broken && <div style={{ ...NOTICE, color: 'var(--danger)' }}>{skill.error}</div>}
      {skill.shadowed && (
        <div style={{ ...NOTICE, color: 'var(--warn)' }}>
          Your own skill of this name is used instead — this one never reaches the agent.
        </div>
      )}
      {error && (
        <div role="alert" style={{ ...NOTICE, color: 'var(--danger)' }}>
          {error}
        </div>
      )}

      <div style={{ fontSize: 'var(--fs-body)', lineHeight: 1.55, color: 'var(--text)' }}>
        {skill.description || <em>no description — the model has nothing to decide by</em>}
      </div>

      {skill.allowedTools.length > 0 && (
        <section style={{ marginTop: 'var(--space-5)' }}>
          <div style={LABEL}>Tools it needs</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
            {skill.allowedTools.map((tool) => (
              <Chip key={tool}>{tool}</Chip>
            ))}
          </div>
        </section>
      )}

      <SkillFiles skill={skill} />

      <div
        style={{
          marginTop: 'var(--space-5)',
          fontSize: 'var(--fs-micro)',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-faint)',
          overflowWrap: 'anywhere',
        }}
      >
        {skill.path}
      </div>
    </div>
  );
}
