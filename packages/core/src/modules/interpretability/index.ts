import { revealSection } from '../../layout/controller';
import type { ModuleManifest } from '../../registry';
import { SUITE_SECTIONS, SUITE_VIEW, whenSuiteSection } from '../interp/ids';
import { designerAction } from './designer/actions';

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
  category: 'research',
  // No panes of its own any more: the context window and the model explorer are
  // sections of the interpretability suite (`modules/interp`). The commands stay
  // here and keep their ids — a command id is an API even when its target moves.
  commands: [
    {
      id: 'interpretability.guide',
      title: 'AI Research: Which pane to use when & why (Workflow Guide)',
      run: () => void revealSection(SUITE_SECTIONS.home, SUITE_VIEW),
    },
    {
      id: 'interpretability.open',
      title: 'Interpretability: Inspect context window',
      run: () => void revealSection(SUITE_SECTIONS.context, SUITE_VIEW),
    },
    {
      // Command id kept as-is: a keybinding or plugin naming it predates the
      // rename, and a command id is an API even when its title isn't.
      id: 'interpretability.openDiagram',
      title: 'Interpretability: Explore model structure',
      run: () => void revealSection(SUITE_SECTIONS.model, SUITE_VIEW),
    },
    // The designer's verbs, borrowed from Blender's node editor. Each one calls
    // through `designer/actions.ts`, which the Design section publishes while it
    // is mounted. The suite keeps sections mounted once opened, so the bindings
    // below are what confine these to the Design section being on screen.
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
  // The designer's are single keys, so every one of them is scoped to the Design
  // section *and* guarded with `!textInput`: unguarded, typing a `d_model` value in
  // the inspector or a class name in the code pane would delete the selected node.
  // The section matters as much as the guard: the suite keeps the designer mounted
  // behind its other tools, and `x` pressed in the lens must not delete a node.
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
      when: `${whenSuiteSection(SUITE_SECTIONS.design)} && !textInput`,
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
