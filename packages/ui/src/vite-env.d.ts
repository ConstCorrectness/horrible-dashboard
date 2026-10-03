// Minimal ambient typing for Vite's import.meta.env, so this library package can
// read build-time flags without depending on Vite's full client types.
interface ImportMetaEnv {
  readonly DEV: boolean;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Vite's `?raw` imports — core's Scrive site build inlines stylesheets this way, and
// this package typechecks core's source.
declare module '*.css?raw' {
  const text: string;
  export default text;
}
