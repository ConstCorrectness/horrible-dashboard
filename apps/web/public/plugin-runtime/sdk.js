// Host-provided @horrible/sdk runtime shim for plugin bundles — see react.js.
// The SDK is mostly types (erased at build time); the runtime surface is tiny.
const sdk = window.__HORRIBLE_RUNTIME__.sdk;

export const SDK_API_VERSION = sdk.SDK_API_VERSION;
export const definePlugin = sdk.definePlugin;
