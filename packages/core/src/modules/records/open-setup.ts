/**
 * Open the table designer: the **Setup** section of the Rows pane.
 *
 * Table setup used to be a window of its own (`records.schema`), which put a
 * one-off form in the launcher beside the views it configures and opened a new
 * document every time you added a column. It is now a section of `records.grid`,
 * so it opens *in* the grid you were looking at and hands back to it on save.
 *
 * Params are set on the instance `revealSection` lands on, rather than passed to
 * `openPanel` — the grid is not a singleton, and opening one per click is the
 * window-sprawl this replaced. The key is `setupSchemaId`, never `schemaId`:
 * that one *pins* a grid to a table, and a designer opened for "new" would pin
 * the grid to a table that does not exist.
 */
import { revealSection } from '../../layout/controller';
import { layoutStore } from '../../layout/store';

export const RECORDS_GRID_VIEW = 'records.grid';

/** `schemaId` is a table id to edit, or `'new'` to create one. */
export function openTableSetup(schemaId: string): void {
  const instanceId = revealSection('setup', RECORDS_GRID_VIEW);
  if (instanceId) layoutStore.dispatch({ type: 'SET_PANE_PARAMS', instanceId, params: { setupSchemaId: schemaId } });
}
