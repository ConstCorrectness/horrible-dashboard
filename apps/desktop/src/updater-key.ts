/**
 * The public half of the release-signing key, as `tauri signer generate` prints it
 * (a base64-wrapped minisign public key). The private half is the
 * `TAURI_SIGNING_PRIVATE_KEY` repo secret — see docs/architecture/releases.mdx.
 */
export const UPDATER_PUBKEY =
  'dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IERDRDRBNDQ2REQzMzVEQkQKUldTOVhUUGRScVRVM0JwMkJKUUllaG42T0w4a25KdUo3VzBrWWNCc2pkRXpsMG41MHZTZkJPSzYK';
