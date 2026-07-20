import { registry, type ModuleManifest } from '../../registry';
import { ModelDiagram } from './ModelDiagram';
import { InterpretabilityPanel, InterpretabilityWidget } from './view';

/**
 * See what the model is actually being handed.
 *
 * The agent loop rebuilds its context every round — system prompt, tool guides,
 * replayed history, the focused editor buffer, the user turn, and a tool list that
 * progressive disclosure recomputes as the model calls `load_tools`. None of it was
 * observable before this pane. See docs/modules/interpretability.mdx.
 */
export const interpretabilityModule: ModuleManifest = {
  id: 'interpretability',
  title: 'Interpretability',
  panels: [
    {
      // `widget` rather than `tool`: this is the subject of its workspace, so it
      // has to be able to hold a center area (a center area takes document or
      // widget panes; tool panes are dock-only).
      id: 'interpretability.context',
      title: 'Context window',
      component: InterpretabilityPanel,
      role: 'widget',
      icon: '🔍',
      singleton: true,
    },
    {
      // Sits beside the context view: what the model *is*, next to what it was
      // given. Also `widget` so it can hold a center area of its own.
      id: 'interpretability.architecture',
      title: 'Model diagram',
      component: ModelDiagram,
      role: 'widget',
      icon: '🧬',
      singleton: true,
    },
  ],
  widgets: [
    {
      // The compact counterpart — dockable alongside whatever you're actually
      // working on, with the full inspector one region-strip click away.
      id: 'interpretability.budget',
      title: 'Context budget',
      component: InterpretabilityWidget,
      role: 'tool',
      icon: '▤',
      defaultDock: 'bottom',
      regions: [
        { id: 'interpretability.context', label: 'Context', icon: '🔍', position: 'bottom' },
      ],
    },
  ],
  /**
   * The Interpretability workspace: the context inspector front and centre, the ask
   * bar beside it, and the wire traffic underneath.
   *
   * The agent chat is not decoration here — the pane only has anything to show once
   * a turn has run, so a layout without a way to drive one is a dead end. They sit
   * in separate visible areas (center + dock) on purpose: panes in inactive tabs
   * unmount, which would drop the live `/ws` subscription mid-turn.
   */
  frames: [
    {
      id: 'interpretability',
      name: 'Interpretability',
      icon: '🔍',
      frame: {
        center: {
          split: 'row',
          sizes: [0.62, 0.38],
          children: [
            { pane: 'interpretability.context' },
            { pane: 'interpretability.architecture' },
          ],
        },
        docks: {
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], size: 180, visible: false },
        },
      },
    },
  ],
  commands: [
    {
      id: 'interpretability.open',
      title: 'Interpretability: Inspect context window',
      run: () => registry.openPanel('interpretability.context'),
    },
    {
      id: 'interpretability.openDiagram',
      title: 'Interpretability: Show model diagram',
      run: () => registry.openPanel('interpretability.architecture'),
    },
  ],
  // `mod+` so one binding covers Ctrl and Cmd, matching the other modules. Not
  // ctrl+shift+i — that's DevTools in every browser, and the browser wins.
  keybindings: [{ key: 'mod+shift+x', command: 'interpretability.open' }],
  settings: [
    {
      key: 'interpretability.modelRepo',
      title: 'Model repository',
      description:
        'Hugging Face repo describing the loaded model. Drives both halves of the pane: ' +
        'tokenizer.json for exact token counts, and config.json for the architecture diagram. ' +
        'Leave empty to infer it from the model name — set it when the model id is a local ' +
        'name rather than a repo (LM Studio builds often are), or when the repo is gated and ' +
        'you have no Hugging Face connection. Without a resolvable repo, token counts fall ' +
        'back to chars/4 estimates and the diagram has no source.',
      type: 'string',
      default: '',
    },
  ],
};
