package com.horrible.dashboard.network

import android.content.Context
import android.util.Base64
import com.google.crypto.tink.subtle.Ed25519Sign
import com.google.crypto.tink.subtle.Ed25519Verify
import java.io.File
import java.security.MessageDigest
import java.security.SecureRandom

typealias NodeId = String

class Identity(private val privateKey: ByteArray) {
    val publicKey: String
    val nodeId: NodeId
    private val signer: Ed25519Sign

    init {
        // Tink's Ed25519Sign constructor takes the private key seed (32 bytes)
        // We can get the public key from the signer itself using reflections or by computing it.
        // For simplicity and since we are in a dev environment, let's use a known way.
        signer = Ed25519Sign(privateKey)
        
        // Use reflection to access the public key if needed, or just recompute it.
        // Actually, Ed25519Sign stores it in a private field `publicKey`.
        val field = Ed25519Sign::class.java.getDeclaredField("publicKey")
        field.isAccessible = true
        val publicKeyBytes = field.get(signer) as ByteArray
        
        publicKey = Base64.encodeToString(publicKeyBytes, Base64.NO_WRAP)
        nodeId = fingerprint(publicKey)
    }

    fun sign(payload: ByteArray): String {
        return Base64.encodeToString(signer.sign(payload), Base64.NO_WRAP)
    }

    companion object {
        private const val KEY_FILE = "network-identity.key"

        /** RFC 4648 base32 alphabet — the one Python's `base64.b32encode` uses. */
        private const val BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

        /**
         * The node id derived from a base64 raw Ed25519 public key.
         *
         * Must stay byte-identical to `identity.fingerprint` in
         * `backend/modules/network/identity.py`:
         * `base32(sha256(pubkey))` unpadded, lowercased, first 16 chars. The node id
         * is self-certifying — the desktop recomputes it from the key we present and
         * drops the handshake if it doesn't equal our `src` — so a different alphabet
         * here (base64url was the earlier bug) closes the socket before auth.
         */
        fun fingerprint(publicKeyB64: String): NodeId {
            val raw = Base64.decode(publicKeyB64, Base64.DEFAULT)
            val digest = MessageDigest.getInstance("SHA-256").digest(raw)
            return base32(digest).lowercase().take(16)
        }

        /** Unpadded RFC 4648 base32 — equivalent to `b32encode(...).rstrip("=")`. */
        private fun base32(data: ByteArray): String {
            val out = StringBuilder()
            var buffer = 0
            var bits = 0
            for (byte in data) {
                buffer = (buffer shl 8) or (byte.toInt() and 0xFF)
                bits += 8
                while (bits >= 5) {
                    out.append(BASE32_ALPHABET[(buffer shr (bits - 5)) and 0x1F])
                    bits -= 5
                }
            }
            if (bits > 0) out.append(BASE32_ALPHABET[(buffer shl (5 - bits)) and 0x1F])
            return out.toString()
        }

        fun loadOrCreate(context: Context): Identity {
            val file = File(context.filesDir, KEY_FILE)
            if (file.exists()) {
                return Identity(file.readBytes())
            }
            // Generate Ed25519 private key (32 bytes seed)
            val privateKey = ByteArray(32)
            SecureRandom().nextBytes(privateKey)
            file.writeBytes(privateKey)
            return Identity(privateKey)
        }

        fun verify(publicKeyB64: String, payload: ByteArray, signatureB64: String): Boolean {
            return try {
                val publicKey = Base64.decode(publicKeyB64, Base64.DEFAULT)
                val signature = Base64.decode(signatureB64, Base64.DEFAULT)
                val verifier = Ed25519Verify(publicKey)
                verifier.verify(signature, payload)
                true
            } catch (e: Exception) {
                false
            }
        }
    }
}
