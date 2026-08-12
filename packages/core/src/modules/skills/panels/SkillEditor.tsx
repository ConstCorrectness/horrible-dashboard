import { useCallback, useEffect, useState } from 'react';

import { saveSkill, skillPreview, type Skill, type SkillPreview } from '../api';

/**
 * Write a skill, and see exactly what the agent will be given.
 *
 * The preview is the point of the screen. A skill is a prompt, and the usual way
 * prompts go wrong is that the author is looking at their intent while the model is
 * looking at a string — so this shows the literal two things it receives: the catalog
 * line injected every turn, and the instructions `use_skill` returns. Not rendered
 * markdown; the raw text, because stray frontmatter and leading whitespace are exactly
 * what a prettified preview would hide.
 */

const DEFAULT_BODY = `# What this skill does

Write the steps here as if briefing someone competent who has not seen this task
before. Concrete beats complete: the model already knows how to write code, it does
not know your conventions.

## Steps

1. …
2. …
`;

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: '0.5rem' }}>
      <label style={{ display: 'block', fontSize: '0.72rem', marginBottom: '0.15rem' }}>
        {label}
        {hint && <span style={{ color: 'var(--text-dim)' }}> — {hint}</span>}
      </label>
      {children}
    </div>
  );
}

export function SkillEditor({ skill, onDone }: { skill: Skill | null; onDone: () => void }) {
  const readOnly = skill?.scope === 'project';
  const [name, setName] = useState(skill?.name ?? '');
  const [description, setDescription] = useState(skill?.description ?? '');
  const [body, setBody] = useState(skill?.body ?? DEFAULT_BODY);
  const [tools, setTools] = useState((skill?.allowedTools ?? []).join(', '));
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPreview = useCallback(async () => {
    if (!skill) return;
    try {
      setPreview(await skillPreview(skill.name));
    } catch {
      // A preview that fails is not worth an error banner over the editor — the
      // fields are still perfectly usable, and the save path reports its own errors.
      setPreview(null);
    }
  }, [skill]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveSkill({
        name: name.trim(),
        description: description.trim(),
        body,
        // Comma-separated because that is how `allowed-tools` is written by hand, and
        // the backend accepts either form in the file itself.
        allowedTools: tools
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <strong>{skill ? skill.name : 'New skill'}</strong>
        {readOnly && (
          <span style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>
            project skill — copy it to your own to edit
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
          {!readOnly && (
            <button
              disabled={busy || !name.trim() || !description.trim()}
              onClick={() => void submit()}
            >
              Save
            </button>
          )}
          <button onClick={onDone}>{readOnly ? 'Back' : 'Cancel'}</button>
        </span>
      </div>

      <Field label="Name" hint="lowercase, hyphens; this is what the agent calls">
        <input
          value={name}
          disabled={readOnly || Boolean(skill)}
          onChange={(e) => setName(e.target.value)}
          placeholder="tidy-a-file"
          style={{ width: '100%' }}
        />
      </Field>

      <Field label="Description" hint="rides EVERY turn — one sentence saying when to use this">
        <textarea
          value={description}
          disabled={readOnly}
          rows={2}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Tidy a source file the way this project likes it. Use before committing."
          style={{ width: '100%' }}
        />
      </Field>

      <Field label="Tools it needs" hint="optional; loaded automatically when used">
        <input
          value={tools}
          disabled={readOnly}
          onChange={(e) => setTools(e.target.value)}
          placeholder="files.read, editor.proposeEdit"
          style={{ width: '100%' }}
        />
      </Field>

      <Field label="Instructions" hint="read only when the agent calls use_skill">
        <textarea
          value={body}
          disabled={readOnly}
          rows={16}
          spellCheck={false}
          onChange={(e) => setBody(e.target.value)}
          style={{ width: '100%', fontFamily: 'monospace', fontSize: '0.7rem', whiteSpace: 'pre' }}
        />
      </Field>

      {error && <div style={{ color: 'var(--danger, #f85149)', fontSize: '0.72rem' }}>{error}</div>}

      {preview && (
        <details style={{ marginTop: '0.5rem' }}>
          <summary style={{ cursor: 'pointer', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            What the agent actually sees
          </summary>
          <div
            style={{ fontSize: '0.68rem', color: 'var(--text-dim)', margin: '0.3rem 0 0.15rem' }}
          >
            Injected every turn:
          </div>
          <pre
            style={{
              margin: 0,
              padding: '0.4rem',
              border: '1px solid var(--border)',
              borderRadius: 4,
              whiteSpace: 'pre-wrap',
              fontSize: '0.66rem',
              maxHeight: 160,
              overflow: 'auto',
            }}
          >
            {preview.catalog}
          </pre>
          <div
            style={{ fontSize: '0.68rem', color: 'var(--text-dim)', margin: '0.4rem 0 0.15rem' }}
          >
            Returned by <code>use_skill(&apos;{skill?.name}&apos;)</code>
            {preview.groups.length > 0 && <> · also loads {preview.groups.join(', ')}</>}:
          </div>
          <pre
            style={{
              margin: 0,
              padding: '0.4rem',
              border: '1px solid var(--border)',
              borderRadius: 4,
              whiteSpace: 'pre-wrap',
              fontSize: '0.66rem',
              maxHeight: 220,
              overflow: 'auto',
            }}
          >
            {preview.instructions}
          </pre>
        </details>
      )}
    </div>
  );
}
