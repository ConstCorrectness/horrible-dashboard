// Vite's `?raw` suffix: the file's text as a string. The static site build inlines
// stylesheets into the published `_scrive/site.css` this way. (apps/web gets the same
// from `vite/client`; packages/ui declares it in its vite-env.d.ts.)
declare module '*.css?raw' {
  const text: string;
  export default text;
}
