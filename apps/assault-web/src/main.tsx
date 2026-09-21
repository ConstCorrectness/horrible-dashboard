import React from 'react';
import ReactDOM from 'react-dom/client';
import { initBackendOrigin, setWsPath } from '@horrible/core';
import App from './App';

// If a backend origin is configured in the environment (e.g. VITE_BACKEND_URL=https://horrible-games.fly.dev),
// initialize it so all API and WebSocket requests target the central game server.
const customOrigin = import.meta.env.VITE_BACKEND_URL || null;
if (customOrigin) {
  initBackendOrigin(customOrigin);
}

// In standalone web mode, point default WebSocket connections to the game server /hassault-ws endpoint
const guestName = localStorage.getItem('hassault_guest_callsign') || '';
setWsPath(`/hassault-ws?guest=1&name=${encodeURIComponent(guestName)}`);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
