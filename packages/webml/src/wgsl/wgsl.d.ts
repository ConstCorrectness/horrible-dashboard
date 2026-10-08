/** WGSL sources are imported as strings (Vite's `?raw`). */
declare module '*.wgsl?raw' {
  const source: string;
  export default source;
}
