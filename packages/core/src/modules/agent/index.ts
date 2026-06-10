import { registry, type ModuleManifest } from '../../registry';

/** See docs/modules/agent-chat.md. First slice: onboarding + one-shot ask on the home view. */
export const agentModule: ModuleManifest = {
  id: 'agent',
  title: 'Agent',
  commands: [
    {
      id: 'agent.home',
      title: 'Agent: Ask your dashboard friend',
      run: () => registry.openView('home'),
    },
  ],
};

export * from './api';
