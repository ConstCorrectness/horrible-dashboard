/**
 * Learn — the notebook's teaching strip (`training.learn`, right region).
 *
 * The rest of the right strip shows you *what* is happening (curves, the graph,
 * renders); this one says *what it means*, for someone learning AI research by
 * doing it. Three parts, each grounded in something real rather than generated:
 *
 * - **This cell** — the knobs and concepts the cell you are in actually names.
 *   Knob text is the recipe form's own help (`GET /training/learn/glossary`), so
 *   the strip and the form can never explain `warmup_ratio` two ways.
 * - **This run** — rule-based reads of the live curves (`learn/diagnose.ts`),
 *   each stating the rule that fired so it teaches how to read a loss curve.
 * - **Go deeper** — the view that answers the next question, or the agent with
 *   the question already written (drafted, never sent).
 */
import { useEffect, useMemo, useState } from 'react';

import { revealRegionView } from '../../../layout/controller';
import { usePaneParams } from '../../../panes';
import { registry } from '../../../registry';
import { draftInChat } from '../../agent/openSession';
import { getLearnGlossary, type LearnTerm } from '../api';
import { onTrainingEvent, watchRun } from '../client';
import { lastProjectId } from '../last-project';
import { diagnose, type Diagnosis, type Series, type Severity } from '../learn/diagnose';
import { focusKey, useFocusedCellId } from '../learn/focus';
import { conceptsIn, knobsIn, type Concept, type GlossaryTerm } from '../learn/terms';
import { openSession, useSession } from '../store';
import './learn.css';

/** Points kept per metric — enough for every rule, bounded for a long run. */
const KEEP = 400;

let glossaryCache: Promise<LearnTerm[]> | null = null;
function loadGlossary(): Promise<LearnTerm[]> {
  glossaryCache ??= getLearnGlossary()
    .then((r) => r.terms)
    .catch(() => {
      glossaryCache = null;
      return [];
    });
  return glossaryCache;
}

/** The live metric series for one project, fed by the same `training` events the
 * Metrics strip draws. */
function useProjectSeries(projectId: string): Map<string, Series> {
  const [version, setVersion] = useState(0);
  const [store] = useState(() => new Map<string, Series>());
  useEffect(() => {
    store.clear();
    setVersion((v) => v + 1);
    if (!projectId) return;
    const ingest = (p: {
      projectId: string;
      step?: number | null;
      ts: number;
      values: Record<string, number>;
    }) => {
      if (p.projectId !== projectId) return;
      for (const [name, y] of Object.entries(p.values ?? {})) {
        const s = store.get(name) ?? { xs: [], ys: [] };
        s.xs.push(p.step ?? p.ts);
        s.ys.push(y);
        if (s.ys.length > KEEP) {
          s.xs.splice(0, s.xs.length - KEEP);
          s.ys.splice(0, s.ys.length - KEEP);
        }
        store.set(name, s);
      }
      setVersion((v) => v + 1);
    };
    const offMetrics = onTrainingEvent('metrics', ingest);
    const offBackfill = onTrainingEvent('run_backfill', (d) => {
      if (!d.points.some((p) => p.projectId === projectId)) return;
      store.clear();
      d.points.forEach(ingest);
    });
    // A run id defaults to its project id, so this backfills the usual case.
    watchRun(projectId);
    return () => {
      offMetrics();
      offBackfill();
    };
  }, [projectId, store]);
  // `version` is the change signal; the map is mutated in place.
  return useMemo(() => new Map(store), [store, version]);
}

export function LearnPane() {
  const params = usePaneParams();
  const [fallback] = useState(() => (params.projectId ? null : lastProjectId()));
  const projectId = String(params.projectId ?? fallback ?? '');
  const notebookPath = String(params.notebook ?? 'main.ipynb');

  const session = useMemo(
    () => (projectId ? openSession(projectId, notebookPath) : null),
    [projectId, notebookPath],
  );
  if (!session) {
    return (
      <div style={s.root}>
        <p style={s.dim}>Open a training project and this strip will explain what you are doing.</p>
      </div>
    );
  }
  return <LearnBody session={session} projectId={projectId} notebookPath={notebookPath} />;
}

function LearnBody({
  session,
  projectId,
  notebookPath,
}: {
  session: ReturnType<typeof openSession>;
  projectId: string;
  notebookPath: string;
}) {
  const state = useSession(session);
  const focusedId = useFocusedCellId(focusKey(projectId, notebookPath));
  const cell =
    state.cells.find((c) => c.id === focusedId) ??
    state.cells.find((c) => c.cell_type === 'code' && c.source.trim());
  const [glossary, setGlossary] = useState<LearnTerm[]>([]);
  useEffect(() => {
    let live = true;
    void loadGlossary().then((g) => live && setGlossary(g));
    return () => {
      live = false;
    };
  }, []);

  const source = cell?.cell_type === 'code' ? cell.source : '';
  const knobs = useMemo(() => knobsIn(source, glossary), [source, glossary]);
  const concepts = useMemo(() => conceptsIn(source), [source]);
  const series = useProjectSeries(projectId);
  const reads = useMemo(() => diagnose(series), [series]);
  const cellIndex = cell ? state.cells.indexOf(cell) + 1 : 0;

  return (
    <div style={s.root}>
      <section style={s.block}>
        <h3 style={s.head}>
          This cell {cellIndex > 0 && <span style={s.mono}>[{cellIndex}]</span>}
        </h3>
        {!source && <p style={s.dim}>Click into a code cell and its parts are explained here.</p>}
        {source && concepts.length === 0 && knobs.length === 0 && (
          <p style={s.dim}>
            Nothing in this cell is in the glossary yet — ask the agent below to walk through it.
          </p>
        )}
        {concepts.map((c, i) => (
          <ConceptCard key={c.title} concept={c} index={i} />
        ))}
        {knobs.length > 0 && (
          <div style={s.knobs}>
            {knobs.map((k, i) => (
              <KnobRow key={k.name} term={k} index={concepts.length + i} />
            ))}
          </div>
        )}
      </section>

      <section style={s.block}>
        <h3 style={s.head}>This run</h3>
        {reads.map((d, i) => (
          <DiagnosisRow key={d.id} d={d} index={i} />
        ))}
      </section>

      <section style={s.block}>
        <h3 style={s.head}>Go deeper</h3>
        <div style={s.actions}>
          <button type="button" onClick={() => revealRegionView('training.metrics')}>
            Curves
          </button>
          <button type="button" onClick={() => revealRegionView('training.modelgraph')}>
            Architecture
          </button>
          <button type="button" onClick={() => registry.openPanel('interpretability.architecture')}>
            Model explorer
          </button>
          <button
            type="button"
            disabled={!source}
            onClick={() =>
              // By cell number, not pasted source: the chat box is a single-line
              // input, which flattens code, and the agent's notebook tools read
              // the cell itself — including any edit made after this click.
              draftInChat(
                `Explain cell [${cellIndex}] of my ${projectId} notebook step by step for someone learning ML: read it with the notebook tools, then say what each line does, why it is needed, and what I could change to experiment.`,
              )
            }
          >
            Ask the agent
          </button>
        </div>
      </section>
    </div>
  );
}

function ConceptCard({ concept, index }: { concept: Concept; index: number }) {
  return (
    <article style={{ ...s.card, ...enter(index) }}>
      <h4 style={s.cardTitle}>{concept.title}</h4>
      <p style={s.body}>{concept.what}</p>
      <p style={{ ...s.body, ...s.why }}>{concept.why}</p>
    </article>
  );
}

function KnobRow({ term, index }: { term: GlossaryTerm; index: number }) {
  return (
    <div style={{ ...s.knob, ...enter(index) }}>
      <span style={s.chip}>{term.name}</span>
      <p style={s.body}>{term.help}</p>
    </div>
  );
}

const SEVERITY_COLOR: Record<Severity, string> = {
  good: 'var(--success)',
  info: 'var(--text-dim)',
  warn: 'var(--warn)',
  bad: 'var(--danger)',
};

function DiagnosisRow({ d, index }: { d: Diagnosis; index: number }) {
  return (
    <article style={{ ...s.card, borderLeftColor: SEVERITY_COLOR[d.severity], ...enter(index) }}>
      <h4 style={s.cardTitle}>{d.title}</h4>
      <p style={s.body}>{d.explanation}</p>
      <p style={s.rule}>rule · {d.rule}</p>
    </article>
  );
}

/** Staggered entrance, capped so a long cell does not take seconds to arrive. */
function enter(index: number): React.CSSProperties {
  return { animation: `learn-in 180ms ease-out ${Math.min(index, 6) * 40}ms both` };
}

const s: Record<string, React.CSSProperties> = {
  root: {
    height: '100%',
    overflow: 'auto',
    padding: '0.6rem',
    display: 'grid',
    gap: '0.9rem',
    alignContent: 'start',
  },
  block: { display: 'grid', gap: '0.45rem' },
  head: {
    margin: 0,
    paddingTop: '0.35rem',
    borderTop: '2px solid var(--accent)',
    fontSize: '11px',
    fontWeight: 700,
    letterSpacing: '0.14em',
    textTransform: 'uppercase',
    color: 'var(--text)',
    display: 'flex',
    gap: '0.5rem',
    alignItems: 'baseline',
  },
  mono: {
    fontFamily: 'var(--font-mono)',
    fontSize: '10.5px',
    color: 'var(--text-dim)',
    letterSpacing: 0,
  },
  dim: { margin: 0, fontSize: '12.5px', lineHeight: 1.45, color: 'var(--text-dim)' },
  card: {
    display: 'grid',
    gap: '0.25rem',
    padding: '0.45rem 0.55rem',
    borderLeft: '2px solid var(--border-strong)',
    background: 'var(--bg-raised)',
  },
  cardTitle: { margin: 0, fontSize: '12.5px', fontWeight: 600, color: 'var(--text)' },
  body: { margin: 0, fontSize: '12.5px', lineHeight: 1.45, color: 'var(--text)' },
  why: { color: 'var(--text-dim)' },
  rule: {
    margin: 0,
    fontFamily: 'var(--font-mono)',
    fontSize: '10.5px',
    lineHeight: 1.4,
    color: 'var(--text-dim)',
  },
  knobs: { display: 'grid', gap: '0.35rem' },
  knob: { display: 'grid', gap: '0.15rem' },
  chip: {
    justifySelf: 'start',
    padding: '1px 6px',
    fontFamily: 'var(--font-mono)',
    fontSize: '10.5px',
    color: 'var(--accent)',
    background: 'var(--accent-dim)',
  },
  actions: { display: 'flex', flexWrap: 'wrap', gap: '0.35rem' },
};
