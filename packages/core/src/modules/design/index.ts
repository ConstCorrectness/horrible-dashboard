import type { ModuleManifest } from '../../registry';
import { DesignCanvas } from './DesignCanvas';

export const designModule: ModuleManifest = {
  id: 'design',
  title: 'Design Canvas',
  panels: [
    {
      id: 'design.canvas',
      title: 'Design Canvas',
      component: DesignCanvas,
      role: 'document',
      icon: '🎨',
    },
  ],
};
