/**
 * The interpretability suite: one window, a rail of tools down its left edge.
 *
 * Interpretability had grown into six panes that did not know about each other —
 * the context window, the model explorer, the llama.cpp traces / stepper / lens,
 * agentpedia and the window's lens lab — joined only by a hand-built strip that
 * opened one from another. They were always one subject: the same model, read at
 * the prompt, the weights and the activations. So they are one pane, with the
 * section machinery doing what that strip did by hand (persisted, commandable,
 * nameable by the agent), and `sectionsKeepAlive` keeping each tool mounted once
 * opened — a design with unsaved edits survives a look at the lens.
 *
 * The bodies stay where they were written and are registered here as `embedded`
 * views, so `show("Stepper")` and `openPane` still find them by name. Each module
 * keeps its own commands and keybindings, re-pointed at the suite (`ids.ts`).
 * The llama.cpp pane keeps the server and the model catalog: running a model is
 * not reading one. See docs/modules/interp-suite.mdx.
 */
import { lazyPane } from '../../lazy-pane';
import { revealSection } from '../../layout/controller';
import type { ModuleManifest, PanelDecl } from '../../registry';
import { interpAgentTools } from './agent-tools';
import { ASK_VIEW, askInterp } from './ask';
import { SUITE_SECTIONS, SUITE_VIEW, type SuiteSection } from './ids';
import { SUITE_TOOLS } from './tools';

const HomeSection = lazyPane(() => import('./HomeSection'), 'HomeSection');
const ContextBody = lazyPane(() => import('./bodies/prompt'), 'ContextBody');
const RunsBody = lazyPane(() => import('./bodies/prompt'), 'RunsBody');
const ModelBody = lazyPane(() => import('./bodies/weights'), 'ModelBody');
const DesignBody = lazyPane(() => import('./bodies/weights'), 'DesignBody');
const AttentionBody = lazyPane(() => import('./attention/AttentionSection'), 'AttentionBody');
const TracesBody = lazyPane(() => import('./bodies/activations'), 'TracesBody');
const StepperBody = lazyPane(() => import('./bodies/activations'), 'StepperBody');
const LensBody = lazyPane(() => import('./bodies/activations'), 'LensBody');
const HeadsBody = lazyPane(() => import('./heads/HeadsSection'), 'HeadsBody');
const LibraryBody = lazyPane(() => import('./library/LibrarySection'), 'LibraryBody');
// The chat (and its 3D avatar) loads when the Ask strip first opens, not with the suite.
const AskPanel = lazyPane(() => import('./AskPanel'), 'AskPanel');

/**
 * Each section's body, as an embedded view. The titles are what `show("…")`
 * matches, so they are the names people used for the panes these replaced.
 */
const BODIES: Record<SuiteSection, { title: string; component: PanelDecl['component'] }> = {
  home: { title: 'Interpretability home', component: HomeSection },
  context: { title: 'Context window', component: ContextBody },
  runs: { title: 'Agent runs', component: RunsBody },
  model: { title: 'Model explorer', component: ModelBody },
  attention: { title: 'Attention layout', component: AttentionBody },
  design: { title: 'Model designer', component: DesignBody },
  traces: { title: 'Activation traces', component: TracesBody },
  stepper: { title: 'Layer stepper', component: StepperBody },
  lens: { title: 'Logit lens', component: LensBody },
  heads: { title: 'Attention heads', component: HeadsBody },
  library: { title: 'Interpretability library', component: LibraryBody },
};

export function suiteBodyView(section: SuiteSection): string {
  return `interp.${section}`;
}

export const interpSuiteModule: ModuleManifest = {
  id: 'interp',
  title: 'Interpretability',
  category: 'research',
  panels: [
    {
      // Spelled out rather than `SUITE_VIEW`: `openers-resolve.test.ts` finds view
      // ids by reading source for `id: '…'` beside a `component:`. The suite test
      // pins the two together.
      id: 'interpretability.suite',
      title: 'Interpretability',
      // Never rendered while every section has a body; a fallback, not a page.
      component: HomeSection,
      role: 'document',
      icon: '🔬',
      singleton: true,
      sectionsLayout: 'rail',
      sectionsKeepAlive: true,
      // Ask: the interpretability agent beside whatever tool is open. A region, not a
      // section — a section would replace the thing being asked about.
      regions: [
        { id: ASK_VIEW, label: 'Ask', icon: '?', key: 'a', position: 'right', defaultSize: 380 },
      ],
      agentTools: interpAgentTools,
      sections: SUITE_TOOLS.map((tool) => ({
        id: tool.section,
        label: tool.label,
        glyph: tool.glyph,
        group: tool.group,
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
      title: 'Interpretability: Open the suite',
      run: () => void revealSection(SUITE_SECTIONS.home, SUITE_VIEW),
    },
    {
      id: 'interp.askAboutThis',
      title: 'Interpretability: Ask the agent about what is on screen',
      run: () => askInterp('interpretability suite'),
    },
  ],
};
