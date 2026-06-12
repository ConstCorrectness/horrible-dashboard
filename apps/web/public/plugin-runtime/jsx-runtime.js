// Host-provided react/jsx-runtime shim for plugin bundles — see react.js.
const runtime = window.__HORRIBLE_RUNTIME__.jsxRuntime;

export const Fragment = runtime.Fragment;
export const jsx = runtime.jsx;
export const jsxs = runtime.jsxs;
