/**
 * The one-line "what is happening to this project right now" note, per project.
 *
 * It exists because the actions that produce such a line stopped living in the
 * pane that shows it. Pushing to Kaggle or Colab is a project action, and project
 * actions are now a **context menu** contributed from the module manifest — which
 * is a plain function with no access to a React component's state. The push
 * progress used to be a `useState` inside `ProjectsPane`, so moving the action out
 * would have silently dropped every "pushing…/pushed → url/push failed" line on
 * the floor.
 *
 * Deliberately module-level and ephemeral. It is the same *slot* the pane already
 * fills from the `env_progress` / `fetch_progress` ws events — the last thing that
 * happened, overwritten by the next — so it is not persisted, not a setting, and
 * not on the wire. A backend-owned fact about a project belongs in `project_changed`.
 */

type Listener = () => void;

const notes = new Map<string, string>();
const listeners = new Set<Listener>();

/** The current note for a project, or `''` when there is nothing to say. */
export function projectNote(projectId: string): string {
  return notes.get(projectId) ?? '';
}

/** Set (or, with an empty string, clear) a project's note and notify the panes. */
export function setProjectNote(projectId: string, note: string): void {
  if (!projectId) return;
  if (note) notes.set(projectId, note);
  else notes.delete(projectId);
  for (const listener of [...listeners]) listener();
}

export function subscribeProjectNotes(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Why a project's authoring actions are unavailable.
 *
 * These projects are working storage — `evals` builds one per suite to run Hugging
 * Face benchmarks in — created straight through `create_project`, so they have no
 * scaffolded `main.ipynb` and their venv holds only the benchmark's requirements
 * (no `ipykernel`). Every authoring action is one that could only fail.
 *
 * Lives here rather than in the pane because both the row (as a tooltip) and the
 * context menu (as an item `detail`) need to say it, and they are now in different
 * files.
 */
export function ownedReason(owner: string): string {
  return (
    `Working storage for the ${owner} module — it has no notebook of its own ` +
    `and its venv cannot run one. Delete it here if you want the disk back.`
  );
}
