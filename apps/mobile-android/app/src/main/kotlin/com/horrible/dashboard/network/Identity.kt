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
        signer = Ed25519Sign(privateKey)
        val publicKeyBytes = signer.publicKey
        publicKey = Base64.encodeToString(publicKeyBytes, Base64.NO_WRAP)
        nodeId = fingerprint(publicKey)
    }

    fun sign(payload: ByteArray): String {
        return Base64.encodeToString(signer.sign(payload), Base64.NO_WRAP)
    }

    companion object {
        private const val KEY_FILE = "network-identity.key"

        fun fingerprint(publicKeyB64: String): NodeId {
            val raw = Base64.decode(publicKeyB64, Base64.DEFAULT)
            val digest = MessageDigest.getInstance("SHA-256").digest(raw)
            return Base64.encodeToString(digest, Base64.NO_WRAP or Base64.URL_SAFE)
                .replace("=", "")
                .lowercase()
                .take(16)
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
