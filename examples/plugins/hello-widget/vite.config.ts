import { defineConfig } from 'vite';

import { horriblePluginViteConfig } from '@horrible/sdk/vite';

export default defineConfig(horriblePluginViteConfig({ entry: 'src/index.tsx' }));
