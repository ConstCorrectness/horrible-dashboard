/**
 * Web search module: pluggable engines, an AI layer on top, and this node's own
 * crawled index. See docs/modules/search.mdx.
 *
 * The agent tools live on the **backend** (`backend/modules/search/agent_tools.py`)
 * rather than being declared here, because searching must work with no browser tab
 * attached — a deep-research run outlives the window that started it. What this
 * module contributes is the settings surface and a panel that answers "why did my
 * search behave like that": which engines ran, and what the local index holds.
 *
 * API keys are deliberately absent from these settings. `GET /api/settings` hands
 * the whole bag to the browser, so keys live in the Web Search connector's encrypted
 * store instead. A provider's *name* is public, which is why `search.provider` is
 * an ordinary setting.
 */
import { registry, type ModuleManifest } from '../../registry';
import { SearchPanel } from './SearchPanel';

export const searchModule: ModuleManifest = {
  id: 'search',
  title: 'Web Search',
  widgets: [
    {
      id: 'search.panel',
      title: 'Web Search',
      component: SearchPanel,
      role: 'tool',
      icon: '🔎',
    },
  ],
  commands: [
    {
      id: 'search.openPanel',
      title: 'Search: Open engines & index panel',
      run: () => registry.openPanel('search.panel'),
    },
  ],
  settings: [
    {
      key: 'search.provider',
      title: 'Search engine',
      description:
        'Which engine to search with. “auto” fans out across every configured engine at once and fuses the rankings, which is almost always better than any single one. Keys are added in the Web Search connector on the home page.',
      type: 'enum',
      enumValues: ['auto', 'tavily', 'brave', 'exa', 'serper', 'searxng', 'crawl', 'ddg'],
      default: 'auto',
    },
    {
      key: 'search.fanoutProviders',
      title: 'Fan-out engines',
      description:
        'Comma-separated engine ids to query when the engine is “auto”. Empty means every configured engine.',
      type: 'string',
      default: '',
    },
    {
      key: 'search.searxngUrl',
      title: 'SearXNG instance URL',
      description:
        'Base URL of a self-hosted SearXNG (e.g. http://localhost:8888) — keyless, private, no per-query cost. Run one with: docker run -d -p 8888:8080 searxng/searxng. You must also add “json” to search.formats in its settings.yml, or it answers HTML and returns nothing. SearXNG is AGPL, so it is never bundled — running it is your call.',
      type: 'string',
      default: '',
    },
    {
      key: 'search.fanout',
      title: 'Query rewrites',
      description:
        'How many differently-worded versions of a query the deep search sends (1 disables rewriting). Each rewrite multiplies the calls made to metered engines.',
      type: 'number',
      default: 3,
    },
    {
      key: 'search.rewriteModel',
      title: 'Rewrite model',
      description:
        'Model used to reword queries for deep search. Empty uses the agent’s configured model.',
      type: 'string',
      default: '',
    },
    {
      key: 'search.concurrency',
      title: 'Search concurrency',
      description: 'How many engine calls run at once across the fan-out.',
      type: 'number',
      default: 6,
    },
    {
      key: 'search.cacheTtlMinutes',
      title: 'Result cache (minutes)',
      description:
        'How long an engine’s answer to a given query is reused. This is what makes query fan-out and overlapping research subagents cheap. 0 disables caching.',
      type: 'number',
      default: 60,
    },
    {
      key: 'search.pageCacheHours',
      title: 'Page cache (hours)',
      description:
        'How long extracted page text is reused before re-fetching. Stops several research subagents each re-downloading the same article. 0 disables it.',
      type: 'number',
      default: 24,
    },
    {
      key: 'search.crawlDelaySeconds',
      title: 'Crawl delay (seconds)',
      description:
        'Minimum gap between requests to the same host while crawling. A site’s own robots.txt Crawl-delay can raise this but never lower it.',
      type: 'number',
      default: 1,
    },
    {
      key: 'search.crawlConcurrency',
      title: 'Crawl concurrency',
      description: 'How many hosts are crawled at once (one request per host at a time).',
      type: 'number',
      default: 4,
    },
  ],
};
