import { generateKeyPairSync, sign } from 'node:crypto';
import { describe, expect, it } from 'vitest';

import { parsePublicKey, verifySignature } from '../src/minisign';
import { UPDATER_PUBKEY } from '../src/updater-key';

// Made with the real tool CI signs with — `tauri signer generate` / `tauri signer
// sign` (@tauri-apps/cli 2.9.6) — over PAYLOAD, with a throwaway key. Keeping a
// vector from the tool itself is the point: a hand-rolled signer in this test would
// only prove the verifier agrees with itself.
const PAYLOAD = Buffer.from('horrible-dashboard installer bytes\n');
const TAURI_PUBKEY =
  'dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDAwNDM5QjJCOTFBNjRCNkEKUldScVM2YVJLNXREQU1GTEdhY3NoVEVXWTVzcXpZK25Ienp3SnBrRTM1U2RWa3hwRkNnNUtRTHcK';
const TAURI_SIGNATURE =
  'dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIHRhdXJpIHNlY3JldCBrZXkKUlVScVM2YVJLNXREQUFUTjMzSkVmOE9CdlVEdSswbCtiY1BpZjJtU3FmdGJ3MDR6MWZ6MllORFYxcWYwUEhsTVdiSWlNUkVJVEpmU05jRWovSm9BTm8rd2VXdFR1T2tCNXdvPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDoxNzkzMjA0OTU5CWZpbGU6cGF5bG9hZC5iaW4KaUp4Yk4wUnA4QjdidC9lV01PVyttYkc2MDFMMkdZQUh2cE1oRVRJNFhOM2F1dGxJamkvMEpGb2F3ODhiZjhDeU5ONXpvMWJyQ2NZSVVITmxLR0tGQ3c9PQo=';

const decoded = (b64: string) => Buffer.from(b64, 'base64').toString('utf8');

describe('minisign', () => {
  const key = parsePublicKey(TAURI_PUBKEY);

  it('accepts a signature made by `tauri signer sign`', () => {
    expect(() => verifySignature(key, TAURI_SIGNATURE, PAYLOAD)).not.toThrow();
  });

  it('accepts the unwrapped minisign text as well as the base64-wrapped file', () => {
    expect(() =>
      verifySignature(parsePublicKey(decoded(TAURI_PUBKEY)), decoded(TAURI_SIGNATURE), PAYLOAD),
    ).not.toThrow();
  });

  it('refuses a tampered file', () => {
    const tampered = Buffer.from(PAYLOAD);
    tampered[0] ^= 1;
    expect(() => verifySignature(key, TAURI_SIGNATURE, tampered)).toThrow(
      /does not match the file/,
    );
  });

  it('refuses a tampered trusted comment', () => {
    const text = decoded(TAURI_SIGNATURE).replace('file:payload.bin', 'file:evil.bin');
    expect(() => verifySignature(key, text, PAYLOAD)).toThrow(/trusted comment/);
  });

  it('refuses a signature from another key', () => {
    // The release key: same format, different key id.
    expect(() => verifySignature(parsePublicKey(UPDATER_PUBKEY), TAURI_SIGNATURE, PAYLOAD)).toThrow(
      /different key/,
    );
  });

  it('refuses a forged signature carrying the trusted key id', () => {
    // An attacker can copy the key id; they cannot produce the Ed25519 signature.
    const { privateKey } = generateKeyPairSync('ed25519');
    const lines = decoded(TAURI_SIGNATURE).split('\n');
    const sigLine = Buffer.from(lines[1]!, 'base64');
    const forged = sign(null, PAYLOAD, privateKey);
    forged.copy(sigLine, 10);
    lines[1] = sigLine.toString('base64');
    expect(() => verifySignature(key, lines.join('\n'), PAYLOAD)).toThrow(/does not match/);
  });

  it('parses the release key the app ships with', () => {
    expect(parsePublicKey(UPDATER_PUBKEY).keyId.length).toBe(8);
  });

  it('rejects garbage', () => {
    expect(() => parsePublicKey('not a key')).toThrow();
    expect(() => verifySignature(key, 'nope', PAYLOAD)).toThrow();
  });
});
