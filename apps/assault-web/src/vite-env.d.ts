/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND_URL?: string;
  readonly VITE_BACKEND_WS_URL?: string;
  readonly VITE_GAME_SERVER_WS?: string;
  readonly VITE_ASSETS_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
