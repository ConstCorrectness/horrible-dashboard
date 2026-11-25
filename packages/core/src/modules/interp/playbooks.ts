/**
 * Goal-driven Playbooks: answer "I don't know how to use this" by turning each
 * common AI-research question into a 1-click workspace arrangement that opens the
 * right pair of tools side-by-side.
 */
import { playgroundSource } from '../webml/playground-state';
import { SUITE_SECTIONS, type SuiteSection } from './ids';
import { openSuiteTab } from './stage';

export interface SuitePlaybook {
  id: string;
  title: string;
  glyph: string;
  question: string;
  summary: string;
  primary: SuiteSection;
  splitWith: SuiteSection;
  subTab?: string;
  companionSubTab?: string;
  prepare?: () => void;
}

export const SUITE_PLAYBOOKS: readonly SuitePlaybook[] = [
  {
    id: 'debug-agent',
    title: 'Debug an Agent Turn',
    glyph: 'timeline',
    question: 'Why did my agent fail, loop, or ignore a tool?',
    summary:
      'Opens the 4-column Turn Debugger beside the Context Window to inspect exact prompt tokens, wire JSON, and dropped tools.',
    primary: SUITE_SECTIONS.runs,
    subTab: 'turn',
    splitWith: SUITE_SECTIONS.context,
  },
  {
    id: 'watch-layers',
    title: 'Watch Layers Form an Answer',
    glyph: 'lens',
    question: 'At which layer does the model settle on its prediction?',
    summary:
      'Pairs the Logit Lens heatmap beside the Model Explorer — click any layer row to inspect its exact attention & FFN tensors.',
    primary: SUITE_SECTIONS.lens,
    splitWith: SUITE_SECTIONS.model,
  },
  {
    id: 'step-forward-pass',
    title: 'Step a Forward Pass Live',
    glyph: 'step',
    question: 'What happens inside each decoder block during execution?',
    summary:
      'Pairs the Layer Stepper beside the Model Explorer so stepping with arrow keys lights up each executing sub-block on the graph.',
    primary: SUITE_SECTIONS.stepper,
    splitWith: SUITE_SECTIONS.model,
  },
  {
    id: 'dose-3d-neurons',
    title: 'Dose 3D Neurons on WebGPU',
    glyph: 'neuron',
    question: 'What changes when specific MLP neurons are turned up or ablated?',
    summary:
      'Opens the WebML GGUF Playground beside the 3D Neuron Stack — load a model once and watch every layer plate light up.',
    primary: SUITE_SECTIONS.playground,
    splitWith: SUITE_SECTIONS.neurons,
    prepare: () => playgroundSource.requestGguf(),
  },
  {
    id: 'steer-sae-features',
    title: 'Find & Steer SAE Concepts',
    glyph: 'steer',
    question: 'Which interpretable features fire here, and can I steer them?',
    summary:
      'Pairs Gemma Scope SAE Features beside Feature Steer — verify reconstruction FVU, pick a concept, and push/suppress it.',
    primary: SUITE_SECTIONS.features,
    splitWith: SUITE_SECTIONS.steer,
  },
  {
    id: 'evaluate-and-debug',
    title: 'Run Evals & Inspect Failures',
    glyph: 'evals',
    question: 'Did my prompt, harness, or fine-tune improve tool calling?',
    summary:
      'Opens Evals beside Runs & Trajectories so you can score a suite and step any failing case round-by-round without switching windows.',
    primary: SUITE_SECTIONS.evals,
    splitWith: SUITE_SECTIONS.runs,
  },
];

export function launchPlaybook(id: string): string | null {
  const pb = SUITE_PLAYBOOKS.find((p) => p.id === id);
  if (!pb) return null;
  pb.prepare?.();
  return openSuiteTab(pb.primary, {
    splitWith: pb.splitWith,
    subTab: pb.subTab,
    companionSubTab: pb.companionSubTab,
  });
}
