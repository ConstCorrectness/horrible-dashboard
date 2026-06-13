import { type ModuleManifest } from '../../registry';

/** See docs/modules/agent-chat.md. First slice: onboarding + one-shot ask on the home view. */
// No commands yet: the home view (where the ask bar lives) is reached via the
// shell's own `shell.home` command.
export const agentModule: ModuleManifest = {
  id: 'agent',
  title: 'Agent',
};

export * from './api';
