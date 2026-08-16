/**
 * MCP module: connect this node to third-party MCP servers.
 *
 * The human surface only. The backend (`backend/modules/mcp/`) owns the sessions and
 * projects each server into the agent as an `mcp-<id>` tool group — see
 * docs/modules/mcp.mdx.
 *
 * Note the division of labour with the chat's `/mcp` slash command: both read
 * `./api`, so the palette command opens the pane and `/mcp` prints the same summary
 * inline without a model turn. Neither reimplements the other.
 */
import { registry, type ModuleManifest } from '../../registry';
import { listServers, summarize } from './api';
import { McpServersPane } from './panels/McpServersPane';

export const mcpModule: ModuleManifest = {
  id: 'mcp',
  title: 'MCP',
  panels: [
    {
      id: 'mcp.servers',
      title: 'MCP Servers',
      component: McpServersPane,
      // A `document` that stays rail-toggleable. It was a dock-only `tool`, but
      // five sections — one of them an authoring surface — do not fit a 280px
      // rail, and everything the pane is for happens *in* it. `dockable` keeps
      // the rail glyph and the left dock available for the times you just want
      // to check a server's status beside your work.
      role: 'document',
      icon: '🔌',
      dockable: 'left',
      singleton: true,
      // Two sections, one component: finding a server and running one are the same
      // objects at two moments, and the discover half hands its result straight to
      // the servers half. Splitting them into two panes would mean two copies of the
      // config shape and a hand-off across a pane boundary.
      sections: [
        { id: 'servers', label: 'Servers', icon: '🔌', key: 's', default: true },
        { id: 'discover', label: 'Discover', icon: '🔎', key: 'd' },
        // Authoring is a third section rather than a pane of its own for the same
        // reason: a scaffolded project *is* a server, and the moment it's provisioned
        // it appears in Servers with the same inspector as any other. Splitting them
        // would mean two views of one object with a hand-off between them.
        { id: 'author', label: 'Author', icon: '✍️', key: 'a' },
      ],
    },
  ],
  settings: [
    {
      // The exported server's *degree* of disclosure. The server itself is gated by
      // HORRIBLE_ENABLE_MCP_SERVER (an env var, so it can't be flipped by anything
      // that can merely write settings); this narrower switch controls whether an
      // already-authorized caller sees prompt text or only its shape and token cost.
      key: 'mcp.server.exposeContent',
      title: 'Expose prompt content over the MCP server',
      description:
        'When the exported MCP server is enabled, include context block text in ' +
        'turn detail. Off means callers see token counts and structure but not your ' +
        'prompts, editor buffers, or tool results. Telemetry bodies and headers are ' +
        'never exported either way.',
      type: 'boolean',
      default: false,
    },
  ],
  commands: [
    {
      id: 'mcp.openServers',
      title: 'MCP: Open servers',
      run: () => registry.openPanel('mcp.servers'),
    },
    {
      id: 'mcp.addServer',
      title: 'MCP: Add server',
      // The form lives in the pane; the command is the discoverable entry point to it.
      run: () => registry.openPanel('mcp.servers'),
    },
    {
      id: 'mcp.discover',
      title: 'MCP: Discover servers',
      // Opens the pane, then switches it. `section.show:mcp.servers:discover` is
      // synthesized by the registry and only reveals a section of an *open* pane, so
      // the discoverable entry point has to do both.
      run: () => {
        registry.openPanel('mcp.servers');
        registry.runCommand('section.show:mcp.servers:discover');
      },
    },
    {
      id: 'mcp.author',
      title: 'MCP: Author a server',
      run: () => {
        registry.openPanel('mcp.servers');
        registry.runCommand('section.show:mcp.servers:author');
      },
    },
    {
      id: 'mcp.status',
      title: 'MCP: Show server status',
      run: async () => {
        const { servers } = await listServers();
        return summarize(servers);
      },
    },
  ],
};

export { listServers, summarize } from './api';
export type { McpServer, McpServerInput, McpState, McpTool, McpTransport } from './api';
