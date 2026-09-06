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
import { useCallback, useEffect, useState } from 'react';

import { Button, Chip, Field } from '../../../Primitives';
import { listToolGroups, type ToolGroup } from '../../agent/api';
import { saveSkill, skillPreview, type Skill, type SkillPreview } from '../api';

const DEFAULT_BODY = `# What this skill does

Write the steps here as if briefing someone competent who has not seen this task
before. Concrete beats complete: the model already knows how to write code, it does
not know your conventions.

## Steps

1. …
2. …
`;

/**
 * The tool-group picker.
 *
 * This was a free-text comma-separated input with a placeholder of
 * `files.read, editor.proposeEdit`. Two things were wrong with that, and the second
 * is the interesting one.
 *
 * You could not tell whether what you typed was real: `allowed-tools` resolves to
 * **groups**, and a name that matches nothing is not an error anywhere — the skill
 * saves, loads, and simply activates nothing, which surfaces later as an agent that
 * "ignored" its own skill.
 *
 * And it hid the seam between this module and MCP entirely. A group is a tool name's
 * prefix, and a connected MCP server *is* a group (`mcp-<id>`) — so a skill can pull a
 * whole third-party server's tools into a turn by naming it here. Nothing in either
 * pane said so. Listing the live catalog makes MCP servers appear in the picker beside
 * the built-ins, which is the only place that relationship is visible.
 *
 * Free text is still accepted alongside it: the file format allows individual tool
 * names (`files.read`), Claude Code writes them that way, and refusing to round-trip a
 * portable file would defeat the point of adopting the format.
 */
function ToolPicker({
  value,
  disabled,
  onChange,
}: {
  value: string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
}) {
  const [groups, setGroups] = useState<ToolGroup[] | null>(null);
  const [connected, setConnected] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    listToolGroups()
      .then((res) => {
        if (!alive) return;
        setGroups(res.groups);
        setConnected(res.connected);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const toggle = (name: string) => {
    onChange(value.includes(name) ? value.filter((v) => v !== name) : [...value, name]);
  };

  // Entries that are not groups: individual tool names, or a group this node does
  // not have. Kept and shown rather than dropped — a skill authored against another
  // machine's servers is still a valid file, and silently discarding its entries on
  // save would corrupt it.
  const known = new Set((groups ?? []).map((g) => g.name));
  const extras = value.filter((v) => !known.has(v));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      {failed ? (
        <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--warn)' }}>
          Could not read the tool catalog — type group or tool names below instead.
        </div>
      ) : !groups ? (
        <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--text-dim)' }}>Reading catalog…</div>
      ) : (
        <>
          {!connected && (
            <div style={{ fontSize: 'var(--fs-meta)', color: 'var(--warn)' }}>
              No app window was connected when this list was built, so groups contributed by the UI
              (editor, files, layout…) are missing from it.
            </div>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
            {groups.map((g) => {
              const on = value.includes(g.name);
              return (
                <button
                  key={g.name}
                  type="button"
                  disabled={disabled}
                  onClick={() => toggle(g.name)}
                  aria-pressed={on}
                  title={`${g.description} (${g.tools} tool${g.tools === 1 ? '' : 's'})`}
                  style={{
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    padding: '0 var(--space-2)',
                    borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
                    background: on ? 'var(--accent-dim)' : 'transparent',
                    color: on ? 'var(--text-strong)' : 'var(--text-dim)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 'var(--fs-micro)',
                    letterSpacing: 'var(--tracking-badge)',
                    textTransform: 'uppercase',
                  }}
                >
                  {g.name}
                  <span style={{ opacity: 0.6 }}> {g.tools}</span>
                </button>
              );
            })}
          </div>
        </>
      )}

      <input
        value={extras.join(', ')}
        disabled={disabled}
        placeholder="…or individual tool names: files.read, editor.proposeEdit"
        onChange={(e) => {
          const typed = e.target.value
            .split(',')
            .map((t) => t.trim())
            .filter(Boolean);
          // Keep the picked groups, replace only the free-text half.
          onChange([...value.filter((v) => known.has(v)), ...typed]);
        }}
        style={{
          width: '100%',
          boxSizing: 'border-box',
          background: 'var(--bg-inset)',
          color: 'var(--text)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          // Horizontal padding only: `controls.css` fixes this control's height and
          // strips its vertical padding (the One Height Rule) — see theming.mdx.
          padding: '0 var(--space-3)',
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--fs-meta)',
        }}
      />
    </div>
  );
}

export function SkillEditor({ skill, onDone }: { skill: Skill | null; onDone: () => void }) {
  const readOnly = skill?.scope === 'project';
  const [name, setName] = useState(skill?.name ?? '');
  const [description, setDescription] = useState(skill?.description ?? '');
  const [body, setBody] = useState(skill?.body ?? DEFAULT_BODY);
  const [tools, setTools] = useState<string[]>(skill?.allowedTools ?? []);
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
        allowedTools: tools,
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
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-5)',
        }}
      >
        <strong
          style={{
            fontSize: 'var(--fs-label)',
            textTransform: 'uppercase',
            letterSpacing: 'var(--tracking-display)',
          }}
        >
          {skill ? skill.name : 'New skill'}
        </strong>
        {readOnly && <Chip kind="warn">read-only</Chip>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--space-2)' }}>
          {!readOnly && (
            <Button
              intent="primary"
              size="sm"
              disabled={busy || !name.trim() || !description.trim()}
              onClick={() => void submit()}
            >
              Save
            </Button>
          )}
          <Button size="sm" onClick={onDone}>
            {readOnly ? 'Back' : 'Cancel'}
          </Button>
        </span>
      </div>

      {readOnly && (
        <div
          style={{
            fontSize: 'var(--fs-meta)',
            color: 'var(--text-dim)',
            marginBottom: 'var(--space-5)',
          }}
        >
          This is the repository's own file. Copy it to your skills to edit — a pane that rewrote a
          git-tracked file would be a surprise in someone's next diff.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
        <Field label="Name" hint="lowercase, hyphens; this is what the agent calls" required>
          <input
            value={name}
            disabled={readOnly || Boolean(skill)}
            onChange={(e) => setName(e.target.value)}
            placeholder="tidy-a-file"
          />
        </Field>

        <Field
          label="Description"
          hint="Rides EVERY turn — one sentence saying when to use this. It is the whole trigger, and the whole per-turn cost."
          required
        >
          <textarea
            value={description}
            disabled={readOnly}
            rows={2}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Tidy a source file the way this project likes it. Use before committing."
          />
        </Field>

        <Field
          label="Tools it needs"
          hint="Optional. These groups are activated in the same step use_skill returns the instructions, so the skill can act without a second round trip."
        >
          <ToolPicker value={tools} disabled={readOnly} onChange={setTools} />
        </Field>

        <Field label="Instructions" hint="Read only when the agent calls use_skill">
          <textarea
            value={body}
            disabled={readOnly}
            rows={16}
            spellCheck={false}
            onChange={(e) => setBody(e.target.value)}
            style={{ whiteSpace: 'pre' }}
          />
        </Field>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            color: 'var(--danger)',
            fontSize: 'var(--fs-body)',
            marginTop: 'var(--space-4)',
          }}
        >
          {error}
        </div>
      )}

      {preview && (
        <details style={{ marginTop: 'var(--space-5)' }}>
          <summary
            style={{
              cursor: 'pointer',
              fontSize: 'var(--fs-meta)',
              color: 'var(--text-dim)',
            }}
          >
            What the agent actually sees
          </summary>
          <div
            style={{
              fontSize: 'var(--fs-meta)',
              color: 'var(--text-dim)',
              margin: 'var(--space-3) 0 var(--space-1)',
            }}
          >
            Injected every turn:
          </div>
          <pre
            style={{
              margin: 0,
              padding: 'var(--space-3)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-inset)',
              whiteSpace: 'pre-wrap',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--fs-micro)',
              maxHeight: 160,
              overflow: 'auto',
            }}
          >
            {preview.catalog}
          </pre>
          <div
            style={{
              fontSize: 'var(--fs-meta)',
              color: 'var(--text-dim)',
              margin: 'var(--space-4) 0 var(--space-1)',
            }}
          >
            Returned by <code>use_skill(&apos;{skill?.name}&apos;)</code>
            {preview.groups.length > 0 && <> · also loads {preview.groups.join(', ')}</>}:
          </div>
          <pre
            style={{
              margin: 0,
              padding: 'var(--space-3)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-inset)',
              whiteSpace: 'pre-wrap',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--fs-micro)',
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
