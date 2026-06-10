// Minimal ambient typing for Vite's import.meta.env, so this library package can
// read build-time flags without depending on Vite's full client types.
interface ImportMetaEnv {
  readonly DEV: boolean;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
