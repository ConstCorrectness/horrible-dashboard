/** The window's logit lens in the model explorer, as static markup. */
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it } from 'vitest';

import { lensRuns } from '../../webml/lens-run';
import { WindowLens } from '../inspect/WindowLens';

const layer = (best: string, p: number) => ({ norm: 5, entropy: 1, top: [{ token: best, p }] });

function record() {
  lensRuns.start('gguf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf');
  lensRuns.push({ token: ' Paris', p: 0.9, layers: [layer(' no', 0.1), layer(' Paris', 0.8)] });
  lensRuns.finish();
}

afterEach(() => lensRuns.reset());

describe('WindowLens', () => {
  it('draws nothing until the window has run the lens', () => {
    expect(renderToStaticMarkup(<WindowLens blocks={2} selectedLayer={null} />)).toBe('');
  });

  it('draws the grid, the chosen token in the accent and where it settles', () => {
    record();
    const html = renderToStaticMarkup(
      <WindowLens blocks={2} selectedLayer={1} onPickLayer={() => {}} />,
    );
    expect(html).toContain('Qwen3-0.6B-Q8_0.gguf · 2 layers · 1 token');
    // Whitespace made visible; layer 1's best is the chosen token.
    expect(html).toContain('>·Paris</th>');
    expect(html).toMatch(/class="is-chosen is-settled"[^>]*>·Paris</);
    expect(html).toMatch(/<tr class="is-selected"><th scope="row"><button[^>]*>1</);
    expect(html).toMatch(/settles<\/th><td>1<\/td>/);
  });

  it('does not link rows to a model with another block count, and says why', () => {
    record();
    const html = renderToStaticMarkup(
      <WindowLens blocks={28} selectedLayer={1} onPickLayer={() => {}} />,
    );
    expect(html).toContain('has 2 layers and the one above has 28 blocks');
    expect(html).not.toContain('class="wl-layer"');
    expect(html).not.toContain('is-selected');
  });
});
