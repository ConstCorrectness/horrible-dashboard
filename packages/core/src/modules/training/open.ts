/**
 * The single entry point for showing a training notebook, so the projects pane
 * and anything else that opens one converge on the same pane rather than
 * splitting off a new one per click.
 */
import { openDocument } from '../../layout/controller';
import { noteProjectOpened } from './last-project';

/**
 * Open a project's notebook. Reopening the same one focuses the pane that holds
 * it; otherwise a clean training-notebook pane is taken over in place.
 *
 * The `title` param names the notebook on the tab and the taskbar button — the
 * view's own title is the same string for every project, so without it two open
 * projects are two identical buttons.
 */
export function openTrainingNotebook(projectId: string, notebook: string): void {
  // Recorded here rather than in the pane, so every route into a project — the
  // projects pane, the agent's `show`, a deep link from the sweep or recipe panes —
  // updates it through one call. A pane recording it on mount would also record the
  // fallback it just resolved, which is a loop, not a memory.
  noteProjectOpened(projectId);
  openDocument(
    'training.notebook',
    `training.notebook:${projectId}/${notebook}`,
    { projectId, notebook, title: notebook },
    () => true,
  );
}

/**
 * Open a project's fine-tuning recipe. Same shape as the notebook opener and for
 * the same reason: a recipe is bound to one project, so reopening the same one
 * focuses its pane instead of splitting a second form onto the screen.
 */
export function openTrainingRecipe(projectId: string): void {
  noteProjectOpened(projectId);
  openDocument('training.recipe', `training.recipe:${projectId}`, { projectId }, () => true);
}
