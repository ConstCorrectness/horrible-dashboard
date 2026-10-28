/**
 * Minisign signature verification — the update's whole security model.
 *
 * Updates are fetched over HTTPS from GitHub Releases, but HTTPS only says the bytes
 * came from that host: anyone who can publish a release, or compromise the account,
 * can serve an installer. A signature made with the release key (the private half
 * lives only in the `TAURI_SIGNING_PRIVATE_KEY` repo secret) is what makes an update
 * unforgeable, so updater.ts refuses to install anything this does not accept. There
 * is deliberately no "skip verification" path.
 *
 * The key and signatures are the ones the Tauri shell already used — `tauri signer`
 * writes standard minisign files and base64-wraps them — so installs that update
 * from the old shell and the new one verify against one identity. Both encodings are
 * accepted here: the base64-wrapped file Tauri hands out, or the minisign text itself.
 *
 * Format (https://jedisct1.github.io/minisign/):
 *   public key line: "Ed" || key id (8) || Ed25519 public key (32)
 *   signature line:  "Ed" | "ED" || key id (8) || signature (64)
 *     ("ED" signs the BLAKE2b-512 of the file, "Ed" the file itself)
 *   global signature: over signature (64) || the trusted comment's text
 */
import { createHash, createPublicKey, verify as edVerify, type KeyObject } from 'node:crypto';

export interface PublicKey {
  keyId: Buffer;
  key: KeyObject;
}

/** Undo Tauri's base64 wrapping if present; leave minisign text as it is. */
function unwrap(text: string): string {
  const trimmed = text.trim();
  if (trimmed.startsWith('untrusted comment:')) return trimmed;
  const decoded = Buffer.from(trimmed, 'base64').toString('utf8');
  if (!decoded.startsWith('untrusted comment:')) {
    throw new Error('not a minisign file (no untrusted comment)');
  }
  return decoded;
}

function lines(text: string): string[] {
  return unwrap(text)
    .split(/\r?\n/)
    .filter((l) => l.length > 0);
}

export function parsePublicKey(text: string): PublicKey {
  const raw = Buffer.from(lines(text)[1] ?? '', 'base64');
  if (raw.length !== 42 || raw.subarray(0, 2).toString('latin1') !== 'Ed') {
    throw new Error('malformed minisign public key');
  }
  const key = createPublicKey({
    key: { kty: 'OKP', crv: 'Ed25519', x: raw.subarray(10).toString('base64url') },
    format: 'jwk',
  });
  return { keyId: raw.subarray(2, 10), key };
}

/**
 * Verify `data` against a minisign signature made by `publicKey`. Throws with the
 * reason on any mismatch — wrong key, tampered file, tampered trusted comment.
 */
export function verifySignature(publicKey: PublicKey, signatureText: string, data: Buffer): void {
  const [, sigLine, trustedLine, globalLine] = lines(signatureText);
  if (!sigLine || !trustedLine || !globalLine) throw new Error('malformed minisign signature');
  const sig = Buffer.from(sigLine, 'base64');
  if (sig.length !== 74) throw new Error('malformed minisign signature line');
  const algorithm = sig.subarray(0, 2).toString('latin1');
  if (algorithm !== 'Ed' && algorithm !== 'ED') {
    throw new Error(`unsupported minisign algorithm ${JSON.stringify(algorithm)}`);
  }
  if (!sig.subarray(2, 10).equals(publicKey.keyId)) {
    throw new Error('signed with a different key than this build trusts');
  }
  const signature = sig.subarray(10);
  const message = algorithm === 'ED' ? createHash('blake2b512').update(data).digest() : data;
  if (!edVerify(null, message, publicKey.key, signature)) {
    throw new Error('signature does not match the file');
  }

  const prefix = 'trusted comment: ';
  if (!trustedLine.startsWith(prefix)) throw new Error('malformed minisign trusted comment');
  const trusted = Buffer.from(trustedLine.slice(prefix.length), 'utf8');
  const globalSig = Buffer.from(globalLine, 'base64');
  if (!edVerify(null, Buffer.concat([signature, trusted]), publicKey.key, globalSig)) {
    throw new Error('trusted comment signature does not match');
  }
}
