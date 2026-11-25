/**
 * The IDE's tools, once: the rail's sections, Home's atlas, and SuiteFrame's
 * inline guide + prerequisite pills are all read from this list, so a tool cannot
 * be on the rail and missing from Home, or be described differently in two places.
 */
import { SUITE_SECTIONS, type SuiteSection } from './ids';

export const SUITE_GROUPS = [
  'Run & Test',
  'Agents & Prompts',
  'Architecture & Layers',
  'Mechanistic Lab',
] as const;

export type SuiteGroup = (typeof SUITE_GROUPS)[number];

export type SuitePrerequisite = 'none' | 'agent-turn' | 'gguf' | 'trace' | 'webml-gguf' | 'sae';

export interface SuiteTool {
  section: SuiteSection;
  /** Rail label, and what `show("…")` matches. */
  label: string;
  /** `NAMED_GLYPHS` key. */
  glyph: string;
  /** Rail group heading; absent for Home and Library, which frame the rail. */
  group?: SuiteGroup;
  /** Allow folding this group on the rail. */
  groupCollapsible?: boolean;
  /** Start this group folded for progressive disclosure until entered. */
  groupDefaultCollapsed?: boolean;
  /** The question this tool answers. */
  question: string;
  /** When to reach for it. */
  when: string;
  /** Plain-English 1-sentence quickstart shown in the header's `? Guide` banner. */
  howToUse: string;
  /** Runtime prerequisite checked to surface 1-click setup actions in-pane. */
  needs: SuitePrerequisite;
}

export const SUITE_TOOLS: readonly SuiteTool[] = [
  {
    section: SUITE_SECTIONS.home,
    label: 'Home',
    glyph: 'home',
    question: 'Where do I start, and which paired tools answer my question?',
    when: 'Launch a guided side-by-side playbook, check live runtimes, or browse every tool.',
    howToUse:
      'Pick a playbook below to open synchronized views side-by-side, or click any status tile to configure your model.',
    needs: 'none',
  },

  // ── Run & Test ─────────────────────────────────────────────────────────────
  {
    section: SUITE_SECTIONS.playground,
    label: 'Playground',
    glyph: 'playground',
    group: 'Run & Test',
    question: 'How does an in-browser model answer, token by token, on this GPU?',
    when: 'Load an ONNX or local GGUF model on WebGPU, inspect token probabilities, and feed 3D Neurons or Lens Lab.',
    howToUse:
      'Choose a catalog or GGUF model, click Load, send a prompt, and hover tokens in the probability strip — or pair beside 3D Neurons.',
    needs: 'none',
  },
  {
    section: SUITE_SECTIONS.runtime,
    label: 'Models & llama.cpp',
    glyph: 'runtime',
    group: 'Run & Test',
    question:
      'Which GGUF weights do I have on disk, how many layers fit in VRAM, and what is being served?',
    when: 'Install llama-server, check per-block VRAM offload, serve a local GGUF, or launch one in WebML.',
    howToUse:
      'Pick a GGUF in Server to preview VRAM fit and click Start, or switch to Models to download weights from Hugging Face.',
    needs: 'none',
  },
  {
    section: SUITE_SECTIONS.evals,
    label: 'Evals',
    glyph: 'evals',
    group: 'Run & Test',
    question: 'Does this model or harness reliably pick the right tools without regressions?',
    when: 'Run tool-calling suites or benchmarks, compare checkpoints, and click failing cases straight into the Turn Debugger.',
    howToUse:
      'Select a suite (like starter), run it against a provider/model, then open Results and click Debug beside on any failing row.',
    needs: 'none',
  },

  // ── Agents & Prompts ───────────────────────────────────────────────────────
  {
    section: SUITE_SECTIONS.context,
    label: 'Context',
    glyph: 'layers',
    group: 'Agents & Prompts',
    question: 'What exact prompt blocks and tool schemas was the model handed?',
    when: 'Prompt bloat, silent TOOL_BUDGET truncation, or instructions that never reached the model.',
    howToUse:
      'Pick a captured agent turn and round to see real token counts per prompt block, per tool group, and any dropped tools.',
    needs: 'agent-turn',
  },
  {
    section: SUITE_SECTIONS.runs,
    label: 'Runs',
    glyph: 'timeline',
    group: 'Agents & Prompts',
    question:
      'What did my agents see, send over the wire, and do — across all runs or inside one turn?',
    when: 'Search fleet trajectories, watch live/OTLP runs, step one turn in 4 columns (Shown · Wire · Did · Cost), fork rounds, or duel harnesses.',
    howToUse:
      'Browse All Runs for fleet search & grading, or switch to Turn Debugger to scrub a single turn round-by-round with Left/Right arrows.',
    needs: 'none',
  },

  // ── Architecture & Layers ──────────────────────────────────────────────────
  {
    section: SUITE_SECTIONS.model,
    label: 'Model',
    glyph: 'cube',
    group: 'Architecture & Layers',
    question: 'How is this network structured, sized, and quantized — block by block?',
    when: 'Inspect real GGUF tensor shapes, mixed quantizations, and KV footprints — or pair beside Stepper/Lens to watch blocks light up.',
    howToUse:
      'Click any node or layer tick on the vertical spine to inspect its exact tensors; double-click to focus neighbours.',
    needs: 'gguf',
  },
  {
    section: SUITE_SECTIONS.attention,
    label: 'Attention',
    glyph: 'attention',
    group: 'Architecture & Layers',
    question:
      'Which blocks use sliding-window, global, SSM, or shared-KV attention — and how much VRAM does it save?',
    when: 'Gemma/Qwen/Llama hybrid mixers, KV sharing arcs, effective context reach by depth, and fp16 KV cache comparisons.',
    howToUse:
      'Click any column in the per-layer ribbon to inspect its causal mask, basis (metadata/rule/tensors), and sync the shared locus.',
    needs: 'gguf',
  },
  {
    section: SUITE_SECTIONS.lens,
    label: 'Lens',
    glyph: 'lens',
    group: 'Architecture & Layers',
    question: 'At which layer does the model form its answer or bridge concept?',
    when: 'Decode every layer into vocabulary words on a recorded llama.cpp trace, fitted Jacobian lens, or live WebML reply.',
    howToUse:
      'Hover or click any (layer × token) cell to move the shared locus; use Compare Lenses for J-lens or Compare to diff base vs fine-tune.',
    needs: 'none',
  },
  {
    section: SUITE_SECTIONS.stepper,
    label: 'Stepper',
    glyph: 'step',
    group: 'Architecture & Layers',
    question: 'What does each operation do to the residual stream during a forward pass?',
    when: 'Step through a traced pass like source code: attention maps, RMSNorm, SwiGLU FFN deltas, and MoE expert routing.',
    howToUse:
      'Pick a recorded trace and use Right/Left (node) or Down/Up (block) arrows; pair beside Model to watch executing sub-blocks highlight.',
    needs: 'trace',
  },
  {
    section: SUITE_SECTIONS.traces,
    label: 'Traces',
    glyph: 'wave',
    group: 'Architecture & Layers',
    question: 'How do I capture a full forward-pass tensor snapshot on my own prompt or code?',
    when: 'Record per-node ggml activations and attention weights on disk so Stepper, Lens, Heads, and Features can read them.',
    howToUse:
      'Choose a local GGUF and prompt (or right-click editor text → Trace this), check estimated disk size, and record a snapshot.',
    needs: 'gguf',
  },

  // ── Mechanistic Lab (progressive fold) ─────────────────────────────────────
  {
    section: SUITE_SECTIONS.neurons,
    label: 'Neurons',
    glyph: 'neuron',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question: 'What happens when individual MLP neurons are amplified, damped, or ablated in 3D?',
    when: 'Orbit every MLP neuron of a WebML GGUF in 3D, dose up to 32 neurons, compare greedy replies, and light up per-token heat.',
    howToUse:
      'Pair with Playground to load a GGUF on WebGPU, click any point on a layer plate to dose it, then run Compare and click reply tokens.',
    needs: 'webml-gguf',
  },
  {
    section: SUITE_SECTIONS.heads,
    label: 'Heads',
    glyph: 'heads',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question:
      'Which of the hundreds of attention heads act as induction, previous-token, or sink heads?',
    when: 'Rank every (layer × head) in a trace instead of hunting through matrices one by one.',
    howToUse:
      'Click Run induction probe to trace a repeated-sequence prompt automatically, then click top-ranked heads in the heatmap.',
    needs: 'trace',
  },
  {
    section: SUITE_SECTIONS.features,
    label: 'Features',
    glyph: 'feature',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question:
      'Which sparse autoencoder (SAE) concepts fired at each token — and do they reconstruct cleanly?',
    when: 'Decompose a GGUF trace with Gemma Scope 2 SAEs, check FVU/L0 fidelity, read Neuronpedia cards, and verify labels.',
    howToUse:
      'Ensure a matching SAE is downloaded in Library, select a trace, click tokens to see active features, or send a feature straight to Steer.',
    needs: 'sae',
  },
  {
    section: SUITE_SECTIONS.steer,
    label: 'Steer',
    glyph: 'steer',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question:
      'How does generation shift when an SAE feature direction is injected into or subtracted from the residual stream?',
    when: 'Causal intervention via llama.cpp control vectors: check whether the feature arrived, next-token KL shift, and side-by-side text.',
    howToUse:
      'Pick a feature from Features, set strength between −1.5× and +1.5× of peak, run greedy comparison, and check Arrived first.',
    needs: 'sae',
  },
  {
    section: SUITE_SECTIONS.axis,
    label: 'Axis',
    glyph: 'axis',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question:
      'Is the model staying in its assistant persona or drifting into role-play — and can we floor it?',
    when: 'Fit an Assistant Axis on your local GGUF, check held-out AUC, monitor multi-turn drift, and cap answer tokens at the floor.',
    howToUse:
      'Build or select an axis for your GGUF, check that held-out AUC separates (≥ 0.8), then project a conversation or agent turn.',
    needs: 'gguf',
  },
  {
    section: SUITE_SECTIONS.graph,
    label: 'Graph',
    glyph: 'graph',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question: 'Which multi-hop feature paths causally drove a specific output token?',
    when: 'Import Neuronpedia or circuit-tracer attribution graphs, prune by influence, pin subgraphs, and group supernodes.',
    howToUse:
      'Import a Neuronpedia graph URL or JSON file, adjust node/edge pruning sliders, click nodes to inspect links, and Ctrl-click to pin.',
    needs: 'none',
  },
  {
    section: SUITE_SECTIONS.nla,
    label: 'Activation → Words',
    glyph: 'speech',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question:
      'Can a model translate a raw residual vector into English and reconstruct the vector back?',
    when: 'Run a local Natural Language Autoencoder (AV explainer + AR reconstructor) and score explanations above the neutral cosine floor.',
    howToUse:
      'Select a text/turn/trace and token positions (e.g. -3,-2,-1), run the pair, and compare each cosine against the marked neutral floor.',
    needs: 'gguf',
  },
  {
    section: SUITE_SECTIONS.design,
    label: 'Design',
    glyph: 'pencil',
    group: 'Mechanistic Lab',
    groupCollapsible: true,
    groupDefaultCollapsed: true,
    question: 'What if this architecture had different blocks, widths, or ablated paths?',
    when: 'Fork the inspected model into an editable node graph and export runnable PyTorch module code.',
    howToUse:
      'Select nodes to edit dimensions, press M to mute/ablate or Shift+A to add blocks, and inspect generated PyTorch code.',
    needs: 'none',
  },

  // ── Footer ─────────────────────────────────────────────────────────────────
  {
    section: SUITE_SECTIONS.library,
    label: 'Library',
    glyph: 'library',
    question:
      'Which SAEs, transcoders, and Jacobian lenses are installed — and do they fit my model?',
    when: 'Download curated Gemma Scope / Jacobian lens artifacts (with byte-exact size confirmation), manage the GPU worker, or query Neuronpedia.',
    howToUse:
      'Browse the curated catalog, click Get to preview exact byte size against your disk budget, and check compatibility badges per model.',
    needs: 'none',
  },
];

export function toolForSection(section: SuiteSection | string | undefined): SuiteTool | undefined {
  return SUITE_TOOLS.find((t) => t.section === section);
}
