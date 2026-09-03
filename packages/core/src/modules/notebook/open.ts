/**
 * The single entry point for showing a notebook — used by the browser pane, the
 * create flow, and the agent tools, so they all land in the same pane instead of
 * splitting off a new one each time.
 */
import { openDocument } from '../../layout/controller';

/**
 * Open `path` in a notebook pane. Reopening the same notebook focuses the pane
 * that already holds it; otherwise an existing notebook pane with no unsaved
 * edits is taken over in place (a notebook autosaves through the kernel session,
 * so a clean pane holds nothing to lose). Only when every notebook pane is dirty
 * — or none is open — does a new pane appear.
 *
 * The `title` param is what the tab and the taskbar button actually read
 * (`layout/taskbar.ts`, `AreaHeader.tsx`), so it carries the file name: two open
 * notebooks both labelled "Notebook Editor" tell you nothing about which is which.
 * The header prefers `title` alone, so passing `path` is not enough.
 */
export function openNotebook(path: string): void {
  const title = path.split(/[\\/]/).pop() || path;
  openDocument('notebook.editor', `notebook.editor:${path}`, { path, title }, () => true);
}
