/**
 * A generation as a strip of tokens, each shaded by the probability the sampler gave
 * it; hovering (or focusing) a token shows the alternatives it beat and the entropy of
 * the distribution. A step recorded with the logit lens also shows, layer by layer,
 * what the model would have said from its residual stream there, and how large that
 * stream was.
 *
 * Shared core UI: the WebML playground, Scrive's `{webllm}` / `{tokenviz}` blocks and
 * a published site all draw it. Pure markup + CSS (no state), so it works rendered to
 * static HTML. `token-strip.css` uses only tokens every app *and* site theme defines.
 */
import './token-strip.css';

export interface TokenAlternative {
  token: string;
  p: number;
}

/** The logit lens at one layer (the GGUF engine's `lens` readout). */
export interface TokenLayer {
  /** L2 norm of the residual stream after the layer. */
  norm: number;
  /** Entropy, in bits, of what the LM head says from it. */
  entropy: number;
  top: TokenAlternative[];
}

export interface TokenStep {
  token: string;
  /** Probability of the chosen token. */
  p: number;
  /** Entropy of the distribution, in bits. */
  entropy: number;
  topk: TokenAlternative[];
  /** First layer to last, when the run was recorded with the logit lens. */
  layers?: TokenLayer[];
}

/** A recorded generation, as `{tokenviz}` reads it from a site file. */
export interface TokenRun {
  model: string;
  prompt: string;
  steps: TokenStep[];
}

/** Whitespace made visible, so a strip of `\n` and spaces is not a strip of nothing. */
export function showToken(token: string): string {
  return token.replace(/\n/g, '↵').replace(/\t/g, '⇥') || '∅';
}

function alternatives(value: unknown): TokenAlternative[] {
  return Array.isArray(value)
    ? value
        .filter(
          (a): a is TokenAlternative =>
            !!a && typeof a.token === 'string' && typeof a.p === 'number',
        )
        .map((a) => ({ token: a.token, p: a.p }))
    : [];
}

/** A step's lens, kept only when every layer is well-formed. */
function layers(value: unknown): TokenLayer[] | undefined {
  if (!Array.isArray(value) || !value.length) return undefined;
  const out: TokenLayer[] = [];
  for (const raw of value) {
    const l = (raw ?? {}) as Record<string, unknown>;
    if (typeof l.norm !== 'number' || typeof l.entropy !== 'number') return undefined;
    out.push({ norm: l.norm, entropy: l.entropy, top: alternatives(l.top) });
  }
  return out;
}

/** A file's JSON as a run, or a reason it is not one. Tolerant of extra fields. */
export function parseTokenRun(value: unknown): TokenRun | string {
  if (!value || typeof value !== 'object') return 'not a JSON object';
  const v = value as Record<string, unknown>;
  if (!Array.isArray(v.steps)) return 'no "steps" array';
  const steps: TokenStep[] = [];
  for (const raw of v.steps) {
    const s = (raw ?? {}) as Record<string, unknown>;
    if (typeof s.token !== 'string' || typeof s.p !== 'number')
      return 'a step needs "token" and "p"';
    const lens = layers(s.layers);
    steps.push({
      token: s.token,
      p: s.p,
      entropy: typeof s.entropy === 'number' ? s.entropy : 0,
      topk: alternatives(s.topk),
      ...(lens && { layers: lens }),
    });
  }
  return {
    model: typeof v.model === 'string' ? v.model : '',
    prompt: typeof v.prompt === 'string' ? v.prompt : '',
    steps,
  };
}

export function TokenStrip({ steps }: { steps: TokenStep[] }) {
  return (
    <div className="tokstrip" role="list" aria-label="Generated tokens by probability">
      {steps.map((s, i) => (
        <span
          key={i}
          role="listitem"
          tabIndex={0}
          className="tokstrip-tok"
          style={{ ['--p' as string]: s.p.toFixed(3) }}
          aria-label={`${JSON.stringify(s.token)} p=${s.p.toFixed(3)}`}
        >
          {showToken(s.token)}
          <span className="tokstrip-pop">
            <span className="tokstrip-meta">
              p {s.p.toFixed(3)} · H {s.entropy.toFixed(2)} bits
            </span>
            {s.topk.map((alt, j) => (
              <span key={j} className={`tokstrip-alt${alt.token === s.token ? ' is-chosen' : ''}`}>
                <span>{JSON.stringify(alt.token)}</span>
                <span className="tokstrip-meta">{alt.p.toFixed(3)}</span>
                <span className="tokstrip-bar" style={{ width: `${Math.max(2, alt.p * 100)}%` }} />
              </span>
            ))}
            {s.layers && <Lens token={s.token} layers={s.layers} />}
          </span>
        </span>
      ))}
    </div>
  );
}

/**
 * One step's logit lens: per layer, the LM head's best token from the residual
 * stream there (in the accent once it is the token finally chosen), its
 * probability, and the stream's norm as a bar against the step's largest.
 */
function Lens({ token, layers }: { token: string; layers: TokenLayer[] }) {
  const most = Math.max(...layers.map((l) => l.norm), 1e-9);
  return (
    <span className="tokstrip-lens" role="table" aria-label="Logit lens by layer">
      <span className="tokstrip-lens-head" role="row">
        <span role="columnheader">layer</span>
        <span role="columnheader">token</span>
        <span role="columnheader">p</span>
        <span role="columnheader">norm</span>
      </span>
      {layers.map((l, i) => {
        const best = l.top[0];
        return (
          <span
            key={i}
            role="row"
            className={`tokstrip-lens-row${best?.token === token ? ' is-chosen' : ''}`}
          >
            <span className="tokstrip-meta">{i}</span>
            <span className="tokstrip-lens-tok">{best ? JSON.stringify(best.token) : '—'}</span>
            <span className="tokstrip-meta">{best ? best.p.toFixed(2) : ''}</span>
            <span className="tokstrip-lens-norm" title={l.norm.toFixed(1)}>
              <span style={{ width: `${Math.max(2, (l.norm / most) * 100)}%` }} />
            </span>
          </span>
        );
      })}
    </span>
  );
}
