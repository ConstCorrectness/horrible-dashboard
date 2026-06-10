import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agentModule,
  BROWSER_CAPABILITIES,
  dashboardModule,
  initCapabilities,
  registry,
} from '@horrible/core';
import { AppShell } from '@horrible/ui';

// Browser layout entry: browser capability set, then module registration.
initCapabilities(BROWSER_CAPABILITIES);
registry.register(dashboardModule);
registry.register(agentModule);

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root element');

createRoot(root).render(
  <StrictMode>
    <AppShell appTitle="horrible-dashboard" />
  </StrictMode>,
);
