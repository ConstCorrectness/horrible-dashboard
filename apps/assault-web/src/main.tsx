import React from 'react';
import ReactDOM from 'react-dom/client';
import { initBackendOrigin, routeAccountToGameServer } from '@horrible/core';
import App from './App';
import { ensureCallsign } from './hooks/useGuestSession';
import { applySocketIdentity, authStore, bindSettingsToAccount, refreshAccount } from './auth';
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
bindSettingsToAccount();
// No node here to hold the token and proxy the account: the armory's calls
// (inventory, equips, wallet, trades) go to the game server's own routes with
// the token read fresh each call, so signing in or out needs no re-binding.
routeAccountToGameServer(() => authStore.getState().token);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
