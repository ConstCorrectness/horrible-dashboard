import React from 'react';
import ReactDOM from 'react-dom/client';
import { initBackendOrigin } from '@horrible/core';
import App from './App';
import { ensureCallsign } from './hooks/useGuestSession';
import { applySocketIdentity, refreshAccount } from './auth';
import './theme.css';
import './landing.css';

// If a backend origin is configured in the environment (e.g. VITE_BACKEND_URL=https://horrible-games.fly.dev),
// initialize it so all API and WebSocket requests target the central game server.
const customOrigin = import.meta.env.VITE_BACKEND_URL || null;
if (customOrigin) {
  initBackendOrigin(customOrigin);
}

// The match socket: as the guest callsign until a stored token has been checked
// (and has a username), then as the account. `applySocketIdentity` re-runs from
// App whenever either changes, so this is only the first value.
applySocketIdentity(ensureCallsign());
void refreshAccount();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
