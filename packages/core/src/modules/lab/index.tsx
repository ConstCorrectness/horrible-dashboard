import { registry, type ModuleManifest } from '../../registry';
import { LabHub } from './HubPane';

/**
 * The Lab: the workspace for building, studying and fine-tuning models.
 *
 * Deliberately an **umbrella**, not a merge. Interpretability, training, mcp and
 * library keep owning their panes, manifests, docs and tests; this module
 * contributes a composition of them plus the one surface none of them had — a way
 * for a *person* to browse the Hugging Face Hub. The agent has been able to search
 * models and datasets since the connector landed.
 *
 * Why a new frame rather than extending the existing ones: `interpretability` is
 * two inspection panes beside a chat, and `training` is deliberately thin (an empty
 * centre plus the metrics strip). Neither is a place to *write* a fine-tuning
 * script with the model, its context, and the Hub all in reach — which is the
 * actual working position this frame seeds.
 */
export const labModule: ModuleManifest = {
  id: 'lab',
  title: 'Lab',
  panels: [
    {
      // `widget` rather than `tool`: this sits in a centre area beside the notebook,
      // and a centre area takes document or widget panes only.
      id: 'lab.hub',
      title: 'Hugging Face',
      component: LabHub,
      role: 'widget',
      icon: '🤗',
      singleton: true,
      // One component, two sections. A model repo and a dataset repo differ in
      // exactly one rendered field, so splitting them into two panes would be two
      // copies of the same browser that drift.
      sections: [
        { id: 'models', label: 'Models', icon: '🧠', key: 'm', default: true },
        { id: 'datasets', label: 'Datasets', icon: '🗃', key: 'd' },
      ],
    },
  ],
  frames: [
    {
      id: 'lab',
      name: 'Lab',
      icon: '🧪',
      frame: {
        center: {
          split: 'row',
          sizes: [0.62, 0.38],
          children: [
            // Where the work happens. The notebook already hosts the training
            // metrics, model graph, rollout and projects as region strips, so it
            // brings most of the training module with it.
            { tabs: ['training.notebook', 'editor.buffer'], active: 0 },
            // What you look things up in, tabbed rather than tiled: at this width
            // three stacked panes would each be too short to read.
            {
              tabs: ['lab.hub', 'interpretability.architecture', 'interpretability.context'],
              active: 0,
            },
          ],
        },
        docks: {
          left: { tools: ['explorer.home'], size: 260 },
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], size: 180, visible: false },
        },
      },
    },
  ],
  commands: [
    {
      id: 'lab.openHub',
      title: 'Lab: Browse Hugging Face',
      run: () => registry.openPanel('lab.hub'),
    },
  ],
};
