import { revealRegionView, revealSection } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import { notebookAgentTools } from './agentTools';
import { ManimPane } from './panels/ManimPane';
import { MetricsPane } from './panels/MetricsPane';
import { ModelGraphPane } from './panels/ModelGraphPane';
import { NotebookPane } from './panels/NotebookPane';
import { ProjectsPane } from './panels/ProjectsPane';
import { RecipePane } from './panels/RecipePane';
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
    {
      // Non-singleton and params-bound like the notebook: a recipe belongs to one
      // project, and two projects open at once must not share a form. A center
      // pane rather than a region strip because it is a real form — the narrow
      // companion strips beside the notebook would make every row wrap.
      id: 'training.recipe',
      title: 'Recipe',
      component: RecipePane,
      role: 'document',
      icon: '🧪',
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
      id: 'training.openRecipe',
      title: 'Training: Open the fine-tuning recipe',
      // Needs a project, and the pane says so rather than guessing one: opening
      // the palette entry from nowhere is how you'd end up editing a recipe that
      // belongs to a project you weren't looking at.
      run: () => registry.openPanel('training.recipe'),
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
    /**
     * The fine-tuning workspace: the whole loop in one frame — write the recipe,
     * run it, watch the curves, convert the checkpoint, and score it — rather than
     * five panes you open one at a time from the command palette.
     *
     * The pairing that carries the idea is **evals directly under the notebook**: a
     * regression in Results names a case, and the code that produced it is one pane
     * up. Everything else is arranged around that. `llamacpp.server` shares the
     * lower area because converting a checkpoint and serving it to be scored are
     * the same errand, and the right column is where a run is *watched* —
     * `training.metrics` live, `localtrack.workspace` for comparing it against
     * previous runs.
     *
     * The document area is seeded **empty** on purpose. `training.notebook` and
     * `training.recipe` are params-bound (`{projectId, notebook}`) and a preset's
     * `tabs` carry no params, so seeding them here would open two panes reading
     * "No project — open me from the Training projects pane". Which is why that
     * pane is first in the left dock: it is the entry point, and the panes it opens
     * land in the empty area.
     */
    {
      id: 'training',
      name: 'Training',
      icon: '🧠',
      // Scoped to the work: `training` + `evals` + `localtrack`, preloading only
      // the first. Deliberately *not* `llamacpp` or `hardware` — those namespaces
      // are settings keys, not agent tools, and a group naming no tools is granted
      // silently (groups are the tool name's prefix; see `_group_of`). `editor` and
      // `files` are permitted but not preloaded, so reading a recipe costs a
      // `load_tools` rather than schema space on every turn.
      agent: 'trainer',
      frame: {
        center: {
          split: 'row',
          sizes: [0.62, 0.38],
          children: [
            {
              split: 'column',
              sizes: [0.58, 0.42],
              children: [{ tabs: [] }, { tabs: ['evals.hub', 'llamacpp.server'], active: 0 }],
            },
            { tabs: ['training.metrics', 'localtrack.workspace'], active: 0 },
          ],
        },
        docks: {
          left: { tools: ['training.projects', 'explorer.home'], size: 280 },
          right: { tools: ['agent.chat'], size: 360 },
          // Present but closed: a fine-tune is exactly when you want to see what
          // the node is talking to, and exactly when you do not want a log tailing
          // under the charts by default.
          bottom: { tools: ['observability.io'], size: 180, visible: false },
        },
      },
    },
  ],
};
