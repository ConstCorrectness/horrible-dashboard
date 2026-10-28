/**
 * The suite's tools, once: the rail's sections and Home's map are both read from
 * this list, so a tool cannot be on the rail and missing from the map, or be
 * described one way in one place and another way in the other.
 */
import { SUITE_SECTIONS, type SuiteSection } from './ids';

export interface SuiteTool {
  section: SuiteSection;
  /** Rail label, and what `show("…")` matches. */
  label: string;
  /** `NAMED_GLYPHS` key. */
  glyph: string;
  /** Rail group heading; absent for Home and the Library, which stand apart. */
  group?: 'Prompt' | 'Weights' | 'Activations' | 'Features';
  /** The question this tool answers. */
  question: string;
  /** When to reach for it. */
  when: string;
}

export const SUITE_TOOLS: readonly SuiteTool[] = [
  {
    section: SUITE_SECTIONS.home,
    label: 'Home',
    glyph: 'home',
    question: 'Where am I, and which tool answers my question?',
    when: 'Start here: the state the suite is looking at, and a map of every tool.',
  },
  {
    section: SUITE_SECTIONS.context,
    label: 'Context',
    glyph: 'layers',
    group: 'Prompt',
    question: 'What was the model actually handed before it answered?',
    when: 'Prompt bloat, a tool the agent "ignored", instructions that never arrived.',
  },
  {
    section: SUITE_SECTIONS.runs,
    label: 'Runs',
    glyph: 'timeline',
    group: 'Prompt',
    question: 'What did one agent turn see, send and do, round by round?',
    when: 'Stepping a turn, forking it with an edit, duelling two harnesses.',
  },
  {
    section: SUITE_SECTIONS.model,
    label: 'Model',
    glyph: 'cube',
    group: 'Weights',
    question: 'How is this network built, sized and quantized — block by block?',
    when: 'Head counts and KV sharing, where the bytes sit, blocks that differ.',
  },
  {
    section: SUITE_SECTIONS.attention,
    label: 'Attention',
    glyph: 'attention',
    group: 'Weights',
    question: 'Which layers are local, global, chunked or recurrent — and what does it cost?',
    when: 'Sliding windows, KV sharing, K=V, gated output, and the KV cache the pattern buys.',
  },
  {
    section: SUITE_SECTIONS.design,
    label: 'Design',
    glyph: 'pencil',
    group: 'Weights',
    question: 'What if the model were different?',
    when: 'Fork the inspected model into a node graph and generate a PyTorch module.',
  },
  {
    section: SUITE_SECTIONS.traces,
    label: 'Traces',
    glyph: 'wave',
    group: 'Activations',
    question: 'What happened inside one forward pass?',
    when: 'Record a pass over your own text; the stepper and lens read the snapshot.',
  },
  {
    section: SUITE_SECTIONS.stepper,
    label: 'Stepper',
    glyph: 'step',
    group: 'Activations',
    question: 'What does each block do to the residual stream, in order?',
    when: 'Step a trace like a debugger: attention maps, deltas, expert routing.',
  },
  {
    section: SUITE_SECTIONS.lens,
    label: 'Lens',
    glyph: 'lens',
    group: 'Activations',
    question: 'At which layer does the model know what it will say?',
    when: 'Read every layer as words — a trace, or the reply this window just wrote.',
  },
  {
    section: SUITE_SECTIONS.heads,
    label: 'Heads',
    glyph: 'heads',
    group: 'Activations',
    question: 'Which of the hundreds of heads are doing something nameable?',
    when: 'Find induction, previous-token and sink heads; the probe records the prompt that reveals them.',
  },
  {
    section: SUITE_SECTIONS.features,
    label: 'Features',
    glyph: 'feature',
    group: 'Features',
    question:
      'Which concepts did the model represent at this token, and can the labels be trusted here?',
    when: 'Gemma Scope 2 SAEs on a trace: features per token, Neuronpedia labels, a reconstruction check and a per-feature label check.',
  },
  {
    section: SUITE_SECTIONS.library,
    label: 'Library',
    glyph: 'library',
    question: 'Which SAEs, transcoders and lenses do I have — and do they fit this model?',
    when: 'Fetch Gemma Scope 2 and friends (size shown first), run the GPU worker, look up a Neuronpedia feature.',
  },
];
