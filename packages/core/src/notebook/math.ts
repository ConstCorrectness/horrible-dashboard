/**
 * TeX → HTML for notebook cells and outputs.
 *
 * KaTeX rather than the hand-rolled treatment the rest of this directory gets. Math
 * layout is not a subset anyone can approximate: the difference between a rendered
 * integral and a plausible-looking one is the whole point of writing it in TeX, and a
 * partial renderer produces output that is wrong in ways the reader cannot see.
 *
 * `throwOnError: false` throughout. A malformed expression renders in KaTeX's own
 * error colour with the source visible, which is what a notebook wants — the
 * alternative is an exception inside a cell's render and a blank output where the
 * user can see neither the result nor what they typed.
 */
import katex from 'katex';

/**
 * Delimiters IPython wraps around `Latex` / `Math` output, longest first.
 *
 * `display(Latex(r'$$x$$'))` and a bare `Math('x')` both arrive as `text/latex`, one
 * delimited and one not, and KaTeX takes the *body* — handed `$$x$$` it typesets the
 * dollar signs as literal characters. Stripping is therefore not cosmetic.
 */
const WRAPPERS: readonly (readonly [string, string])[] = [
  ['\\begin{equation}', '\\end{equation}'],
  ['\\[', '\\]'],
  ['\\(', '\\)'],
  ['$$', '$$'],
  ['$', '$'],
];

/** Strip one layer of TeX delimiters, if the string is wrapped in a matching pair. */
export function stripMathDelimiters(src: string): string {
  const s = src.trim();
  for (const [open, close] of WRAPPERS) {
    if (s.length > open.length + close.length && s.startsWith(open) && s.endsWith(close)) {
      return s.slice(open.length, s.length - close.length).trim();
    }
  }
  return s;
}

/** Render TeX to an HTML string. Never throws; bad input renders as flagged source. */
export function renderMath(tex: string, displayMode: boolean): string {
  return katex.renderToString(tex, {
    displayMode,
    throwOnError: false,
    // `\text{…}` and friends only; no `\href`, no `\includegraphics`. Kernel output
    // is the user's own code, but a notebook is also the most commonly *shared*
    // artifact here, and there is no expression worth rendering that needs more.
    trust: false,
    strict: false,
    output: 'html',
  });
}

/** A `text/latex` output bundle → HTML. Always display mode: it is its own block. */
export function renderLatexOutput(src: string): string {
  return renderMath(stripMathDelimiters(src), true);
}
