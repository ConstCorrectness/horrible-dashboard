import { defineConfig } from 'vite';

import { horriblePluginViteConfig } from '@horribledashboard/sdk/vite';

export default defineConfig(horriblePluginViteConfig({ entry: 'src/index.tsx' }));
