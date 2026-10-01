import { dialogs } from '../../dialogs';
import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';
import { SECTIONS } from './format';
import { getState, searchFor } from './store';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const DiscoverPane = lazyPane(() => import('./panels/DiscoverPane'), 'DiscoverPane');

const PANE = 'discover.browse';

/** Rail/section glyphs. Emoji are the rail's convention (see CLAUDE.local.md §1);
 *  nothing inside the pane uses them. */
const SECTION_META: Record<string, { icon: string; key: string }> = {
  models: { icon: '🧠', key: 'm' },
  datasets: { icon: '🗃', key: 'd' },
  spaces: { icon: '🛰', key: 's' },
  papers: { icon: '📰', key: 'p' },
  arxiv: { icon: '📄', key: 'a' },
  repos: { icon: '🐙', key: 'g' },
  kaggle: { icon: '🏁', key: 'k' },
  mcp: { icon: '🔌', key: 'c' },
  plugins: { icon: '🧩', key: 'u' },
  skills: { icon: '🛠', key: 'l' },
  docs: { icon: '📚', key: 'o' },
};

/** Open Discover on one section. The registry synthesizes `section.show:*` but it
 *  only switches a pane that's already open, so this does both. */
export function openDiscover(section?: string): void {
  registry.openPanel(PANE);
  if (section) void registry.runCommand(`section.show:${PANE}:${section}`);
}

/**
 * Discover: one browse surface over every external catalog the app works with —
 * the Hugging Face Hub, HF Daily Papers, arXiv, GitHub, Kaggle, the MCP registry,
 * plugins, agent skills and documentation.
 *
 * Frontend half of `backend/modules/discover`. Each section opens on a real,
 * labelled default feed instead of an empty search box. See docs/modules/discover.mdx.
 */
export const discoverModule: ModuleManifest = {
  id: 'discover',
  title: 'Discover',
  category: 'research',
  panels: [
    {
      id: PANE,
      title: 'Discover',
      component: DiscoverPane,
      // A browser you work in, tabbed beside other documents — not a glanceable widget.
      role: 'document',
      icon: '🧭',
      singleton: true,
      sections: SECTIONS.map((s, i) => ({
        id: s.id,
        label: s.label,
        icon: SECTION_META[s.id]?.icon,
        key: SECTION_META[s.id]?.key,
        default: i === 0,
      })),
    },
  ],
  commands: [
    {
      id: 'discover.open',
      title: 'Discover: Browse models, datasets, papers, repos and more',
      run: () => openDiscover(),
    },
    ...SECTIONS.map((s) => ({
      id: `discover.open.${s.id}`,
      title: `Discover: ${s.label}`,
      run: () => openDiscover(s.id),
    })),
    {
      id: 'discover.search',
      title: 'Discover: Search…',
      run: async () => {
        const choice = await dialogs.prompt({
          title: 'Search Discover',
          placeholder: 'models: qwen · repos: vllm · arxiv: "speculative decoding" · docs: Counter',
          confirmLabel: 'Search',
        });
        const raw = choice?.trim();
        if (!raw) return;
        // `section: query`, or a bare query for the Models section.
        const match = /^([a-z]+):\s*(.+)$/i.exec(raw);
        const section = SECTIONS.find((s) => s.id === match?.[1]?.toLowerCase()) ?? SECTIONS[0];
        const query = match && section.id === match[1].toLowerCase() ? match[2] : raw;
        const kind = getState().kinds[section.id] ?? section.kinds[0];
        openDiscover(section.id);
        searchFor(section.source, kind, query);
      },
    },
  ],
};
