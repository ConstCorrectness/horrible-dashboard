/**
 * Records module: user-defined tables — papers to read, job applications, contacts,
 * anything row-shaped — as views of one store (rows, review, board) plus a rail and
 * a table designer. The rows are real SQLite tables in the app database, so
 * everything here is also visible from the database console's `app` connection.
 *
 * The **Review** pane is the point of the module, not the grid: an agent reading a
 * document proposes field values with citations, and you accept them one by one.
 * That is why the review surface is docked into the workspaces where reading
 * actually happens (Research, Web Ops, Data Entry) rather than living in a
 * records-only workspace of its own. See docs/modules/records.mdx.
 */
import { registry, type ModuleManifest } from '../../registry';
import { seedSchemas } from './api';
import { RecordBoard } from './RecordBoard';
import { RecordForm } from './RecordForm';
import { RecordGrid } from './RecordGrid';
import { RecordList } from './RecordList';
import { refreshSchemas } from './store';
import { TableSetup } from './TableSetup';

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
      // "Rows" rather than "Records": the module, the pane and the row were all
      // called some inflection of "record", which said nothing about any of them.
      title: 'Rows',
      component: RecordGrid,
      role: 'document',
      icon: '▤',
    },
    {
      id: 'records.form',
      // Named for its actual job. It is the proposal-review surface first and a
      // data-entry form second — see the propose/approve loop in the docs.
      title: 'Review',
      component: RecordForm,
      role: 'document',
      icon: '📋',
    },
    {
      id: 'records.schema',
      title: 'Table setup',
      component: TableSetup,
      role: 'document',
      icon: '⚙',
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
      // A section of Explorer now — see modules/explorer.
      embedded: true,
    },
  ],
  explorerSources: [{ id: 'tables', label: 'Tables', icon: '🗃', view: 'records.list', key: 'r' }],
  // One workspace, not two. The old `crm` preset arranged a contacts/deals pipeline
  // that presumed a sales workflow the substrate never actually required — and it
  // was the reason a generic table store read as CRM software. The review surface
  // now docks into the workspaces where the reading happens instead (Research and
  // Web Ops, in modules/layouts), so an extraction is reviewed beside the document
  // it came from rather than a workspace switch away.
  frames: [
    {
      id: 'intake',
      name: 'Data Entry',
      icon: '📥',
      // Source left, review right, half and half: the whole workflow is reading one
      // and confirming the other, and neither is secondary.
      agent: 'intake',
      frame: {
        center: {
          split: 'row',
          sizes: [0.5, 0.5],
          children: [
            { tabs: ['research.pdfViewer', 'browser.view'], active: 0 },
            { tabs: ['records.form', 'records.grid'], active: 0 },
          ],
        },
        docks: {
          left: { tools: ['explorer.home'], size: 240 },
          right: { tools: ['agent.chat'], size: 360 },
          bottom: { tools: ['observability.io'], visible: false },
        },
      },
    },
  ],
  commands: [
    {
      id: 'records.open',
      title: 'Records: Open table rows',
      run: () => registry.openPanel('records.grid'),
    },
    {
      id: 'records.openReview',
      title: 'Records: Open review',
      run: () => registry.openPanel('records.form'),
    },
    {
      id: 'records.openBoard',
      title: 'Records: Open board',
      run: () => registry.openPanel('records.board'),
    },
    {
      id: 'records.newTable',
      title: 'Records: New table…',
      run: () => registry.openPanel('records.schema', { params: { schemaId: 'new' } }),
    },
    {
      id: 'records.seed',
      title: 'Records: Create the starter tables (contacts, deals, activities, intake)',
      run: () => void ensureSeeded(),
    },
  ],
};

export { ensureSeeded };
