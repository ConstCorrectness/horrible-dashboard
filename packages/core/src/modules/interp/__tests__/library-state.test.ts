import { describe, expect, it } from 'vitest';

import type { Artifact, WorkerInfo } from '../library/api';
import { libraryDigest, libraryStore, type LibraryState } from '../library/state';

function artifact(state: 'compatible' | 'incompatible' | 'unknown'): Artifact {
  return {
    id: `gemma-scope:${state}`,
    entryId: 'gemma-scope-2-1b-it-resid',
    kind: 'sae',
    repo: 'google/gemma-scope-2-1b-it',
    choice: 'resid_post/layer_13_width_16k_l0_medium',
    modelRepo: 'google/gemma-3-1b-it',
    path: 'x',
    bytes: 1,
    facts: { layer: 13, source: { layer: 'folder name' } },
    createdAt: 0,
    compat: [{ state, reasons: [], target: 'gemma-3-1b-it-Q8_0.gguf', source: 'gguf' }],
  };
}

const worker = (patch: Partial<WorkerInfo>): WorkerInfo => ({
  env: {
    state: 'idle',
    group: '',
    line: '',
    error: '',
    path: '',
    exists: true,
    groups: ['base'],
    available: ['base', 'circuits', 'sae'],
  },
  state: 'stopped',
  pid: null,
  ops: [],
  exitCode: null,
  log: [],
  ...patch,
});

const base: LibraryState = { ...libraryStore.get() };

describe('libraryDigest', () => {
  it('says nothing before anything was read', () => {
    expect(libraryDigest({ ...base, library: null, worker: null })).toBeNull();
  });

  it('counts only compatible artifacts as fitting — unknown is not a yes', () => {
    const line = libraryDigest({
      ...base,
      library: {
        artifacts: [artifact('compatible'), artifact('unknown'), artifact('incompatible')],
        targets: [],
        usedBytes: 3,
        budgetBytes: 10,
      },
      worker: null,
    });
    expect(line).toBe('3 artifacts (1 fit a loaded model)');
  });

  it('names the model the worker holds', () => {
    const line = libraryDigest({
      ...base,
      library: null,
      worker: worker({
        state: 'ready',
        status: {
          loaded: {
            repo: 'Qwen/Qwen3-0.6B',
            dtype: 'bfloat16',
            device: 'cuda',
            layers: 28,
            dModel: 1024,
            loadSeconds: 6,
          },
        },
      }),
    });
    expect(line).toBe('worker ready, holding Qwen/Qwen3-0.6B');
  });

  it('reports a missing environment in words', () => {
    expect(libraryDigest({ ...base, library: null, worker: worker({ state: 'no_env' }) })).toBe(
      'worker no env',
    );
  });
});
