/**
 * The one control that turns an estimate into a measurement.
 *
 * Everything else in this pane is our own arithmetic — shapes propagated in pure
 * Python, parameter counts summed from a table — and it is chipped `estimated`
 * throughout because that is what it is. This builds the generated module in a
 * training project's venv and runs a real forward pass.
 *
 * It renders **three** states, and the third is the point. "It ran" and "it raised"
 * are the easy ones; **"we could not ask"** — no project, no venv, no torch — is the
 * state that everything gets wrong by collapsing into silence, and a model reported
 * as validated when nothing validated it is worse than not offering the button.
 */
import { formatCount, type HandoffResult, type ProbeResult, type TrainingProject } from './graph';

function Outcome({ result }: { result: ProbeResult }) {
  if (result.status === 'ran') {
    const agrees = result.agrees;
    return (
      <div className="mg-probe-out mg-probe-ok">
        <span className="mg-probe-line">
          Ran in {result.durationMs}ms on torch {result.torchVersion} → output{' '}
          <span className="mg-mono">[{result.outputShape.join(', ')}]</span>
        </span>
        <span className="mg-probe-line">
          <span className="mg-mono">{formatCount(result.totalParams ?? 0)}</span> parameters,
          measured
          {agrees === true && ' — matching the estimate exactly.'}
          {agrees === false && (
            <span className="mg-probe-mismatch">
              {' '}
              — but the pane estimated{' '}
              <span className="mg-mono">{formatCount(result.estimatedParams ?? 0)}</span>. The
              measurement is right and the estimate is wrong; the cost overlay has been off by{' '}
              {formatCount(Math.abs((result.totalParams ?? 0) - (result.estimatedParams ?? 0)))}.
            </span>
          )}
        </span>
      </div>
    );
  }

  if (result.status === 'failed') {
    return (
      <div className="mg-probe-out mg-probe-bad">
        <span className="mg-probe-line">{result.message}</span>
        {result.traceback && <pre className="mg-probe-trace">{result.traceback}</pre>}
      </div>
    );
  }

  return (
    <div className="mg-probe-out mg-probe-unknown">
      <span className="mg-probe-line">
        <strong>Not checked.</strong> {result.message}
      </span>
      <span className="mg-probe-line mg-probe-note">
        The numbers above are still this pane&apos;s own arithmetic — nothing has run them.
      </span>
    </div>
  );
}

export function ProbeBar({
  projects,
  project,
  onProject,
  onRun,
  running,
  result,
  onHandoff,
  handingOff,
  handoff,
}: {
  projects: TrainingProject[];
  project: string;
  onProject: (id: string) => void;
  onRun: () => void;
  running: boolean;
  result: ProbeResult | null;
  /** Write the design into the selected project as a trainable model. */
  onHandoff: () => void;
  handingOff: boolean;
  handoff: HandoffResult | null;
}) {
  return (
    <div className="mg-probe">
      <div className="mg-probe-head">
        <span className="mg-probe-title">Run it</span>
        <select
          className="mg-input mg-probe-pick"
          value={project}
          onChange={(e) => onProject(e.target.value)}
          aria-label="Training project to run in"
        >
          <option value="">no project</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button type="button" className="mg-button" onClick={onRun} disabled={running}>
          {running ? 'running…' : 'Forward pass'}
        </button>
        {/* The other thing a chosen project is for: not checking the design, but
            keeping it. Same picker, because picking twice for one project is a
            question the user has already answered. */}
        <button
          type="button"
          className="mg-button"
          onClick={onHandoff}
          disabled={handingOff || !project}
          title={
            project
              ? 'Write this design into the project as model.py, and add a notebook cell that imports it.'
              : 'Pick a training project first.'
          }
        >
          {handingOff ? 'writing…' : 'Send to project'}
        </button>
      </div>
      {result && <Outcome result={result} />}
      {!result && (
        <p className="mg-probe-note">
          Builds the generated module in that project&apos;s venv and runs one batch through it.
          Needs torch installed there — the backend deliberately has none.
        </p>
      )}
      {handoff && (
        <div className={`mg-probe-out ${handoff.ok ? 'mg-probe-ok' : 'mg-probe-bad'}`}>
          <span className="mg-probe-line">
            {handoff.ok
              ? `${handoff.className} written to model.py${
                  handoff.cells
                    ? `, and ${handoff.replaced ? 'the notebook block was replaced' : 'a notebook block added'}.`
                    : '.'
                }`
              : handoff.message}
          </span>
          {handoff.ok && handoff.message && (
            <span className="mg-probe-line mg-probe-note">{handoff.message}</span>
          )}
        </div>
      )}
    </div>
  );
}
