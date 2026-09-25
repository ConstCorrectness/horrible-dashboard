import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const MarketplacePanel = lazyPane(() => import('./MarketplacePanel'), 'MarketplacePanel');

/**
 * Browse, install, and manage plugins built against @horribledashboard/sdk.
 * See docs/modules/marketplace.md and docs/architecture/plugin-sdk.md.
 */
export const marketplaceModule: ModuleManifest = {
  id: 'marketplace',
  title: 'Marketplace',
  panels: [
    {
      id: 'marketplace.home',
      title: 'Marketplace',
      component: MarketplacePanel,
      role: 'document',
      icon: '🛍',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'marketplace.open',
      title: 'Marketplace: Open',
      run: () => registry.openPanel('marketplace.home'),
    },
  ],
};
