import { describe, expect, it } from 'vitest';

import { tokenizeLines } from '../highlight';

/** Re-join a tokenization back into source text. */
function reassemble(lines: ReturnType<typeof tokenizeLines>): string {
  return lines.map((line) => line.map((t) => t.text).join('')).join('\n');
}

const SAMPLE = `"""A generated module.

Spans two lines on purpose.
"""
import torch
from torch import nn


class Net(nn.Module):  # horrible:node=n1
    def __init__(self) -> None:
        super().__init__()
        self.fc = nn.Linear(768, 3072)  # horrible:node=n2
        self.scale = 0.125

    def forward(self, x):
\t\treturn self.fc(x) * self.scale
`;

describe('tokenizeLines', () => {
  // The whole correctness argument: highlighting may not alter the text. A dropped
  // gap run or a mishandled multi-line string would silently eat characters, and the
  // pane would still look like plausible code.
  it('reassembles to the original source exactly', () => {
    expect(reassemble(tokenizeLines(SAMPLE))).toBe(SAMPLE);
  });

  // Line numbers are the join between the code and the graph — the `# horrible:node=`
  // markers are keyed by them. A docstring or bracketed expression whose styled range
  // crosses a newline is what would shift them.
  it('keeps one entry per source line', () => {
    expect(tokenizeLines(SAMPLE)).toHaveLength(SAMPLE.split('\n').length);
  });

  it('actually assigns classes to keywords and strings', () => {
    const lines = tokenizeLines('import torch\n');
    const classes = lines[0].map((t) => t.cls).filter(Boolean);
    expect(classes.some((c) => c.includes('tok-keyword'))).toBe(true);
  });

  it('handles empty and blank-line-only sources', () => {
    expect(reassemble(tokenizeLines(''))).toBe('');
    expect(reassemble(tokenizeLines('\n\n'))).toBe('\n\n');
    expect(tokenizeLines('\n\n')).toHaveLength(3);
  });

  it('survives text that is not Python', () => {
    const junk = '<<< not python >>>\n@@@\n';
    expect(reassemble(tokenizeLines(junk))).toBe(junk);
  });
});
