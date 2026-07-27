/**
 * Records module: user-defined tables — CRM contacts and deals, intake forms,
 * anything row-shaped — as three views of one store (grid, form, board) plus a
 * rail. The rows are real SQLite tables in the app database, so everything here is
 * also visible from the database console's `app` connection.
 *
 * The two workspaces it owns are the point: **CRM** is the pipeline, and **Data
 * Entry** is a source document beside a form the agent fills by *proposing* values
 * you accept field by field. See docs/modules/records.mdx.
 */
import { registry, type ModuleManifest } from '../../registry';
import { seedSchemas } from './api';
import { RecordBoard } from './RecordBoard';
import { RecordForm } from './RecordForm';
import { RecordGrid } from './RecordGrid';
import { RecordList } from './RecordList';
import { refreshSchemas } from './store';

/** Create the built-in tables if they're missing, then reload the rail. Called
 * when a records workspace opens — idempotent, and never touches an existing
 * schema, so a user who reshaped `deals` keeps their version. */
async function ensureSeeded(): Promise<void> {
  try {
    await seedSchemas();
  } catch {
    /* backend down — the panes show their empty state and this retries on reopen */
  }
  await refreshSchemas();
}

export const recordsModule: ModuleManifest = {
  id: 'records',
  title: 'Records',
  panels: [
    {
      id: 'records.grid',
      title: 'Records',
      component: RecordGrid,
      role: 'document',
      icon: '▤',
    },
    {
      id: 'records.form',
      title: 'Record',
      component: RecordForm,
      role: 'document',
      icon: '📋',
    },
    {
      id: 'records.board',
      title: 'Board',
      component: RecordBoard,
      role: 'document',
      icon: '🗂',
    },
    {
      id: 'records.list',
      title: 'Tables',
      component: RecordList,
      role: 'tool',
      icon: '🗃',
      defaultDock: 'left',
      defaultDockSize: 260,
      singleton: true,
    },
  ],
  frames: [
    {
      id: 'crm',
      name: 'CRM',
      icon: '👥',
      // Pipeline first (the board is how you think about deals), the open record
      // beside it, and the activity log under that — logging a call shouldn't cost
      // a navigation. The CRM agent docks right to enrich what's selected.
      agent: 'crm',
      frame: {
        center: {
          split: 'row',
          sizes: [0.6, 0.4],
          children: [
            { tabs: ['records.board', 'records.grid'], active: 0 },
            {
              split: 'column',
              sizes: [0.55, 0.45],
              children: [
                { pane: 'records.form' },
                { pane: 'records.grid', params: { schemaId: 'activities' } },
              ],
            },
          ],
        },
        docks: {
          left: { tools: ['records.list'], size: 260 },
          right: { tools: ['agent.chat'], size: 380 },
        },
      },
    },
    {
      id: 'intake',
      name: 'Data Entry',
      icon: '📥',
      // Source left, form right, half and half: the whole workflow is reading one
      // and confirming the other, and neither is secondary.
      agent: 'intake',
      frame: {
        center: {
          split: 'row',
          sizes: [0.5, 0.5],
          children: [
            { tabs: ['research.pdfViewer', 'browser.view'], active: 0 },
            { pane: 'records.form' },
          ],
        },
        docks: {
          left: { tools: ['records.list'], size: 240 },
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], visible: false },
        },
      },
    },
  ],
  commands: [
    {
      id: 'records.open',
      title: 'Records: Open table grid',
      run: () => registry.openPanel('records.grid'),
    },
    {
      id: 'records.openBoard',
      title: 'Records: Open board',
      run: () => registry.openPanel('records.board'),
    },
    {
      id: 'records.seed',
      title: 'Records: Create the built-in tables (contacts, deals, activities, intake)',
      run: () => void ensureSeeded(),
    },
  ],
};

export { ensureSeeded };
