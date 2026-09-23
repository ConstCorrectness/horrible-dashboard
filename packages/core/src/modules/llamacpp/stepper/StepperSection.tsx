/**
 * The layer debugger: step through a recorded forward pass the way a debugger steps
 * through lines.
 *
 * The Traces section is a record list you can pin and scrub; the Lens is a grid of
 * words. This is the third reading — **execution order**. The cursor is a program
 * counter over the trace's records (see `program.ts`), and every verb a debugger
 * has maps onto it: step into the next node, step over the rest of a block, step out
 * of the pass, step *back* (a snapshot makes reverse execution free), and continue to
 * a breakpoint — including conditional ones that no source debugger could offer,
 * "stop where the lens first predicts ' Paris'".
 *
 * Two things tie it to the rest of the app, both through the model-locus bus rather
 * than an import: the cursor is **published** (layer, stage, step, position), so the
 * model explorer's architecture diagram follows it like a program counter drawn on
 * the map; and a locus set by someone else — a click on a block in the explorer,
 * `dash.lens.focus(layer=…)`, an agent's `lens.*` tool — **moves** the cursor.
 *
 * Keyboard goes through `keymap/` via the `bindLayerStepper` handle, never a
 * `keydown` listener here.
 */
import './stepper.css';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { setModelLocus, useModelLocus } from '../../../model-locus';
import {
  getLensGrid,
  getTrace,
  getTraceProfile,
  listTraces,
  type TraceDetail,
  type TraceSummary,
} from '../api';
import { KIND_LABELS, type NodeKind } from '../node-kind';
import { getPins } from '../pins';
import { bindLayerStepper } from './actions';
import {
  buildProgram,
  callStack,
  continueBack,
  continueForward,
  describeBreakpoint,
  lensKey,
  reverseStepOver,
  stepBack,
  stepForLayer,
  stepInto,
  stepOut,
  stepOver,
  stopsAt,
  type Breakpoint,
  type HitContext,
  type Program,
} from './program';
import { StageView } from './StageView';
import { subscribeStepperTarget, takeStepperTarget, type StepperTarget } from './target';
import { Timeline } from './Timeline';

/**
 * Survives the section unmounting (switching to Lens and back must not lose your
 * place), per trace. Module state rather than a setting: a cursor is a session
 * fact, not a preference, and persisting it would reopen tomorrow on today's step.
 */
interface Session {
  cursor: number;
  position: number;
  breakpoints: Breakpoint[];
}
const sessions = new Map<string, Session>();
let lastTraceId = '';

const STAGE_ORDER: NodeKind[] = ['attention', 'ffn', 'moe', 'ssm', 'residual', 'norm', 'output'];
const PLAY_MS = 450;
/** Profiles are one request per pass; a long generation should not fire hundreds. */
const MAX_PROFILED_PASSES = 24;

let nextId = 1;
const newId = () => `bp${nextId++}`;

export function StepperSection() {
  const [traces, setTraces] = useState<TraceSummary[] | null>(null);
  const [traceId, setTraceId] = useState(lastTraceId);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [error, setError] = useState('');
  const [cursor, setCursor] = useState(0);
  const [position, setPosition] = useState(0);
  const [breakpoints, setBreakpoints] = useState<Breakpoint[]>([]);
  const [rms, setRms] = useState<Map<number, number | null>>(new Map());
  const [lensTop1, setLensTop1] = useState<Map<string, number> | undefined>(undefined);
  const [lensChoices, setLensChoices] = useState<{ id: number; text: string; layer: number }[]>([]);
  const [playing, setPlaying] = useState(false);
  const [status, setStatus] = useState('');
  const pendingTarget = useRef<StepperTarget | null>(takeStepperTarget());

  // ── loading ─────────────────────────────────────────────────────────────

  useEffect(() => {
    let alive = true;
    void listTraces()
      .then((list) => {
        if (!alive) return;
        setTraces(list.traces);
        const wanted = pendingTarget.current?.traceId;
        setTraceId((current) => wanted || current || list.traces[0]?.traceId || '');
      })
      .catch((err: unknown) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(
    () =>
      subscribeStepperTarget((next) => {
        pendingTarget.current = next;
        setTraceId(next.traceId);
        // Same trace: the load effect will not refire, so apply it here.
        if (next.traceId === traceIdRef.current && programRef.current) applyTarget();
      }),
    // `applyTarget` reads refs only.
    [],
  );

  const traceIdRef = useRef(traceId);
  traceIdRef.current = traceId;

  useEffect(() => {
    if (!traceId) return;
    lastTraceId = traceId;
    let alive = true;
    setDetail(null);
    setError('');
    setRms(new Map());
    setLensTop1(undefined);
    setLensChoices([]);
    void getTrace(traceId)
      .then((d) => alive && setDetail(d))
      .catch((err: unknown) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [traceId]);

  const program = useMemo<Program | null>(
    () => (detail ? buildProgram(detail.records) : null),
    [detail],
  );
  const programRef = useRef<Program | null>(null);
  programRef.current = program;

  const applyTarget = useCallback(() => {
    const p = programRef.current;
    const target = pendingTarget.current;
    if (!p || !target) return;
    pendingTarget.current = null;
    let step = target.step ?? 0;
    if (target.layer !== undefined) {
      step = stepForLayer(p, target.passIndex ?? 0, target.layer) ?? 0;
    }
    setCursor(Math.max(0, Math.min(p.steps.length - 1, step)));
  }, []);

  // Restore (or start) this trace's session once its program exists.
  useEffect(() => {
    if (!program || !detail) return;
    const saved = sessions.get(detail.trace.traceId);
    setCursor(Math.min(saved?.cursor ?? 0, Math.max(0, program.steps.length - 1)));
    setPosition(
      saved?.position ?? Math.max(0, detail.tokens.filter((t) => !t.generated).length - 1),
    );
    setBreakpoints(saved?.breakpoints ?? []);
    applyTarget();
  }, [program, detail, applyTarget]);

  useEffect(() => {
    if (!detail) return;
    sessions.set(detail.trace.traceId, { cursor, position, breakpoints });
  }, [detail, cursor, position, breakpoints]);

  // Statistics for the timeline and for stat breakpoints: one profile per pass.
  useEffect(() => {
    if (!program || !traceId) return;
    let alive = true;
    const passes = program.passes.slice(0, MAX_PROFILED_PASSES);
    void Promise.all(passes.map((p) => getTraceProfile(traceId, p, 'rms').catch(() => null))).then(
      (profiles) => {
        if (!alive) return;
        const map = new Map<number, number | null>();
        for (const profile of profiles) {
          for (const point of profile?.points ?? []) map.set(point.index, point.value);
        }
        setRms(map);
      },
    );
    return () => {
      alive = false;
    };
  }, [program, traceId]);

  // Lens top-1 at the watched position, per layer — only needed once a lens
  // breakpoint exists, or to offer one. Read at the last prompt position of the
  // prompt pass by default, since that is where "the answer" is being formed.
  const lensPass = program?.steps[cursor]?.passIndex ?? 0;
  const loadLens = useCallback(() => {
    if (!traceId) return;
    setStatus('Reading the lens at every layer…');
    void getLensGrid(traceId, { k: 1, positions: [position], passIndex: lensPass })
      .then((grid) => {
        const top = new Map<string, number>();
        const seen = new Map<number, { id: number; text: string; layer: number }>();
        grid.layers.forEach((layer, row) => {
          const cell = grid.cells[row]?.[0];
          if (!cell?.ids.length) return;
          top.set(lensKey(lensPass, layer), cell.ids[0]);
          if (!seen.has(cell.ids[0]))
            seen.set(cell.ids[0], { id: cell.ids[0], text: cell.texts[0], layer });
        });
        setLensTop1(top);
        setLensChoices([...seen.values()]);
        setStatus('');
      })
      .catch((err: unknown) =>
        setStatus(`Lens unavailable: ${err instanceof Error ? err.message : String(err)}`),
      );
  }, [traceId, position, lensPass]);

  useEffect(() => {
    if (breakpoints.some((b) => b.kind === 'lens')) loadLens();
    // Only when the position or pass under the lens changes, not per breakpoint edit.
  }, [position, lensPass]);

  // ── verbs ────────────────────────────────────────────────────────────────

  const ctx = useMemo<HitContext>(() => ({ stats: { rms }, lensTop1 }), [rms, lensTop1]);
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const bpRef = useRef(breakpoints);
  bpRef.current = breakpoints;
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;

  const move = useCallback((next: (p: Program, i: number) => number) => {
    const p = programRef.current;
    if (!p) return;
    setStatus('');
    setCursor(next(p, cursorRef.current));
  }, []);

  const runContinue = useCallback((dir: 1 | -1) => {
    const p = programRef.current;
    if (!p) return;
    const result = (dir === 1 ? continueForward : continueBack)(
      p,
      cursorRef.current,
      bpRef.current,
      ctxRef.current,
    );
    setCursor(result.step);
    setStatus(
      result.hit
        ? `Stopped: ${describeBreakpoint(result.hit)}`
        : bpRef.current.length
          ? `No breakpoint ${dir === 1 ? 'ahead' : 'behind'} — ran to the ${dir === 1 ? 'end' : 'start'}`
          : `No breakpoints set — ran to the ${dir === 1 ? 'end' : 'start'}`,
    );
  }, []);

  const toggleLayerBreakpoint = useCallback(() => {
    const p = programRef.current;
    const layer = p?.steps[cursorRef.current]?.layer;
    if (layer === null || layer === undefined) return;
    setBreakpoints((list) =>
      list.some((b) => b.kind === 'layer' && b.layer === layer)
        ? list.filter((b) => !(b.kind === 'layer' && b.layer === layer))
        : [...list, { id: newId(), kind: 'layer', layer }],
    );
  }, []);

  // Play = repeated step over, stopping on any breakpoint crossed inside the frame.
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      const p = programRef.current;
      if (!p) return;
      const from = cursorRef.current;
      const to = stepOver(p, from);
      for (let j = from + 1; j <= to; j++) {
        const hit = stopsAt(p, j, bpRef.current, ctxRef.current);
        if (hit) {
          setCursor(j);
          setPlaying(false);
          setStatus(`Stopped: ${describeBreakpoint(hit)}`);
          return;
        }
      }
      setCursor(to);
      if (to >= p.steps.length - 1 || to === from) setPlaying(false);
    }, PLAY_MS);
    return () => window.clearInterval(timer);
  }, [playing]);

  useEffect(() => {
    bindLayerStepper({
      stepInto: () => move(stepInto),
      stepBack: () => move(stepBack),
      stepOver: () => move(stepOver),
      reverseStepOver: () => move(reverseStepOver),
      stepOut: () => move(stepOut),
      continueForward: () => runContinue(1),
      continueBack: () => runContinue(-1),
      toggleBreakpoint: toggleLayerBreakpoint,
      togglePlay: () => setPlaying((v) => !v),
      restart: () => {
        setPlaying(false);
        move(() => 0);
      },
    });
    return () => bindLayerStepper(null);
  }, [move, runContinue, toggleLayerBreakpoint]);

  // ── the model-locus bus, both directions ─────────────────────────────────

  const step = program?.steps[cursor] ?? null;
  const modelSha = detail?.trace.modelSha;
  useEffect(() => {
    if (!step || !traceId) return;
    setModelLocus(
      {
        traceId,
        modelSha,
        layer: step.layer ?? (step.stage === 'output' ? undefined : -1),
        stage: step.stage,
        step: step.step,
        passIndex: step.passIndex,
        position,
      },
      'stepper',
    );
  }, [step, traceId, modelSha, position]);

  const locus = useModelLocus();
  const handled = useRef(locus);
  useEffect(() => {
    if (locus === handled.current) return;
    handled.current = locus;
    const p = programRef.current;
    if (!p || locus.source === 'stepper') return;
    if (locus.traceId && locus.traceId !== traceIdRef.current) return;
    if (locus.modelSha && modelSha && locus.modelSha !== modelSha) return;
    if (typeof locus.step === 'number' && locus.step >= 0 && locus.step < p.steps.length) {
      setCursor(locus.step);
      return;
    }
    const here = p.steps[cursorRef.current];
    // A follower echoing the block we are already in (the explorer re-selecting
    // what it was just shown) must not yank the cursor back to the block's start.
    const sameBlock =
      here &&
      (locus.layer === -1 ? here.layer === null : here.layer === locus.layer) &&
      (!locus.stage || locus.stage === here.stage);
    if (typeof locus.layer === 'number' && !sameBlock) {
      const pass = locus.passIndex ?? here?.passIndex ?? 0;
      const target = stepForLayer(
        p,
        pass,
        locus.layer === -1 ? null : locus.layer,
        locus.stage as NodeKind | undefined,
      );
      if (target === null) setStatus(`Layer ${locus.layer} was not traced in this pass`);
      else setCursor(target);
    }
    if (typeof locus.position === 'number') setPosition(locus.position);
  }, [locus, modelSha]);

  // ── render ───────────────────────────────────────────────────────────────

  if (error) return <p className="llama-note">{error}</p>;
  if (traces && !traces.length)
    return (
      <div className="llama-section">
        <p className="llama-meta">
          No traces yet. Record one in the <strong>Traces</strong> section (turn attention on to
          step through attention heads), then come back here to step through it layer by layer.
        </p>
      </div>
    );
  if (!detail || !program || !step) return <p className="llama-meta">Loading the trace…</p>;

  const tokens = detail.tokens;
  const passSteps = program.steps.filter((s) => s.passIndex === step.passIndex);
  const stepInPass = passSteps.findIndex((s) => s.step === step.step);
  const pins = getPins(detail.trace.modelName);
  const layerHasBp = breakpoints.some((b) => b.kind === 'layer' && b.layer === step.layer);

  return (
    <div className="llama-section stp">
      <div className="stp-head-bar">
        <select
          aria-label="Trace"
          value={traceId}
          onChange={(e) => setTraceId(e.target.value)}
          style={{ padding: '0 0.5rem' }}
        >
          {(traces ?? []).map((t) => (
            <option key={t.traceId} value={t.traceId}>
              {t.modelName} · {t.prompt.slice(0, 40)}
              {t.attention ? ' · attn' : ''}
            </option>
          ))}
        </select>
        <div className="stp-controls" role="toolbar" aria-label="Stepper controls">
          <button onClick={() => move(() => 0)} title="Restart (Home)">
            Restart
          </button>
          <button onClick={() => runContinue(-1)} title="Reverse continue (Shift+C)">
            ◂◂
          </button>
          <button onClick={() => move(reverseStepOver)} title="Reverse step over (Up)">
            Back over
          </button>
          <button onClick={() => move(stepBack)} title="Step back (Left)">
            Back
          </button>
          <button className="stp-primary" onClick={() => move(stepInto)} title="Step into (Right)">
            Step
          </button>
          <button
            className="stp-primary"
            onClick={() => move(stepOver)}
            title="Step over (Down or F10)"
          >
            Over
          </button>
          <button onClick={() => move(stepOut)} title="Step out of this pass (Shift+Right)">
            Out
          </button>
          <button onClick={() => runContinue(1)} title="Continue to next breakpoint (C)">
            ▸▸
          </button>
          <button onClick={() => setPlaying((v) => !v)} title="Play layer by layer (Space)">
            {playing ? 'Pause' : 'Play'}
          </button>
        </div>
        <span className="stp-pc">
          <span className="stp-num">
            {step.step + 1}/{program.steps.length}
          </span>{' '}
          · pass {step.passIndex} · node {stepInPass + 1}/{passSteps.length}
        </span>
      </div>

      <div className="stp-tokens" role="listbox" aria-label="Token position">
        {tokens.map((t) => (
          <button
            key={t.index}
            role="option"
            aria-selected={t.index === position}
            className={`stp-token${t.index === position ? ' stp-token-on' : ''}${
              t.generated ? ' stp-token-gen' : ''
            }`}
            onClick={() => setPosition(t.index)}
            title={`position ${t.index} · id ${t.id}`}
          >
            {t.text || '␣'}
          </button>
        ))}
      </div>

      <div className="stp-grid">
        <aside className="stp-col">
          <h4 className="stp-h">Call stack</h4>
          <ol className="stp-stack">
            {callStack(step).map((frame, i) => (
              <li key={i} style={{ paddingLeft: `${i * 0.6}rem` }}>
                <span className={i === 2 ? `stp-stage-chip stp-stage-${step.stage}` : ''}>
                  {frame.label}
                </span>
                {frame.detail && <span className="stp-dim"> {frame.detail}</span>}
              </li>
            ))}
          </ol>

          <h4 className="stp-h">Breakpoints</h4>
          <BreakpointEditor
            step={step}
            breakpoints={breakpoints}
            layerHasBp={layerHasBp}
            onToggleLayer={toggleLayerBreakpoint}
            onAdd={(bp) => setBreakpoints((l) => [...l, bp])}
            onRemove={(id) => setBreakpoints((l) => l.filter((b) => b.id !== id))}
            lensChoices={lensChoices}
            onLoadLens={loadLens}
            lensReady={lensTop1 !== undefined}
          />
        </aside>

        <section className="stp-main" aria-label={`${step.name} view`}>
          <div className="stp-main-head">
            <span className={`stp-stage-chip stp-stage-${step.stage}`}>
              {KIND_LABELS[step.stage]}
            </span>
            <code>{step.name}</code>
            <span className="stp-dim">
              {step.op} · {step.shape.join('×')} · {step.fidelity}
            </span>
          </div>
          <StageView
            traceId={traceId}
            step={step}
            tokens={tokens}
            position={position}
            onPosition={setPosition}
          />
        </section>

        <aside className="stp-col">
          <h4 className="stp-h">Locals</h4>
          <dl className="stp-locals">
            <dt>rms</dt>
            <dd className="stp-num">{fmt(rms.get(step.recordIndex))}</dd>
            <dt>block</dt>
            <dd className="stp-num">{step.layer ?? '—'}</dd>
            <dt>record</dt>
            <dd className="stp-num">#{step.recordIndex}</dd>
          </dl>
          <h4 className="stp-h">Watch</h4>
          {pins.length ? (
            <ul className="stp-watch">
              {pins.map((name) => {
                const hit = program.steps.find(
                  (s) => s.name === name && s.passIndex === step.passIndex,
                );
                // Debugger semantics: a node later in this pass has not run yet at
                // the cursor, and showing its value would be reading the future.
                const future = hit ? hit.step > step.step : false;
                return (
                  <li key={name} className={future ? 'stp-future' : ''}>
                    <button className="stp-watch-name" onClick={() => hit && setCursor(hit.step)}>
                      {name}
                    </button>
                    <span className="stp-num">
                      {!hit ? 'not traced' : future ? 'not yet run' : fmt(rms.get(hit.recordIndex))}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="llama-meta">Pin nodes in the Traces section to watch them here.</p>
          )}
        </aside>
      </div>

      <Timeline
        program={program}
        cursor={cursor}
        rms={rms}
        breakpoints={breakpoints}
        ctx={ctx}
        onSeek={(s) => {
          setPlaying(false);
          setCursor(s);
        }}
      />
      <p className="stp-caption" aria-live="polite">
        {status ||
          'Bars: one per node, coloured by stage, height log(1 + rms). Arrows step, Down/F10 steps over a block, C continues, B toggles a breakpoint on this block.'}
      </p>
    </div>
  );
}

function fmt(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(3) : '—';
}

function BreakpointEditor({
  step,
  breakpoints,
  layerHasBp,
  onToggleLayer,
  onAdd,
  onRemove,
  lensChoices,
  onLoadLens,
  lensReady,
}: {
  step: { layer: number | null; stage: NodeKind; name: string };
  breakpoints: Breakpoint[];
  layerHasBp: boolean;
  onToggleLayer: () => void;
  onAdd: (bp: Breakpoint) => void;
  onRemove: (id: string) => void;
  lensChoices: { id: number; text: string; layer: number }[];
  onLoadLens: () => void;
  lensReady: boolean;
}) {
  const [statStage, setStatStage] = useState<NodeKind | 'any'>('residual');
  const [threshold, setThreshold] = useState('100');
  return (
    <div className="stp-bp">
      <ul className="stp-bp-list">
        {breakpoints.map((bp) => (
          <li key={bp.id}>
            <span className="stp-bp-dot" aria-hidden="true" />
            <span>{describeBreakpoint(bp)}</span>
            <button className="llama-linkbtn" onClick={() => onRemove(bp.id)} title="Remove">
              ✕
            </button>
          </li>
        ))}
        {!breakpoints.length && <li className="stp-dim">None — Continue runs to the end.</li>}
      </ul>
      <div className="stp-bp-add">
        {step.layer !== null && (
          <button className="btn-mini" onClick={onToggleLayer}>
            {layerHasBp ? 'Clear' : 'Break at'} block {step.layer}
          </button>
        )}
        <button
          className="btn-mini"
          onClick={() => onAdd({ id: newId(), kind: 'stage', stage: step.stage })}
        >
          Every {step.stage}
        </button>
        <button
          className="btn-mini"
          onClick={() => onAdd({ id: newId(), kind: 'node', name: step.name })}
        >
          This node
        </button>
      </div>
      <div className="stp-bp-add">
        <select
          aria-label="Stage for the threshold breakpoint"
          value={statStage}
          onChange={(e) => setStatStage(e.target.value as NodeKind | 'any')}
          style={{ padding: '0 0.4rem' }}
        >
          <option value="any">any node</option>
          {STAGE_ORDER.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <span className="stp-dim">rms &gt;</span>
        <input
          type="number"
          aria-label="Threshold"
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
          style={{ width: '4.5rem', padding: '0 0.4rem' }}
        />
        <button
          className="btn-mini"
          onClick={() => {
            const above = Number(threshold);
            if (Number.isFinite(above))
              onAdd({ id: newId(), kind: 'stat', stage: statStage, stat: 'rms', above });
          }}
        >
          Add
        </button>
      </div>
      <div className="stp-bp-add">
        {lensReady ? (
          lensChoices.length ? (
            <select
              aria-label="Break when the lens predicts"
              value=""
              onChange={(e) => {
                const choice = lensChoices.find((c) => String(c.id) === e.target.value);
                if (choice)
                  onAdd({ id: newId(), kind: 'lens', tokenId: choice.id, text: choice.text });
              }}
              style={{ padding: '0 0.4rem' }}
            >
              <option value="">Break when the lens first says…</option>
              {lensChoices.map((c) => (
                <option key={c.id} value={c.id}>
                  {JSON.stringify(c.text)} (from block {c.layer})
                </option>
              ))}
            </select>
          ) : (
            <span className="stp-dim">The lens read nothing at this position.</span>
          )
        ) : (
          <button className="btn-mini" onClick={onLoadLens}>
            Lens breakpoint…
          </button>
        )}
      </div>
    </div>
  );
}
