import { registry, type ModuleManifest } from '../../registry';
import { designerAction } from './designer/actions';
import { ModelExplorer } from './ModelExplorer';
import { InterpretabilityPanel } from './view';

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
      // `document`: a surface you read and work in, and one that tabs. It was a
      // `widget` to make it centre-placeable, but widget means centre-placeable
      // *alone* — and the `lab` preset tabs this with the model explorer, the
      // Hugging Face hub and llama.cpp in a single area, which only documents do.
      id: 'interpretability.context',
      title: 'Context window',
      component: InterpretabilityPanel,
      role: 'document',
      icon: '🔍',
      singleton: true,
    },
    {
      // Sits beside the context view: what the model *is*, next to what it was
      // given. A document for the same reason.
      id: 'interpretability.architecture',
      title: 'Model explorer',
      component: ModelExplorer,
      role: 'document',
      icon: '🧬',
      singleton: true,
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
      // Command id kept as-is: a keybinding or plugin naming it predates the
      // rename, and a command id is an API even when its title isn't.
      id: 'interpretability.openDiagram',
      title: 'Interpretability: Explore model structure',
      run: () => registry.openPanel('interpretability.architecture'),
    },
    // The designer's verbs, borrowed from Blender's node editor. Each one calls
    // through `designer/actions.ts`, which the Design tab publishes while it is
    // mounted — so in Inspect mode, where none of these mean anything, they are
    // no-ops without a mode flag to keep in step.
    {
      id: 'interpretability.design.addNode',
      title: 'Model designer: Add node',
      run: () => designerAction('addNode'),
    },
    {
      id: 'interpretability.design.mute',
      title: 'Model designer: Mute node (ablate)',
      run: () => designerAction('toggleMute'),
    },
    {
      id: 'interpretability.design.delete',
      title: 'Model designer: Delete node',
      run: () => designerAction('deleteSelected'),
    },
    {
      id: 'interpretability.design.collapse',
      title: 'Model designer: Collapse node',
      run: () => designerAction('toggleCollapse'),
    },
    {
      id: 'interpretability.design.frameAll',
      title: 'Model designer: Frame all',
      run: () => designerAction('frameAll'),
    },
    {
      id: 'interpretability.design.enterGroup',
      title: 'Model designer: Enter group',
      run: () => designerAction('enterGroup'),
    },
    {
      id: 'interpretability.design.exitGroup',
      title: 'Model designer: Leave group',
      run: () => designerAction('exitGroup'),
    },
    {
      id: 'interpretability.design.group',
      title: 'Model designer: Group selection',
      run: () => designerAction('groupSelection'),
    },
  ],
  // `mod+` so one binding covers Ctrl and Cmd, matching the other modules. Not
  // ctrl+shift+i — that's DevTools in every browser, and the browser wins.
  //
  // The designer's are single keys, so every one of them is scoped to the pane
  // *and* guarded with `!textInput`: unguarded, typing a `d_model` value in the
  // inspector or a class name in the code pane would delete the selected node.
  // A binding naming `paneFocus` already beats an unscoped global, so the pane
  // does not need to take capture — which matters, because the same pane hosts
  // Inspect mode, where swallowing the keyboard would be wrong.
  keybindings: [
    { key: 'mod+shift+x', command: 'interpretability.open' },
    ...(
      [
        ['shift+a', 'addNode'],
        ['m', 'mute'],
        ['x', 'delete'],
        ['h', 'collapse'],
        ['home', 'frameAll'],
        ['tab', 'enterGroup'],
        ['shift+tab', 'exitGroup'],
        ['mod+g', 'group'],
      ] as const
    ).map(([key, verb]) => ({
      key,
      command: `interpretability.design.${verb}`,
      when: "paneFocus == 'interpretability.architecture' && !textInput",
    })),
  ],
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
    {
      key: 'interpretability.ggufPath',
      title: 'Model weights file (.gguf)',
      description:
        'Absolute path to the GGUF the server has loaded, for the tensor inventory in the ' +
        'model explorer. Leave empty to find it automatically: Ollama models are located in ' +
        'its blob store and LM Studio models through its own index. Set this for llama.cpp, ' +
        'vLLM or any other server whose model store we cannot walk — and to override the ' +
        'automatic result if it ever picks the wrong file.',
      type: 'string',
      default: '',
    },
  ],
};
