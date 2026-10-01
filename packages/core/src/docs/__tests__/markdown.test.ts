// @vitest-environment happy-dom
import { describe, it, expect } from 'vitest';
import { renderMarkdown } from '../markdown';

describe('renderMarkdown', () => {
  it('renders inline markdown and basic tags safely', () => {
    const el = renderMarkdown('Here is `inline_code` and **bold** and *italic*.');
    expect(el.innerHTML).toContain('<code>inline_code</code>');
    expect(el.innerHTML).toContain('<strong>bold</strong>');
    expect(el.innerHTML).toContain('<em>italic</em>');
  });

  it('renders horizontal rule dividers cleanly', () => {
    const el = renderMarkdown('First line\n\n---\n\nSecond line');
    expect(el.innerHTML).toContain('<hr class="cm-lsp-divider">');
  });

  it('renders python code blocks with syntax highlighting classes', () => {
    const md = '```python\ndef load_dataset(path: str) -> None:\n    pass\n```';
    const el = renderMarkdown(md);
    expect(el.innerHTML).toContain('tok-keyword');
    expect(el.innerHTML).toContain('tok-variableName');
    expect(el.innerHTML).toContain('load_dataset');
  });

  it('extracts symbol kind tags like (function) and highlights the signature', () => {
    const md = '```python\n(function) def load_dataset(path: str = "train") -> None:\n    pass\n```';
    const el = renderMarkdown(md);
    expect(el.innerHTML).toContain('<span class="cm-lsp-kind">(function)</span>');
    expect(el.innerHTML).toContain('tok-keyword');
    expect(el.innerHTML).toContain('load_dataset');
    expect(el.innerHTML).toContain('tok-string');
  });

  it('extracts class kind tags like (class)', () => {
    const md = '```python\n(class) class DatasetDict:\n    pass\n```';
    const el = renderMarkdown(md);
    expect(el.innerHTML).toContain('<span class="cm-lsp-kind">(class)</span>');
    expect(el.innerHTML).toContain('DatasetDict');
  });

  it('escapes generics and HTML characters safely inside code blocks', () => {
    const md = '```typescript\nfunction test<T>(x: list<T>): T { return x; }\n```';
    const el = renderMarkdown(md);
    expect(el.innerHTML).not.toContain('<T>');
    expect(el.innerHTML).toContain('&lt;');
    expect(el.innerHTML).toContain('&gt;');
  });

  it('falls back safely for unknown languages', () => {
    const md = '```unknownlang\nhello world <safe>\n```';
    const el = renderMarkdown(md);
    expect(el.innerHTML).toContain('&lt;safe&gt;');
  });
});
