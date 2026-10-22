import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const LabHub = lazyPane(() => import('./HubPane'), 'LabHub');

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
  category: 'research',
  panels: [
    {
      // `document`, not `widget`. Both live in a centre area, but a widget takes
      // an area *alone* — and this module's own `lab` preset tabs the hub with
      // the model explorer, the context view and llama.cpp in one area, which is
      // a document's arrangement. A widget is a readout you glance at; this is a
      // repository browser you work in.
      id: 'lab.hub',
      title: 'Hugging Face',
      component: LabHub,
      role: 'document',
      icon: '🤗',
      singleton: true,
      // One component, two sections. A model repo and a dataset repo differ in
      // exactly one rendered field, so splitting them into two panes would be two
      // copies of the same browser that drift.
      sections: [
        { id: 'models', label: 'Models', icon: '🧠', key: 'm', default: true },
        { id: 'datasets', label: 'Datasets', icon: '🗃', key: 'd' },
        // Models / Datasets are what the Hub has; Local is what *you* have. A
        // section rather than a 76th pane opener, for the reason the consolidation
        // effort gives: one table does not need a destination of its own.
        { id: 'local', label: 'Local', icon: '📦', key: 'l' },
      ],
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
