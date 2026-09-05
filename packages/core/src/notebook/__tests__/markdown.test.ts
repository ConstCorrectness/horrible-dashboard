/**
 * Markdown + math rendering for notebook cells.
 *
 * The math cases are the point. Every rule in `markdown.ts` was written for prose and
 * corrupts TeX in a way that still *renders* — an escaped `<`, an eaten `*`, a code
 * span claimed by a stray backtick — so the guard has to be that math survives the
 * pipeline, not merely that KaTeX is reachable.
 */
import { describe, expect, it } from 'vitest';

import { renderMarkdown } from '../markdown';
import { renderLatexOutput, stripMathDelimiters } from '../math';

describe('markdown', () => {
  it('still renders the prose subset', () => {
    expect(renderMarkdown('# Title')).toContain('<h1>Title</h1>');
    expect(renderMarkdown('- one\n- two')).toContain('<li>one</li>');
    expect(renderMarkdown('**bold**')).toContain('<strong>bold</strong>');
    expect(renderMarkdown('`code`')).toContain('<code>code</code>');
  });

  it('escapes HTML in prose', () => {
    expect(renderMarkdown('<script>x</script>')).not.toContain('<script>');
  });
});

describe('math in markdown cells', () => {
  it('typesets a display block', () => {
    const html = renderMarkdown('$$\\int_0^1 x^2 dx$$');
    expect(html).toContain('katex');
    expect(html).toContain('katex-display');
  });

  it('typesets inline math without the display wrapper', () => {
    const html = renderMarkdown('mass–energy is $E=mc^2$ exactly');
    expect(html).toContain('katex');
    expect(html).not.toContain('katex-display');
    expect(html).toContain('mass–energy is');
  });

  it('leaves currency alone', () => {
    // The classic false positive: two dollar amounts in a sentence look exactly like
    // an inline math pair, and typesetting the words between them is worse than
    // never supporting inline math at all.
    const html = renderMarkdown('it costs $5 and then $10');
    expect(html).not.toContain('katex');
    expect(html).toContain('$5');
    expect(html).toContain('$10');
  });

  it('survives characters the prose rules would have mangled', () => {
    // `<` would be escaped to an entity, `*` eaten by the italic rule, and a
    // backtick claimed as a code span — each producing wrong math, not an error.
    const html = renderMarkdown('$a < b$ and $x * y$ and $c \\ne d$');
    // KaTeX emits its own `&lt;` for the relation, so the check is that the prose
    // pipeline never saw the source: no `<em>` from the italic rule, and three
    // rendered spans rather than a mangled two.
    expect(html).not.toContain('<em>');
    expect((html.match(/class="katex/g) ?? []).length).toBeGreaterThanOrEqual(3);
  });

  it('renders bad TeX as flagged source rather than throwing', () => {
    expect(() => renderMarkdown('$\\notacommand{}$')).not.toThrow();
  });

  it('does not typeset a lone dollar', () => {
    expect(renderMarkdown('costs $5')).not.toContain('katex');
    expect(renderMarkdown('$5 and $10 and $20')).not.toContain('katex');
  });
});

describe('latex outputs', () => {
  it('strips the delimiters IPython wraps around Latex()', () => {
    // Handed `$$x$$`, KaTeX typesets the dollar signs as literal characters.
    expect(stripMathDelimiters('$$x^2$$')).toBe('x^2');
    expect(stripMathDelimiters('$x^2$')).toBe('x^2');
    expect(stripMathDelimiters('\\[x^2\\]')).toBe('x^2');
    expect(stripMathDelimiters('  \\begin{equation}x\\end{equation} ')).toBe('x');
  });

  it('leaves an undelimited body alone', () => {
    expect(stripMathDelimiters('\\frac{1}{2}')).toBe('\\frac{1}{2}');
  });

  it('renders a Latex output in display mode', () => {
    const html = renderLatexOutput('$$\\frac{1}{2}$$');
    expect(html).toContain('katex-display');
    expect(html).not.toContain('$$');
  });
});

describe('pipe tables', () => {
  const table = '| col | value |\n| --- | --- |\n| a | 1 |\n| b | 2 |';

  it('renders a header and body rows', () => {
    const html = renderMarkdown(table);
    expect(html).toContain('<th>col</th>');
    expect(html).toContain('<td>a</td>');
    expect((html.match(/<tr>/g) ?? []).length).toBe(3);
  });

  it('starts even with no blank line above it', () => {
    // Without the paragraph-run guard the header is swallowed as prose and the
    // table renders as a line of pipes.
    expect(renderMarkdown(`intro line\n${table}`)).toContain('<th>col</th>');
  });

  it('leaves prose containing a pipe alone', () => {
    // The separator identifies a table, not the pipes: `a | b` is a common way to
    // write an alternation and must not become a one-row table.
    expect(renderMarkdown('choose a | b')).not.toContain('<table>');
  });

  it('still renders a bare --- as a rule, not a separator', () => {
    expect(renderMarkdown('above\n\n---\n\nbelow')).toContain('<hr />');
  });
});
