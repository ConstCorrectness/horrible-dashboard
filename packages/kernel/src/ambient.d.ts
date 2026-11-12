// Typechecking this package walks into @horrible/core's sources, which use Vite's
// `?raw` import suffix. Every consumer of core declares it (apps/web via
// `vite/client`, packages/ui in its vite-env.d.ts); module packages get it here.
declare module '*.css?raw' {
  const text: string;
  export default text;
}
