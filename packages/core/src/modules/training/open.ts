/**
 * The single entry point for showing a training notebook, so the projects pane
 * and anything else that opens one converge on the same pane rather than
 * splitting off a new one per click.
 */
import { openDocument } from '../../layout/controller';

/**
 * Open a project's notebook. Reopening the same one focuses the pane that holds
 * it; otherwise a clean training-notebook pane is taken over in place.
 */
export function openTrainingNotebook(projectId: string, notebook: string): void {
  openDocument(
    'training.notebook',
    `training.notebook:${projectId}/${notebook}`,
    { projectId, notebook },
    () => true,
  );
}
