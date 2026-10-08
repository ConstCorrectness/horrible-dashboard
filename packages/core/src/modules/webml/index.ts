/**
 * WebML: language models that run in this window, on its GPU (WebGPU), with no
 * backend round-trip and nothing leaving the machine. One app-global engine
 * (`engine.ts`) serves the playground pane, the `browser` chat provider and Scrive's
 * `{webllm}` blocks. See docs/modules/webml.mdx.
 */
import { nodeGgufModelId, probeWebGpu, SUPPORTED_ARCHS } from '@horrible/webml';

import { isHosted } from '../../hosted';
import { openPane } from '../../layout/controller';
import { lazyPane } from '../../lazy-pane';
import { minibuffer } from '../../minibuffer';
import type { ModuleManifest } from '../../registry';
import { webmlEngine } from './engine';

export { initWebmlRelay } from './relay';
// For other modules (Scrive's `{webllm}`): the one app-global engine.
export { useEngineState, useGpuReport, webmlEngine } from './engine';

const PlaygroundPanel = lazyPane(() => import('./panels/PlaygroundPanel'), 'PlaygroundPanel');

export const PLAYGROUND_VIEW = 'webml.playground';

/**
 * Whether "Run in this window" makes sense for a GGUF the node lists: an
 * architecture the WGSL engine runs, and a node on this machine. Hosted, the
 * backend is a remote machine, and every load would pull the whole file across
 * the network with nowhere to keep it.
 */
export function canRunNodeGguf(architecture: string): boolean {
  return !isHosted() && SUPPORTED_ARCHS.includes(architecture);
}

/**
 * Load a GGUF this node has (a path from the llama.cpp catalog, a training run's
 * conversion) into the window's engine, and open the playground to chat with it.
 * Nothing comes from the Hub: the bytes stream from the node's file route.
 */
export async function runNodeGguf(path: string): Promise<void> {
  const gpu = await probeWebGpu();
  if (!gpu.available) {
    minibuffer.say(`No WebGPU in this window: ${gpu.reason}`);
    return;
  }
  void openPane(PLAYGROUND_VIEW);
  try {
    await webmlEngine().load(nodeGgufModelId(path), 'q4f16', 'webgpu');
  } catch (err) {
    minibuffer.say(`Could not load ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

export const webmlModule: ModuleManifest = {
  id: 'webml',
  title: 'WebML',
  category: 'research',
  panels: [
    {
      id: PLAYGROUND_VIEW,
      title: 'WebML Playground',
      component: PlaygroundPanel,
      role: 'document',
      icon: '🧪',
      singleton: true,
    },
  ],
  settings: [
    {
      key: 'webml.defaultModel',
      title: 'Default model',
      description:
        'The Hugging Face model the playground selects first. Any ONNX text-generation repo works; the catalog lists the ones known to load on WebGPU.',
      type: 'string',
      default: 'onnx-community/Qwen3-0.6B-ONNX',
    },
    {
      key: 'webml.temperature',
      title: 'Temperature',
      description:
        'Sampling temperature for playground replies. 0 is greedy (always the most likely token).',
      type: 'number',
      default: 0.7,
    },
    {
      key: 'webml.topk',
      title: 'Alternatives recorded per token',
      description:
        'How many runner-up tokens the probability strip keeps for each generated token (0 turns the strip off). Costs a pass over the vocabulary per token, nothing on the GPU.',
      type: 'number',
      default: 5,
    },
    {
      key: 'webml.thinking',
      title: 'Let thinking models think',
      description:
        'For models with a reasoning switch (Qwen3, SmolLM3): reason in a <think> block before answering. Slower, often better.',
      type: 'boolean',
      default: false,
    },
  ],
  commands: [
    {
      id: 'webml.openPlayground',
      title: 'WebML: Open playground (run a model on this GPU)',
      run: () => void openPane(PLAYGROUND_VIEW),
    },
    {
      id: 'webml.unload',
      title: 'WebML: Unload the in-browser model (free GPU memory)',
      run: () => {
        const state = webmlEngine().getState();
        if (state.kind !== 'ready') {
          minibuffer.say('No in-browser model is loaded');
          return;
        }
        webmlEngine().unload();
        minibuffer.say(`Unloaded ${state.model}`);
      },
    },
  ],
};
