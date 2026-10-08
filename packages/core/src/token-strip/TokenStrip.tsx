/**
 * A generation as a strip of tokens, each shaded by the probability the sampler gave
 * it; hovering (or focusing) a token shows the alternatives it beat and the entropy of
 * the distribution.
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

export interface TokenStep {
  token: string;
  /** Probability of the chosen token. */
  p: number;
  /** Entropy of the distribution, in bits. */
  entropy: number;
  topk: TokenAlternative[];
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
    steps.push({
      token: s.token,
      p: s.p,
      entropy: typeof s.entropy === 'number' ? s.entropy : 0,
      topk: Array.isArray(s.topk)
        ? s.topk
            .filter(
              (a): a is TokenAlternative =>
                !!a && typeof a.token === 'string' && typeof a.p === 'number',
            )
            .map((a) => ({ token: a.token, p: a.p }))
        : [],
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
          </span>
        </span>
      ))}
    </div>
  );
}
