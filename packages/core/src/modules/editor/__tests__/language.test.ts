import { describe, expect, it } from 'vitest';

import {
  descriptionFor,
  extensionForLanguage,
  lspLanguageId,
  PICKABLE_LANGUAGES,
  resolveLanguage,
  sniffLanguage,
} from '../language';

describe('resolveLanguage', () => {
  it('resolves the languages the old regex knew', () => {
    expect(resolveLanguage({ title: 'a.py' }).name).toBe('Python');
    expect(resolveLanguage({ title: 'a.ts' }).name).toBe('TypeScript');
    expect(resolveLanguage({ title: 'a.tsx' }).name).toBe('TSX');
    expect(resolveLanguage({ title: 'a.jsx' }).name).toBe('JSX');
    expect(resolveLanguage({ title: 'a.md' }).name).toBe('Markdown');
  });

  // The whole reason for language-data: these used to render as Markdown, silently.
  it('resolves languages the old regex fell through on', () => {
    expect(resolveLanguage({ title: 'main.rs' }).name).toBe('Rust');
    expect(resolveLanguage({ title: 'main.cpp' }).name).toBe('C++');
    expect(resolveLanguage({ title: 'x.go' }).name).toBe('Go');
    expect(resolveLanguage({ title: 'x.json' }).name).toBe('JSON');
    expect(resolveLanguage({ title: 'x.yaml' }).name).toBe('YAML');
  });

  it('honours a pin over everything, and a hint over the filename', () => {
    expect(resolveLanguage({ title: 'a.py', pinned: 'Rust' }).name).toBe('Rust');
    expect(resolveLanguage({ title: 'note', hint: 'python' }).name).toBe('Python');
    // A named file wins over content that looks like something else — a .py file
    // holding prose for a moment is still Python.
    expect(resolveLanguage({ title: 'a.py', content: '# Just a heading\n\n- a\n- b\n' }).name).toBe(
      'Python',
    );
  });

  it('sniffs content only when the title has no extension', () => {
    const py = 'import torch\n\ndef main():\n    return 1\n';
    expect(resolveLanguage({ title: 'untitled', content: py }).name).toBe('Python');
    expect(resolveLanguage({ title: 'untitled.md', content: py }).name).toBe('Markdown');
  });

  it('falls back to Markdown rather than to nothing', () => {
    expect(resolveLanguage({ title: 'a.unheardof' }).name).toBe('Markdown');
    expect(resolveLanguage({ title: 'untitled', content: '' }).name).toBe('Markdown');
  });

  it('derives the LSP id from the resolved language', () => {
    expect(lspLanguageId('a.py')).toBe('python');
    expect(lspLanguageId('a.tsx')).toBe('typescriptreact');
    expect(lspLanguageId('a.rs')).toBe('rust');
    expect(lspLanguageId('a.cpp')).toBe('cpp');
    expect(lspLanguageId('a.md')).toBeNull();
  });

  it('offers only pickable languages that actually exist', () => {
    for (const name of PICKABLE_LANGUAGES) {
      expect(descriptionFor(name), name).not.toBeNull();
    }
  });
});

describe('sniffLanguage', () => {
  it('takes a shebang as decisive', () => {
    expect(sniffLanguage('#!/usr/bin/env python3\nx = 1\n')).toBe('Python');
    expect(sniffLanguage('#!/usr/bin/env node\nx\n')).toBe('JavaScript');
  });

  it('recognises the families', () => {
    expect(sniffLanguage('#include <vector>\nstd::vector<int> v;\n')).toBe('C++');
    expect(sniffLanguage('use std::io;\n\npub fn main() {\n    let mut x = 1;\n}\n')).toBe('Rust');
    expect(sniffLanguage('from vllm import LLM\n\ndef run(self):\n    pass\n')).toBe('Python');
    expect(sniffLanguage('const a = 1;\nexport function go() {\n  console.log(a);\n}\n')).toBe(
      'JavaScript',
    );
    expect(sniffLanguage('# Title\n\n- one\n- two\n\n```py\nx\n```\n')).toBe('Markdown');
  });

  it('refuses to guess on one signal', () => {
    // `import x` alone is Python, JavaScript, Rust and Java at once. Flipping the
    // grammar under the cursor on this much evidence is the failure mode.
    expect(sniffLanguage('import x\n')).toBeNull();
    expect(sniffLanguage('')).toBeNull();
    expect(sniffLanguage('just some prose about things\n')).toBeNull();
  });

  it('recognises a whole JSON document', () => {
    expect(sniffLanguage('{"a": 1, "b": [2, 3]}')).toBe('JSON');
  });
});

describe('extensionForLanguage', () => {
  // language-data lists extensions in no particular order — Python's first entry is
  // `BUILD`, which had a sniffed-Python scratch buffer offering to save itself as
  // `untitled.BUILD`. The conventional extension is a naming convention, not a
  // grammar fact.
  it('proposes the conventional extension, not language-data order', () => {
    expect(extensionForLanguage('Python')).toBe('py');
    expect(extensionForLanguage('Markdown')).toBe('md');
    expect(extensionForLanguage('C++')).toBe('cpp');
    expect(extensionForLanguage('Rust')).toBe('rs');
  });

  it('never proposes an uppercase extension for a language it does not name', () => {
    for (const name of PICKABLE_LANGUAGES) {
      const ext = extensionForLanguage(name);
      expect(ext, name).toBe(ext.toLowerCase());
    }
  });
});
