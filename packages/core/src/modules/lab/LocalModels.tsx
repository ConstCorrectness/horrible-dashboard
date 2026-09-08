import { useCallback, useEffect, useState } from 'react';

import { apiGet } from '../../api';
import { DataList, DataRow, type RowKind } from '../../DataList';
import { registry } from '../../registry';

/**
 * Everything this node made, with where it came from and how it scored.
 *
 * The join `training/lineage.py` recorded and nothing rendered. Five disjoint views
 * of "the models I have" existed — the llama.cpp GGUF catalog, Hugging Face
 * downloads, chat-provider model *names*, per-project checkpoints, and the lineage
 * table — so "which fine-tune is this, and did it beat its base?" was answered by
 * writing a join by hand in the database console.
 *
 * A **section of `lab.hub`**, not a pane of its own. The pane is already the
 * browse-for-a-model surface with a section mechanism; adding a 76th opener to a
 * launcher the consolidation effort spent months shrinking would be the wrong trade
 * for one table. Models / Datasets are what the Hub has; Local is what *you* have.
 */

interface EvalScore {
  runId: string;
  suiteId: string;
  passed: number;
  total: number;
  completed: number;
  finishedAt: string;
}

interface LocalModel {
  ggufPath: string;
  projectId: string;
  checkpoint: string;
  baseModel: string;
  outType: string;
  isAdapter: boolean;
  localtrackRunId: string;
  createdAt: number;
  present: boolean;
  sizeBytes: number;
  evals: EvalScore[];
  metrics: Record<string, number>;
}

const basename = (path: string): string => path.split(/[\\/]/).pop() || path;

function bytes(n: number): string {
  if (!n) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** The best finished score, or null when nothing has scored this file. */
function bestScore(evals: EvalScore[]): EvalScore | null {
  if (evals.length === 0) return null;
  return evals.reduce((best, e) =>
    e.passed / (e.total || 1) > best.passed / (best.total || 1) ? e : best,
  );
}

export function LocalModels() {
  const [models, setModels] = useState<LocalModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    apiGet<{ models: LocalModel[] }>('/training/inventory')
      .then((r) => setModels(r.models))
      .catch((e: unknown) => {
        setModels([]);
        setError(e instanceof Error ? e.message : String(e));
      });
  }, []);

  useEffect(load, [load]);

  if (models === null) return <div style={{ padding: 'var(--space-6)' }}>Loading…</div>;

  if (error) {
    // Rendered, not swallowed. An empty table and a failed request look identical,
    // and this one is a join over four stores — plenty of ways to fail.
    return (
      <div style={{ padding: 'var(--space-6)', color: 'var(--danger)' }}>
        Could not read the model inventory: {error}{' '}
        <button className="btn-mini" onClick={load}>
          Retry
        </button>
      </div>
    );
  }

  if (models.length === 0) {
    return (
      <div
        style={{
          padding: 'var(--space-7)',
          color: 'var(--text-dim)',
          fontSize: 'var(--fs-body)',
          lineHeight: 1.6,
        }}
      >
        {/* Says what fills it, not just that it is empty. This lists models this node
            *made*; a GGUF you downloaded has no provenance to show and belongs in
            the llama.cpp catalog, which is a different question. */}
        Nothing converted yet. Fine-tune a project, convert a checkpoint to GGUF, and it appears
        here with the base model it came from, the run that produced it, and any eval scores it has
        earned.
      </div>
    );
  }

  return (
    <div style={{ overflow: 'auto', height: '100%' }}>
      <DataList label="Models this node made">
        {models.map((m, i) => {
          const best = bestScore(m.evals);
          const loss = m.metrics['train/loss'] ?? m.metrics['eval/loss'];
          // A missing file is the one state worth colouring: you can read this
          // fine-tune's whole history and cannot serve it. `idle` for a model
          // nothing has scored — not `ok`, which would claim a verdict that was
          // never reached.
          const kind: RowKind = !m.present ? 'warn' : best ? 'ok' : 'idle';
          return (
            <DataRow
              key={m.ggufPath}
              index={i}
              kind={kind}
              // No tick on an unscored model: `hideMark` where the kind is a
              // category rather than a verdict.
              hideMark={kind === 'idle'}
              title={basename(m.ggufPath)}
              badge={m.isAdapter ? 'LORA' : m.outType || undefined}
              meta={[
                best ? `${best.passed}/${best.total}` : 'unscored',
                ...(loss !== undefined ? [`loss ${loss.toFixed(4)}`] : []),
                ...(m.sizeBytes ? [bytes(m.sizeBytes)] : []),
              ]}
              metaTone={!m.present ? 'warn' : undefined}
              footnotes={
                !m.present ? 'The file is gone — its history is here, but it cannot be served.' : undefined
              }
              actions={<RowActions model={m} />}
            >
              {m.baseModel ? `Fine-tune of ${m.baseModel}` : 'No base model recorded'}
              {m.checkpoint ? ` · ${m.checkpoint}` : ''}
            </DataRow>
          );
        })}
      </DataList>
    </div>
  );
}

/** The three places a row leads. Every one is a param deep link. */
function RowActions({ model }: { model: LocalModel }) {
  return (
    <>
      {model.projectId && (
        <button
          className="btn-mini"
          title={`Open ${model.projectId}'s recipe`}
          onClick={() =>
            registry.openPanel('training.recipe', { params: { projectId: model.projectId } })
          }
        >
          Recipe
        </button>
      )}
      {model.localtrackRunId && (
        <button
          className="btn-mini"
          title="Open the training curves this model came from"
          onClick={() =>
            registry.openPanel('localtrack.workspace', {
              params: { projectId: model.projectId },
            })
          }
        >
          Curves
        </button>
      )}
      <button
        className="btn-mini"
        disabled={!model.present}
        title={
          model.present
            ? 'Score this model against an eval suite'
            : 'The file is gone — there is nothing to serve or score'
        }
        onClick={() => {
          registry.openPanel('evals.hub', { params: { modelPath: model.ggufPath } });
          void registry.runCommand('section.show:evals.hub:run');
        }}
      >
        Score
      </button>
    </>
  );
}
