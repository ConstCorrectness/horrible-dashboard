import type { ModuleManifest } from '../../registry';
import { WriterEditor } from './WriterEditor';
import { PublishTool } from './PublishTool';

export const writerModule: ModuleManifest = {
  id: 'writer',
  title: 'Writer',
  panels: [
    {
      id: 'writer.editor',
      title: 'Writer Workspace',
      component: WriterEditor,
      role: 'document',
      icon: '📝',
    },
    {
      id: 'writer.publish',
      title: 'Publish Integrations',
      component: PublishTool,
      role: 'tool',
      icon: '🚀',
    },
  ],
};
