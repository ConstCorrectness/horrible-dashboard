/**
 * The Interpretability & Model Lab IDE: one window fusing prompts, agent runs,
 * evaluations, local/browser runtimes (`llama.cpp` + WebML), weights, activations,
 * features, and circuits — with a multi-tab + side-by-side split center stage,
 * progressive rail groups, and a shared Subject & Runtime Bar.
 */
import { lazyPane } from '../../lazy-pane';
import { listPanes } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import type { ModuleManifest, PanelDecl } from '../../registry';
import { interpAgentTools } from './agent-tools';
import { ASK_VIEW, askInterp } from './ask';
import { SUITE_SECTIONS, SUITE_VIEW, whenSuiteSection, type SuiteSection } from './ids';
import { NEURON_KEYS, NEURON_VERBS, neuronAction } from './neurons/actions';
import { openSuiteTab, splitSuiteWith, swapSuiteSplit } from './stage';
import { SubjectBar } from './SubjectBar';
import { SUITE_TOOLS } from './tools';

const HomeSection = lazyPane(() => import('./HomeSection'), 'HomeSection');
const PlaygroundBody = lazyPane(() => import('./bodies/runtime'), 'PlaygroundBody');
const RuntimeBody = lazyPane(() => import('./bodies/runtime'), 'RuntimeBody');
const EvalsBody = lazyPane(() => import('./bodies/runtime'), 'EvalsBody');
const ContextBody = lazyPane(() => import('./bodies/prompt'), 'ContextBody');
const RunsBody = lazyPane(() => import('./bodies/prompt'), 'RunsBody');
const ModelBody = lazyPane(() => import('./bodies/weights'), 'ModelBody');
const DesignBody = lazyPane(() => import('./bodies/weights'), 'DesignBody');
const AttentionBody = lazyPane(() => import('./attention/AttentionSection'), 'AttentionBody');
const TracesBody = lazyPane(() => import('./bodies/activations'), 'TracesBody');
const StepperBody = lazyPane(() => import('./bodies/activations'), 'StepperBody');
const LensBody = lazyPane(() => import('./bodies/activations'), 'LensBody');
const HeadsBody = lazyPane(() => import('./heads/HeadsSection'), 'HeadsBody');
const NeuronsBody = lazyPane(() => import('./neurons/NeuronsSection'), 'NeuronsBody');
const FeaturesBody = lazyPane(() => import('./features/FeaturesSection'), 'FeaturesBody');
const SteerBody = lazyPane(() => import('./steer/SteerSection'), 'SteerBody');
const AxisBody = lazyPane(() => import('./axis/AxisSection'), 'AxisBody');
const GraphBody = lazyPane(() => import('./graph/GraphSection'), 'GraphBody');
const NlaBody = lazyPane(() => import('./nla/NlaSection'), 'NlaBody');
const LibraryBody = lazyPane(() => import('./library/LibrarySection'), 'LibraryBody');
const AskPanel = lazyPane(() => import('./AskPanel'), 'AskPanel');

/**
 * Each section's body, as an embedded view. Distinct titles so `show("…")` and
 * `registry-titles.test.ts` resolve unambiguously.
 */
const BODIES: Record<SuiteSection, { title: string; component: PanelDecl['component'] }> = {
  home: { title: 'Interpretability home', component: HomeSection },
  playground: { title: 'Model playground (WebGPU)', component: PlaygroundBody },
  runtime: { title: 'Local runtime & GGUF models', component: RuntimeBody },
  evals: { title: 'Evaluation suites & benchmarks', component: EvalsBody },
  context: { title: 'Context window', component: ContextBody },
  runs: { title: 'Agent runs & trajectories', component: RunsBody },
  model: { title: 'Model explorer', component: ModelBody },
  attention: { title: 'Attention layout', component: AttentionBody },
  lens: { title: 'Logit lens', component: LensBody },
  stepper: { title: 'Layer stepper', component: StepperBody },
  traces: { title: 'Activation traces', component: TracesBody },
  neurons: { title: 'Neuron steering', component: NeuronsBody },
  heads: { title: 'Attention heads', component: HeadsBody },
  features: { title: 'SAE features', component: FeaturesBody },
  steer: { title: 'Steering', component: SteerBody },
  axis: { title: 'Assistant axis', component: AxisBody },
  graph: { title: 'Attribution graph', component: GraphBody },
  nla: { title: 'Natural language autoencoder', component: NlaBody },
  design: { title: 'Model designer', component: DesignBody },
  library: { title: 'Interpretability library', component: LibraryBody },
};

export function suiteBodyView(section: SuiteSection): string {
  return `interp.${section}`;
}

function suiteInstanceId(): string | null {
  return (
    listPanes(layoutStore.getSnapshot().frame).find((p) => p.pane.viewId === SUITE_VIEW)?.pane
      .instanceId ?? null
  );
}

export const interpSuiteModule: ModuleManifest = {
  id: 'interp',
  title: 'Interpretability',
  category: 'research',
  panels: [
    {
      // Spelled out rather than `SUITE_VIEW`: `openers-resolve.test.ts` finds view
      // ids by reading source for `id: '…'` beside a `component:`.
      id: 'interpretability.suite',
      title: 'Interpretability',
      component: HomeSection,
      role: 'document',
      icon: '🔬',
      singleton: true,
      sectionsLayout: 'rail',
      sectionsKeepAlive: true,
      stageHeader: SubjectBar,
      allowSectionSplit: true,
      regions: [
        { id: ASK_VIEW, label: 'Ask', icon: '?', key: 'a', position: 'right', defaultSize: 380 },
      ],
      agentTools: interpAgentTools,
      sections: SUITE_TOOLS.map((tool) => ({
        id: tool.section,
        label: tool.label,
        glyph: tool.glyph,
        group: tool.group,
        groupCollapsible: tool.groupCollapsible,
        groupDefaultCollapsed: tool.groupDefaultCollapsed,
        view: suiteBodyView(tool.section),
        default: tool.section === SUITE_SECTIONS.home,
      })),
    },
    ...(Object.entries(BODIES) as [SuiteSection, (typeof BODIES)[SuiteSection]][]).map(
      ([section, body]) => ({
        id: suiteBodyView(section),
        title: body.title,
        component: body.component,
        role: 'document' as const,
        embedded: true,
      }),
    ),
    {
      id: 'interp.ask',
      title: 'Ask the interpretability agent',
      component: AskPanel,
      role: 'tool',
      embedded: true,
    },
  ],
  settings: [
    {
      key: 'interp.libraryBudgetGb',
      title: 'Library disk budget (GB)',
      description:
        'How much disk the interpretability library (SAEs, transcoders, lenses under the data ' +
        'directory) may use. A download that would take it past this is refused before a byte ' +
        'is written. 0 = no limit.',
      type: 'number',
      default: 40,
    },
    {
      key: 'interp.neuronpedia.allowText',
      title: 'Let Neuronpedia see my text',
      description:
        'Feature lookups by id send nothing of yours. Testing a prompt, remote steering, NLA ' +
        'explanations and remote graph generation send your text to neuronpedia.org, and are ' +
        'refused while this is off.',
      type: 'boolean',
      default: false,
    },
  ],
  commands: [
    {
      id: 'interp.open',
      title: 'Interpretability: Open the IDE',
      run: () => void openSuiteTab(SUITE_SECTIONS.home),
    },
    {
      id: 'interp.askAboutThis',
      title: 'Interpretability: Ask the agent about what is on screen',
      run: () => askInterp('interpretability IDE'),
    },
    {
      id: 'interp.splitSwap',
      title: 'Interpretability: Swap side-by-side split views',
      run: () => {
        const id = suiteInstanceId();
        if (id) swapSuiteSplit(id);
      },
    },
    {
      id: 'interp.splitClose',
      title: 'Interpretability: Close side-by-side split view',
      run: () => {
        const id = suiteInstanceId();
        if (id) splitSuiteWith(id, null);
      },
    },
    {
      id: 'interp.neurons.open',
      title: 'Interpretability: Open Neurons (3D neuron steering)',
      run: () => void openSuiteTab(SUITE_SECTIONS.neurons),
    },
    ...NEURON_VERBS.map(([verb, title]) => ({
      id: `interp.neurons.${verb}`,
      title: `Neurons: ${title}`,
      run: () => neuronAction(verb),
    })),
  ],
  keybindings: NEURON_KEYS.map(([key, verb]) => ({
    key,
    command: `interp.neurons.${verb}`,
    when: `${whenSuiteSection(SUITE_SECTIONS.neurons)} && !textInput`,
  })),
};
