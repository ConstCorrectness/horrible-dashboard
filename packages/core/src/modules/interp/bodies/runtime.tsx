/**
 * The Run & Test group bodies:
 * - `PlaygroundBody`: WebGPU in-browser Playground with 1-click pairing to 3D Neurons & Logit Lens.
 * - `RuntimeBody`: local `llama.cpp` server, hardware VRAM offload planner & GGUF catalog.
 * - `EvalsBody`: tool-calling & benchmark suites, runner, results & comparison.
 */
import { IconSplit } from '../../../glyphs';
import { EvalsHub } from '../../evals/EvalsHub';
import { LlamaCppPane } from '../../llamacpp/ServerPane';
import { useEngineState } from '../../webml/engine';
import { PlaygroundPanel } from '../../webml/panels/PlaygroundPanel';
import { SUITE_SECTIONS } from '../ids';
import { openSuiteTab } from '../stage';
import { SubSections, type SubTab } from '../SubSections';
import { SuiteFrame } from '../SuiteFrame';

export const RUNTIME_TABS: readonly SubTab[] = [
  { id: 'server', label: 'Server & Offload' },
  { id: 'models', label: 'GGUF Catalog' },
];

export const EVALS_TABS: readonly SubTab[] = [
  { id: 'suites', label: 'Suites' },
  { id: 'run', label: 'Run' },
  { id: 'results', label: 'Results' },
  { id: 'compare', label: 'Compare' },
];

export function PlaygroundBody() {
  const engine = useEngineState();
  const isGgufReady = engine.kind === 'ready' && engine.engine === 'gguf';

  return (
    <SuiteFrame title="Playground">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '5px 12px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
          fontSize: 11.5,
          color: 'var(--text-secondary)',
          flex: 'none',
        }}
      >
        <span>
          {isGgufReady
            ? `GGUF loaded on WebGPU (${engine.model.split('/').pop()}) — ready for 3D Neuron dosing & live Logit Lens.`
            : 'Load a GGUF model to unlock 3D Neuron steering and per-layer Logit Lens, or pick any ONNX model for fast WebGPU chat.'}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="interp-mini-action"
          style={pairBtn}
          onClick={() =>
            openSuiteTab(SUITE_SECTIONS.playground, { splitWith: SUITE_SECTIONS.neurons })
          }
          title="Open 3D Neurons side-by-side with Playground"
        >
          <IconSplit />
          Pair 3D Neurons
        </button>
        <button
          type="button"
          className="interp-mini-action"
          style={pairBtn}
          onClick={() =>
            openSuiteTab(SUITE_SECTIONS.playground, {
              splitWith: SUITE_SECTIONS.lens,
              companionSubTab: 'reply',
            })
          }
          title="Open Logit Lens (Reply) side-by-side with Playground"
        >
          <IconSplit />
          Pair Logit Lens
        </button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        <PlaygroundPanel />
      </div>
    </SuiteFrame>
  );
}

const pairBtn = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  height: 22,
  padding: '0 8px',
  border: '1px solid var(--border)',
  borderRadius: 3,
  background: 'var(--bg-primary)',
  color: 'var(--text-primary)',
  fontSize: 10.5,
  fontWeight: 600,
  cursor: 'pointer',
  flex: 'none' as const,
  ['--icon-size' as string]: '12px',
};

export function RuntimeBody() {
  return (
    <SuiteFrame title="Models & llama.cpp">
      <SubSections scope={SUITE_SECTIONS.runtime} tabs={RUNTIME_TABS}>
        <LlamaCppPane />
      </SubSections>
    </SuiteFrame>
  );
}

export function EvalsBody() {
  return (
    <SuiteFrame title="Evals">
      <SubSections scope={SUITE_SECTIONS.evals} tabs={EVALS_TABS}>
        <EvalsHub />
      </SubSections>
    </SuiteFrame>
  );
}
