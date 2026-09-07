import { registry, type ModuleManifest } from '../../registry';
import { DatasetPicker } from './panels/DatasetPicker';
import { DatasetsPane } from './panels/DatasetsPane';

/**
 * Datasets: the material a fine-tune is made of, as a first-class object.
 *
 * A recipe used to hold a dataset *name*. Meanwhile four places in this app
 * already produced or inspected training material — the Hub peek layer in evals,
 * the Hub browser in lab, `evals.export`'s SFT jsonl, the trajectories exporter —
 * and none of them was reachable from the recipe form.
 *
 * This module is the seam. A registered dataset carries its source, split,
 * detected shape and column map, so a run is reproducible and a sweep's twelve
 * points provably ate the same rows.
 *
 * The picker is `embedded`: it exists as a region on the recipe pane rather than
 * as a destination of its own, because "which dataset" is a question you answer
 * while looking at the recipe, not in another tab.
 *
 * See docs/modules/datasets.mdx.
 */
export const datasetsModule: ModuleManifest = {
  id: 'datasets',
  title: 'Datasets',
  settings: [
    {
      key: 'datasets.peekRows',
      title: 'Preview rows',
      description: 'Rows fetched when inspecting a dataset.',
      type: 'number',
      default: 5,
    },
    {
      key: 'datasets.tokenSample',
      title: 'Token-stat sample size',
      description:
        'Examples sampled when measuring token lengths. A histogram is an estimate; ' +
        'downloading a million rows to draw one would defeat asking before the run.',
      type: 'number',
      default: 200,
    },
    {
      key: 'datasets.synthEngine',
      title: 'Synthetic data engine',
      description:
        'Which engine the builder’s synthesize step uses. Nemo needs the NVIDIA connector.',
      type: 'enum',
      enumValues: ['local', 'nemo'],
      default: 'local',
    },
  ],
  panels: [
    {
      id: 'datasets.browser',
      title: 'Datasets',
      component: DatasetsPane,
      role: 'document',
      icon: '📚',
      singleton: true,
      dockable: 'left',
    },
  ],
  widgets: [
    {
      id: 'datasets.picker',
      title: 'Dataset',
      component: DatasetPicker,
      role: 'widget',
      icon: '📚',
      // A companion of the recipe form, not a destination: it answers a question
      // you have while looking at the recipe.
      embedded: true,
    },
  ],
  explorerSources: [
    { id: 'datasets', label: 'Datasets', icon: '📚', view: 'datasets.browser', key: 'd' },
  ],
  commands: [
    {
      id: 'datasets.open',
      title: 'Datasets: Browse and register datasets',
      run: () => registry.openPanel('datasets.browser'),
    },
  ],
};
