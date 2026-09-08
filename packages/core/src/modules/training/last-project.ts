/**
 * The training project you were last working in.
 *
 * Both `training.notebook` and `training.recipe` are **params-bound** — they mean
 * nothing without a `projectId` — and a `FramePreset`'s `tabs` carry no params. So
 * the two workspaces built around them (`training`, `ai-research`) had to seed an
 * empty centre area, and opening either one landed you on "Empty area" until you
 * went to the projects pane and picked something. Every time. The comment on the
 * `training` preset states this as a known consequence of the preset format.
 *
 * It is not really a preset-format problem, it is a missing default: there is
 * exactly one project a person means when they open the training workspace and do
 * not say which, and it is the one they were in last. With that recorded, the
 * presets can seed the notebook itself and land on real work.
 *
 * `localStorage`, following `layout/recents.ts`: this is a per-viewer convenience,
 * not shared state and not a setting. It is deliberately **not** a `SettingDecl` —
 * that would put "Last project" on the settings page as something to configure,
 * and `GET /api/settings` hands the whole bag to every plugin.
 *
 * Every read and write is guarded. Private mode, a storage quota and a browser set
 * to block site data all throw on access rather than returning null, and losing the
 * last project is never worth failing an open over — the panes fall back to the
 * project picker, which is what they showed before this existed.
 */

const KEY = 'horrible.training.lastProject';

/** The last project opened, or `null` if there is none / storage is unavailable. */
export function lastProjectId(): string | null {
  try {
    return localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
}

/** Record that a project was opened. Called by the openers, not by the panes. */
export function noteProjectOpened(projectId: string): void {
  if (!projectId) return;
  try {
    localStorage.setItem(KEY, projectId);
  } catch {
    // See above.
  }
}

/**
 * Forget the remembered project.
 *
 * Called when a pane resolves the fallback and the backend says that project does
 * not exist — deleted, or a directory that lost its `project.json`. Without this
 * the workspace would greet you with the same dead project on every open, and the
 * user has no way to see, let alone clear, a value stored here.
 */
export function forgetLastProject(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // See above.
  }
}
