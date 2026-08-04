import { revealRegionView, revealSection } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import { notebookAgentTools } from './agentTools';
import { ManimPane } from './panels/ManimPane';
import { MetricsPane } from './panels/MetricsPane';
import { ModelGraphPane } from './panels/ModelGraphPane';
import { NotebookPane } from './panels/NotebookPane';
import { ProjectsPane } from './panels/ProjectsPane';
import { RolloutPane } from './panels/RolloutPane';
import { TrainingPeersPane } from './panels/TrainingPeersPane';

/**
 * Training module: notebook-driven neural-network training. Projects come from
 * the pluggable environment-provider layer (Kaggle competitions/datasets, HF
 * datasets, Gymnasium envs, backend-plugin providers); each project owns a uv
 * venv and an .ipynb notebook run on a per-project Jupyter kernel, with live
 * metrics/architecture/rollout panes fed over the `training` ws channel. See
 * docs/modules/training.mdx.
 */
export const trainingModule: ModuleManifest = {
  id: 'training',
  title: 'Training',
  settings: [
    {
      key: 'training.projectsRoot',
      title: 'Projects root',
      description: 'Directory that holds training projects (one subdir each).',
      type: 'string',
      default: '~/horrible/training',
    },
    {
      key: 'training.defaultPython',
      title: 'Default Python version',
      description: 'Python version uv pins for new project venvs.',
      type: 'string',
      default: '3.12',
    },
    {
      key: 'training.kaggle.username',
      title: 'Kaggle username',
      description: 'Kaggle API username; falls back to ~/.kaggle/kaggle.json.',
      type: 'string',
      default: '',
    },
    {
      key: 'training.kaggle.key',
      title: 'Kaggle API key',
      description: 'Kaggle API key; falls back to ~/.kaggle/kaggle.json.',
      type: 'string',
      default: '',
    },
    {
      key: 'training.hf.token',
      title: 'Hugging Face token',
      description: 'Optional token for private/gated datasets.',
      type: 'string',
      default: '',
    },
    {
      key: 'training.google.clientId',
      title: 'Google OAuth client id',
      description: 'Your own Google OAuth client (installed-app type) for Colab push via Drive.',
      type: 'string',
      default: '',
    },
    {
      key: 'training.google.clientSecret',
      title: 'Google OAuth client secret',
      description: 'Secret for the Google OAuth client used by Colab push.',
      type: 'string',
      default: '',
    },
    {
      key: 'training.manim.quality',
      title: 'Manim render quality',
      description: 'Default manim render quality (l=480p, m=720p, h=1080p).',
      type: 'enum',
      enumValues: ['l', 'm', 'h'],
      default: 'm',
    },
    {
      key: 'training.metrics.bufferPoints',
      title: 'Metrics buffer points',
      description: 'Metric points kept per run for chart backfill.',
      type: 'number',
      default: 5000,
    },
    {
      key: 'training.fabric.advertise',
      title: 'Advertise training compute',
      description: 'Broadcast to peers that this node is offering GPU / seeking help.',
      type: 'enum',
      enumValues: ['off', 'offering', 'seeking'],
      default: 'off',
    },
    {
      key: 'training.fabric.note',
      title: 'Training ad note',
      description: 'Free-text note attached to your training ad (hardware, availability…).',
      type: 'string',
      default: '',
    },
  ],
  panels: [
    {
      id: 'training.projects',
      title: 'Training Projects',
      component: ProjectsPane,
      role: 'tool',
      icon: '🗂',
      defaultDock: 'left',
      singleton: true,
      // A section of Explorer now — see modules/explorer. Stays registered so the
      // region strip on `training.notebook` keeps working unchanged.
      embedded: true,
    },
    {
      // Non-singleton: one pane per open notebook (params: {projectId, notebook}).
      id: 'training.notebook',
      title: 'Notebook',
      component: NotebookPane,
      role: 'document',
      editor: true,
      icon: '🧠',
      // The training workbench as regions on the notebook itself.
      regions: [
        { id: 'training.metrics', label: 'Metrics', icon: '📈', key: 'm', position: 'right' },
        {
          id: 'training.modelgraph',
          label: 'Architecture',
          icon: '🕸',
          key: 'a',
          position: 'right',
        },
        { id: 'training.rollout', label: 'Rollout', icon: '🎮', key: 'u', position: 'right' },
        { id: 'training.manim', label: 'Manim', icon: '🎬', position: 'right' },
        { id: 'training.peers', label: 'Peers', icon: '🤝', position: 'right' },
        { id: 'training.projects', label: 'Projects', icon: '🗂', key: 'p', position: 'left' },
      ],
      // Full cell CRUD + execute for the agent (group `notebook`).
      agentTools: notebookAgentTools,
    },
  ],
  explorerSources: [
    { id: 'projects', label: 'Projects', icon: '🗂', view: 'training.projects', key: 'j' },
  ],
  widgets: [
    {
      id: 'training.metrics',
      title: 'Training Metrics',
      component: MetricsPane,
      role: 'widget',
      icon: '📈',
    },
    {
      id: 'training.modelgraph',
      title: 'Model Architecture',
      component: ModelGraphPane,
      role: 'widget',
      icon: '🕸',
      // Embedded: a companion strip of the notebook it rides on. `training.metrics`
      // deliberately is NOT — it is seeded as the Training workspace's own center
      // pane, so it is a destination in a way these four are not.
      embedded: true,
    },
    {
      id: 'training.rollout',
      title: 'Rollout Stream',
      component: RolloutPane,
      role: 'widget',
      icon: '🎮',
      embedded: true,
    },
    {
      id: 'training.manim',
      title: 'Manim Renders',
      component: ManimPane,
      role: 'widget',
      icon: '🎬',
      embedded: true,
    },
    {
      id: 'training.peers',
      title: 'Training Peers',
      component: TrainingPeersPane,
      role: 'widget',
      icon: '🤝',
      embedded: true,
    },
  ],
  commands: [
    {
      id: 'training.open',
      title: 'Training: Open projects',
      run: () => {
        revealSection('projects', 'explorer.home');
      },
    },
    {
      id: 'training.openMetrics',
      title: 'Training: Open metrics charts',
      run: () => registry.openPanel('training.metrics'),
    },
    {
      id: 'training.openModelGraph',
      title: 'Training: Open model architecture',
      run: () => revealRegionView('training.modelgraph'),
    },
    {
      id: 'training.openRollout',
      title: 'Training: Open rollout stream',
      run: () => revealRegionView('training.rollout'),
    },
    {
      id: 'training.openManim',
      title: 'Training: Open manim renders',
      run: () => revealRegionView('training.manim'),
    },
    {
      id: 'training.openPeers',
      title: 'Training: Open training peers',
      run: () => revealRegionView('training.peers'),
    },
  ],
  frames: [
    {
      id: 'training',
      name: 'Training',
      icon: '🧠',
      frame: {
        center: {
          split: 'row',
          sizes: [0.65, 0.35],
          children: [{ tabs: [] }, { pane: 'training.metrics' }],
        },
        docks: { left: { tools: ['explorer.home'], size: 280 } },
      },
    },
  ],
};
