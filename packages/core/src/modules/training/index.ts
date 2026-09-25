import { lazyPane } from '../../lazy-pane';
import { revealRegionView, revealSection } from '../../layout/controller';
import { registry, type ModuleManifest } from '../../registry';
import { ownedReason, setProjectNote } from './activity';
import { notebookAgentTools } from './agentTools';
import { deleteProject, pushProject } from './api';
import { openTrainingNotebook, openTrainingRecipe } from './open';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const ProjectsPane = lazyPane(() => import('./panels/ProjectsPane'), 'ProjectsPane');
const NotebookPane = lazyPane(() => import('./panels/NotebookPane'), 'NotebookPane');
const RecipePane = lazyPane(() => import('./panels/RecipePane'), 'RecipePane');
const SweepPane = lazyPane(() => import('./panels/SweepPane'), 'SweepPane');
const MetricsPane = lazyPane(() => import('./panels/MetricsPane'), 'MetricsPane');
const ModelGraphPane = lazyPane(() => import('./panels/ModelGraphPane'), 'ModelGraphPane');
const RolloutPane = lazyPane(() => import('./panels/RolloutPane'), 'RolloutPane');
const ManimPane = lazyPane(() => import('./panels/ManimPane'), 'ManimPane');
const LearnPane = lazyPane(() => import('./panels/LearnPane'), 'LearnPane');

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
  category: 'research',
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
      title: 'Training Notebook',
      component: NotebookPane,
      role: 'document',
      editor: true,
      icon: '🧠',
      // The training workbench as regions on the notebook itself. One strip, one
      // view at a time — `defaultSize` is declared once for the position (the
      // first decl carrying one wins) because a loss curve and a dagre graph are
      // both wider than the engine's 300px default, and a companion you cannot
      // read is one you open, squint at, and close again.
      //
      // Learn comes first and opens by default: the other strips show what is
      // happening, this one says what it means — which is the point of doing
      // research *in* this app rather than in a bare notebook.
      regions: [
        {
          id: 'training.learn',
          label: 'Learn',
          icon: '✦',
          key: 'l',
          position: 'right',
          defaultOpen: true,
          defaultSize: 400,
        },
        { id: 'training.metrics', label: 'Metrics', icon: '📈', key: 'm', position: 'right' },
        {
          id: 'training.modelgraph',
          label: 'Architecture',
          icon: '🕸',
          key: 'a',
          position: 'right',
        },
        { id: 'training.manim', label: 'Manim', icon: '🎬', position: 'right' },
        { id: 'training.rollout', label: 'Rollout', icon: '🎮', key: 'u', position: 'right' },
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
      // The dataset picker rides beside the form rather than in another tab:
      // "which dataset, in which shape" is a question you have *while* looking at
      // the recipe, and a tab switch is where the shape check gets skipped.
      regions: [
        { id: 'datasets.picker', label: 'Dataset', icon: '📚', key: 'd', position: 'right' },
      ],
    },
    {
      // Params-bound like the recipe: a sweep varies one project's recipe, and two
      // projects open at once must not share an axis list.
      id: 'training.sweep',
      title: 'Ablation sweep',
      component: SweepPane,
      role: 'document',
      icon: '🎚',
    },
  ],
  explorerSources: [
    { id: 'projects', label: 'Projects', icon: '🗂', view: 'training.projects', key: 'j' },
  ],
  /**
   * What you can do to a project.
   *
   * The row in the projects list used to carry all of this inline: `Open notebook`,
   * `🧪 Recipe`, `⇪ Kaggle`, `⇪ Colab` and `✕`, five peer buttons squeezed onto one
   * flex line beside the name, the status and the owner badge — in a 280px dock.
   * Nothing read as primary, a push to somebody else's servers sat at the same
   * weight as opening the notebook, and at that width each button was a couple of
   * characters wide. The row is one target now (it opens the notebook) and
   * everything else is here, where a menu can afford verbs and an explanation.
   */
  contextMenu: [
    {
      kind: 'training.project',
      items: (target) => {
        const projectId = String(target.projectId ?? '');
        const owner = target.owner ? String(target.owner) : '';
        // An owned project is working storage for another module: no scaffolded
        // notebook, and a venv without ipykernel. Disabled *with the reason*
        // rather than absent — the reason is the whole value, and hiding the
        // items would make an inert project look identical to a healthy one.
        const blocked = owner ? { disabled: true, detail: ownedReason(owner) } : {};
        const push = (provider: 'kaggle' | 'colab', label: string) => ({
          id: `training.push.${provider}`,
          label,
          ...blocked,
          run: () => {
            setProjectNote(projectId, `pushing to ${label}…`);
            void pushProject(projectId, provider)
              .then((r) =>
                setProjectNote(projectId, r.url ? `pushed → ${r.url}` : `push: ${r.status}`),
              )
              .catch((e: Error) => setProjectNote(projectId, `push failed: ${e.message}`));
          },
        });
        return [
          {
            id: 'training.open',
            label: 'Open notebook',
            hint: 'Click',
            ...blocked,
            run: () => openTrainingNotebook(projectId, 'main.ipynb'),
          },
          {
            id: 'training.recipe',
            label: 'Fine-tuning recipe',
            detail: owner
              ? ownedReason(owner)
              : "A typed form that writes cells into this project's notebook.",
            disabled: Boolean(owner),
            run: () => openTrainingRecipe(projectId),
          },
          {
            id: 'training.push',
            label: 'Push a copy to',
            ...blocked,
            // One verb, two destinations. They were two sibling buttons, which
            // read as two unrelated features rather than one choice of target.
            submenu: [push('kaggle', 'Kaggle'), push('colab', 'Colab')],
            run: () => undefined,
          },
          {
            id: 'training.delete',
            label: 'Delete project',
            detail: 'Removes its venv and downloaded data from disk.',
            danger: true,
            // Never blocked: an owned project is exactly the one you might want
            // the disk back from, and it is the only action that still applies.
            run: () => {
              setProjectNote(projectId, 'deleting…');
              void deleteProject(projectId)
                .then(() => setProjectNote(projectId, ''))
                .catch((e: Error) => setProjectNote(projectId, `delete failed: ${e.message}`));
            },
          },
        ];
      },
    },
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
      id: 'training.learn',
      title: 'Learn',
      component: LearnPane,
      role: 'widget',
      icon: '✦',
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
      id: 'training.openSweep',
      title: 'Training: Open the ablation sweep',
      run: () => registry.openPanel('training.sweep'),
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
      id: 'training.openLearn',
      title: 'Training: Explain this cell and run',
      run: () => revealRegionView('training.learn'),
    },
  ],
  frames: [
    /**
     * The AI-research workspace: the loop from *material* to *answer*.
     *
     * The Training frame above starts at a notebook, which assumes you already
     * know what you are training on. This one starts a step earlier and ends a
     * step later, because that is where the questions actually are: browse or
     * build a dataset, check its shape suits the task, run a grid over the knob
     * you are unsure about, and read which one moved the metric.
     *
     * The arrangement follows the loop left to right. Datasets on the left because
     * everything downstream is a property of what you picked; recipe over sweep in
     * the middle because a sweep is a recipe with axes and reads as one; localtrack
     * and evals on the right because a training curve is not an answer — a
     * comparison and a score are.
     */
    {
      // NOT `research` — the layouts module already owns that id for the
      // paper-reading frame, and two frames with one id collide in the tab strip.
      id: 'ai-research',
      name: 'AI Research',
      description:
        'The loop from material to answer: browse a dataset, sweep the knob you are unsure of, read which point moved the metric — then step through the model layer by layer.',
      // Not 🔬: the paper-reading `research` preset and the llama.cpp Traces
      // section both use it, and three identical glyphs in one strip is no glyph.
      icon: '🧪',
      // `datasets` preloaded alongside `training`: the first thing asked of the
      // agent in this frame is almost always about data.
      agent: 'trainer',
      frame: {
        center: {
          split: 'row',
          sizes: [0.34, 0.66],
          children: [
            // The Python reference tabs with the data: the two things you look
            // things up in before writing a cell.
            { tabs: ['datasets.browser', 'docviewer.browse'], active: 0 },
            {
              split: 'row',
              sizes: [0.55, 0.45],
              children: [
                { pane: 'training.notebook' },
                {
                  // Did it help (localtrack, evals), what did the model do inside
                  // (the layer stepper and the architecture it walks), and what did
                  // the agent do (trajectories). Seeding skips any id whose module
                  // is disabled, so this degrades rather than breaks.
                  tabs: [
                    'localtrack.workspace',
                    'evals.hub',
                    'llamacpp.server',
                    'interpretability.architecture',
                    'trajectories.hub',
                  ],
                  active: 0,
                },
              ],
            },
          ],
        },
        docks: {
          // Explorer, not `training.projects`: that view is `embedded`, and a
          // saved layout strips embedded views out of docks on load — so the
          // Projects dock this used to seed vanished on the first reload.
          left: { tools: ['explorer.home'], size: 260 },
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], size: 180, visible: false },
        },
      },
    },
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
     * The document area seeds `training.notebook`. It used to seed **empty**,
     * because the notebook is params-bound (`{projectId, notebook}`) and a preset's
     * `tabs` carry no params — so seeding it opened a pane reading "No project".
     * The pane resolves its own default now (`last-project.ts`: the project you were
     * last in, falling back to the project picker), so opening this workspace lands
     * on the notebook you left rather than on an empty area with a dock to go
     * hunting in. Explorer's Projects section stays in the left dock: it is still
     * the entry point for a *different* project, and what it opens retargets this
     * pane in place.
     */
    {
      id: 'training',
      name: 'Training',
      description:
        'The whole fine-tune in one frame — write the recipe, run it, watch the curves, convert the checkpoint and score it.',
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
              children: [
                { pane: 'training.notebook' },
                { tabs: ['evals.hub', 'llamacpp.server'], active: 0 },
              ],
            },
            { tabs: ['training.metrics', 'localtrack.workspace'], active: 0 },
          ],
        },
        docks: {
          // Explorer only. `training.projects` used to be docked beside it, and
          // Explorer's **Projects section is that same pane** — so the dock
          // opened the identical list twice, one above the other, and the second
          // copy was the one the workspace description called the entry point.
          // The notebook's left region strip still points at `training.projects`
          // directly, which is what `embedded: true` on it is for.
          left: { tools: ['explorer.home'], size: 280 },
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
