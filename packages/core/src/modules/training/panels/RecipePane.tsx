import { useCallback, useEffect, useMemo, useState } from 'react';

import { usePaneParams } from '../../../panes';
import {
  applyRecipe,
  convertCheckpoint,
  getRecipe,
  listCheckpoints,
  recipeDocs,
  saveRecipe,
  type Checkpoint,
  type DocLink,
  type Recipe,
  type RecipeField,
  type RecipePayload,
  type ResolvedField,
} from '../api';

/**
 * The fine-tuning recipe: a form over a typed schema, and the loop that closes.
 *
 * Three things on this screen are load-bearing rather than cosmetic:
 *
 * - **Each field shows how it resolved against the installed library.** A field
 *   the installed `trl` renamed is emitted under the name it accepts; one it has
 *   never heard of is *dropped*, and the form says dropped rather than quietly
 *   rendering an input whose value goes nowhere.
 * - **The form admits it is not the whole API.** `TrainingArguments` has well
 *   over a hundred fields. The header says how many this form covers, because a
 *   form that looks complete is a form you trust to be complete.
 * - **Local metrics are unconditional.** The tracker picker is additive: the
 *   generated code always installs `ht.callback()`, so "none" means "no third
 *   party", never "no metrics".
 */

const dim = { color: 'var(--text-dim)' } as const;
const card = {
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '0.5rem 0.6rem',
  display: 'flex',
  flexDirection: 'column' as const,
  gap: '0.4rem',
};

const STATUS_STYLE: Record<ResolvedField['status'], React.CSSProperties> = {
  ok: {},
  renamed: { color: 'rgb(230 190 120)' },
  unsupported: { color: 'var(--danger, #f08a8a)', textDecoration: 'line-through' },
  unvalidated: { color: 'var(--text-dim)' },
};

function bytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(0)} MB`;
  return `${(value / 1024).toFixed(0)} KB`;
}

function FieldInput({
  field,
  value,
  onChange,
  disabled,
}: {
  field: RecipeField;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled: boolean;
}) {
  if (field.type === 'bool') {
    return (
      <input
        type="checkbox"
        checked={Boolean(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (field.type === 'select') {
    return (
      <select
        value={String(value ?? '')}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {field.options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === 'text') {
    return (
      <input
        type="text"
        value={String(value ?? '')}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: '12rem' }}
      />
    );
  }
  return (
    <input
      type="number"
      value={Number(value ?? 0)}
      disabled={disabled}
      step={field.type === 'float' ? 'any' : 1}
      onChange={(e) =>
        onChange(field.type === 'int' ? Math.round(Number(e.target.value)) : Number(e.target.value))
      }
      style={{ width: '7rem' }}
    />
  );
}

function FieldRow({
  field,
  resolved,
  value,
  onChange,
}: {
  field: RecipeField;
  resolved: ResolvedField | undefined;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const status = resolved?.status ?? 'unvalidated';
  const dropped = status === 'unsupported';
  return (
    <div
      style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: 12 }}
      title={field.help}
    >
      <span style={{ flex: '1 1 auto', ...STATUS_STYLE[status] }}>{field.label}</span>
      {status === 'renamed' && <span style={{ ...dim, fontSize: 10 }}>→ {resolved?.emit}</span>}
      {dropped && <span style={{ ...dim, fontSize: 10 }}>not in your version</span>}
      <FieldInput field={field} value={value} onChange={onChange} disabled={dropped} />
    </div>
  );
}

function ConvertCard({ projectId }: { projectId: string }) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [note, setNote] = useState('');
  const [selected, setSelected] = useState('');
  const [outType, setOutType] = useState('f16');
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    void listCheckpoints(projectId)
      .then((res) => {
        setCheckpoints(res.checkpoints);
        setNote(res.note);
        setSelected((current) =>
          current && res.checkpoints.some((c) => c.relPath === current)
            ? current
            : (res.checkpoints[0]?.relPath ?? ''),
        );
      })
      .catch(() => undefined);
  }, [projectId]);

  useEffect(refresh, [refresh]);

  const run = async () => {
    if (!selected) return;
    setBusy(true);
    setLog([]);
    try {
      await convertCheckpoint(projectId, { checkpoint: selected, outType }, (event) => {
        if (event.error) setLog((l) => [...l, `error: ${String(event.error)}`]);
        else if (event.status === 'done')
          setLog((l) => [
            ...l,
            `done — ${String(event.path)} (${bytes(Number(event.sizeBytes ?? 0))})`,
            event.servable
              ? 'It is in the llama.cpp catalog now; start a server on it from that pane.'
              : 'This is a LoRA adapter: llama-server loads it with --lora beside its base model, not on its own.',
          ]);
        else setLog((l) => [...l, String(event.status ?? '')]);
      });
    } catch (err) {
      setLog((l) => [...l, err instanceof Error ? err.message : String(err)]);
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const current = checkpoints.find((c) => c.relPath === selected);

  return (
    <div style={card}>
      <strong style={{ fontSize: 12 }}>Convert a checkpoint to GGUF</strong>
      <p style={{ ...dim, fontSize: 11, margin: 0, lineHeight: 1.45 }}>
        This is what closes the loop: the result lands in the llama.cpp module&rsquo;s managed model
        directory, so a model you trained is one this node serves.
      </p>
      {checkpoints.length === 0 ? (
        <p style={{ ...dim, fontSize: 11, margin: 0 }}>
          No checkpoints yet — train something, then come back. {note}
        </p>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={selected} onChange={(e) => setSelected(e.target.value)}>
              {checkpoints.map((c) => (
                <option key={c.relPath} value={c.relPath}>
                  {c.relPath} · {c.kind} · {bytes(c.sizeBytes)}
                </option>
              ))}
            </select>
            <select value={outType} onChange={(e) => setOutType(e.target.value)}>
              {['f16', 'bf16', 'f32', 'q8_0'].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button onClick={() => void run()} disabled={busy}>
              {busy ? 'Converting…' : 'Convert'}
            </button>
          </div>
          {current?.kind === 'lora' && (
            <p style={{ ...dim, fontSize: 11, margin: 0 }}>
              An adapter, not a model — it converts with llama.cpp&rsquo;s LoRA converter and its
              base model is read from <code>adapter_config.json</code>.
            </p>
          )}
        </>
      )}
      {log.length > 0 && (
        <pre
          style={{
            margin: 0,
            fontSize: 11,
            maxHeight: '8rem',
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
          }}
        >
          {log.join('\n')}
        </pre>
      )}
    </div>
  );
}

export function RecipePane() {
  const params = usePaneParams();
  const projectId = String(params.projectId ?? '');
  const [payload, setPayload] = useState<RecipePayload | null>(null);
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [docs, setDocs] = useState<DocLink[]>([]);
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    void getRecipe(projectId)
      .then((data) => {
        if (!alive) return;
        setPayload(data);
        setRecipe(data.recipe);
      })
      .catch((err) => alive && setStatus(err instanceof Error ? err.message : String(err)));
    void recipeDocs()
      .then((res) => alive && setDocs(res.links))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [projectId]);

  const resolvedBy = useMemo(() => {
    const map = new Map<string, ResolvedField>();
    for (const item of payload?.resolved ?? []) map.set(item.name, item);
    return map;
  }, [payload]);

  const groups = useMemo(() => {
    const out = new Map<string, RecipeField[]>();
    for (const field of payload?.fields ?? []) {
      if (field.target === 'lora' && recipe && !recipe.useLora) continue;
      const list = out.get(field.group) ?? [];
      list.push(field);
      out.set(field.group, list);
    }
    return out;
  }, [payload, recipe]);

  if (!projectId)
    return <p style={{ ...dim, padding: '0.6rem' }}>No project bound to this pane.</p>;
  if (!payload || !recipe)
    return <p style={{ ...dim, padding: '0.6rem' }}>{status || 'Loading…'}</p>;

  const intro = payload.introspection;
  const versions = Object.entries(intro.versions)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k} ${v}`)
    .join(' · ');
  const covered = payload.fields.length;
  const total = covered + (intro.extra.sft ?? 0) + (intro.extra.lora ?? 0);

  const update = (name: string, value: unknown) =>
    setRecipe({ ...recipe, values: { ...recipe.values, [name]: value } });

  const save = async () => {
    setStatus('Saving…');
    try {
      const next = await saveRecipe(projectId, recipe);
      setPayload(next);
      setStatus('Saved.');
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  const apply = async () => {
    setStatus('Writing cells…');
    try {
      const res = await applyRecipe(projectId, recipe);
      setStatus(`Wrote ${res.cells} cells into ${res.notebook}. Open the notebook to run them.`);
      setPayload(await getRecipe(projectId));
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        padding: '0.6rem',
        overflow: 'auto',
        height: '100%',
        fontSize: 12,
      }}
    >
      <div style={card}>
        <strong style={{ fontSize: 12 }}>Recipe · {projectId}</strong>
        <p style={{ ...dim, fontSize: 11, margin: 0, lineHeight: 1.45 }}>
          {intro.available ? (
            <>
              Validated against <strong>{versions}</strong> in this project&rsquo;s venv. This form
              covers {covered} of {total} knobs — edit the generated cell for the rest.
            </>
          ) : (
            <>
              Not validated: {intro.error || 'no library found'}. Every field is emitted as written
              and may be rejected at runtime.
            </>
          )}
        </p>
        {docs.map((link) => (
          <p key={link.url} style={{ ...dim, fontSize: 11, margin: 0 }}>
            <a href={link.url} target="_blank" rel="noreferrer">
              {link.label}
            </a>
            {link.version ? ` · docs for ${link.version}` : ''}
            {link.installedMismatch ? ` · you have ${link.installedMismatch}` : ''}
          </p>
        ))}
        {payload.warnings.map((warning) => (
          <p key={warning} style={{ color: 'var(--danger, #f08a8a)', fontSize: 11, margin: 0 }}>
            {warning}
          </p>
        ))}
      </div>

      <div style={card}>
        <strong style={{ fontSize: 12 }}>Model &amp; data</strong>
        <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <span style={{ ...dim, width: '7rem' }}>Base model</span>
          <input
            value={recipe.baseModel}
            onChange={(e) => setRecipe({ ...recipe, baseModel: e.target.value })}
            placeholder="meta-llama/Llama-3.2-1B"
            style={{ flex: 1 }}
          />
        </label>
        <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <span style={{ ...dim, width: '7rem' }}>Dataset</span>
          <input
            value={recipe.dataset}
            onChange={(e) => setRecipe({ ...recipe, dataset: e.target.value })}
            placeholder="trl-lib/Capybara"
            style={{ flex: 1 }}
          />
        </label>
        <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <span style={{ ...dim, width: '7rem' }}>Output dir</span>
          <input
            value={recipe.outputDir}
            onChange={(e) => setRecipe({ ...recipe, outputDir: e.target.value })}
            style={{ flex: 1 }}
          />
        </label>
        <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={recipe.useLora}
            onChange={(e) => setRecipe({ ...recipe, useLora: e.target.checked })}
          />
          <span>LoRA (adapters instead of a full fine-tune)</span>
        </label>
      </div>

      {[...groups.entries()].map(([group, fields]) => (
        <div key={group} style={card}>
          <strong style={{ fontSize: 12, textTransform: 'capitalize' }}>{group}</strong>
          {fields.map((field) => (
            <FieldRow
              key={field.name}
              field={field}
              resolved={resolvedBy.get(field.name)}
              value={recipe.values[field.name]}
              onChange={(value) => update(field.name, value)}
            />
          ))}
        </div>
      ))}

      <div style={card}>
        <strong style={{ fontSize: 12 }}>Metrics</strong>
        <p style={{ ...dim, fontSize: 11, margin: 0, lineHeight: 1.45 }}>
          The metrics pane is fed unconditionally by <code>ht.callback()</code>, which every
          generated recipe installs. These are <em>additional</em> destinations — &ldquo;none&rdquo;
          means no third party, not no metrics.
        </p>
        <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
          {payload.trackers
            .filter((t) => t !== 'none')
            .map((tracker) => (
              <label key={tracker} style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
                <input
                  type="checkbox"
                  checked={recipe.trackers.includes(tracker)}
                  onChange={(e) =>
                    setRecipe({
                      ...recipe,
                      trackers: e.target.checked
                        ? [...recipe.trackers.filter((t) => t !== 'none'), tracker]
                        : recipe.trackers.filter((t) => t !== tracker),
                    })
                  }
                />
                {tracker}
              </label>
            ))}
        </div>
        <p style={{ ...dim, fontSize: 11, margin: 0 }}>
          W&amp;B and MLflow credentials live in the <strong>Experiment trackers</strong> connector
          tile, never in settings — and reach the run through its environment at spawn.
        </p>
      </div>

      <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button onClick={() => void save()}>Save</button>
        <button onClick={() => void apply()}>Write cells into the notebook</button>
        <span style={{ ...dim, fontSize: 11 }}>{status}</span>
      </div>

      <ConvertCard projectId={projectId} />
    </div>
  );
}
