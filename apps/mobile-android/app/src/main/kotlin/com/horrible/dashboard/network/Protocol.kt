package com.horrible.dashboard.network

import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import org.json.JSONObject

/**
 * Peer-wire serialization + signing. Mirrors `backend/modules/network/protocol.py`.
 *
 * The signature covers the **authenticated fields only** — `sig`, `dst` and `ttl`
 * are excluded because a relay may legitimately rewrite the latter two in flight.
 * Canonicalization runs over the *raw wire JSON* rather than the parsed
 * [PeerEnvelope]: Moshi widens every JSON number to `Double`, so round-tripping an
 * inbound envelope through Kotlin objects would turn the desktop's `"v":1` into
 * `1.0` and fail every verification. See [CanonicalJson].
 */
object Protocol {
    private val moshi = Moshi.Builder()
        .addLast(KotlinJsonAdapterFactory())
        .build()
    private val envelopeAdapter = moshi.adapter(PeerEnvelope::class.java)

    // Message types — keep in sync with backend/modules/network/protocol.py.
    const val HELLO = "hello"
    const val HELLO_ACK = "hello_ack"
    const val AUTH = "auth"
    const val AUTH_RESULT = "auth_result"
    const val PRESENCE = "presence"
    const val PING = "ping"
    const val PONG = "pong"
    const val AGENT_REQUEST = "agent_request"
    const val AGENT_STREAM = "agent_stream"
    const val AGENT_RESULT = "agent_result"
    const val AGENT_CANCEL = "agent_cancel"
    const val PEER_CHAT = "peer_chat"
    const val REMOTE_COMMAND = "remote_command"
    const val VIEW_REQUEST = "view_request"
    const val VIEW_ACCEPT = "view_accept"
    const val VIEW_FRAME = "view_frame"
    const val ERROR = "error"

    fun encode(env: PeerEnvelope): String = envelopeAdapter.toJson(env)

    fun decode(raw: String): PeerEnvelope? = envelopeAdapter.fromJson(raw)

    /**
     * The bytes a signature covers, for an envelope in its raw wire form.
     *
     * Moshi omits null fields, but Python's `model_dump` always emits `re` (as
     * `null`) — so it is added back when absent. Everything else follows from
     * [CanonicalJson] reproducing `json.dumps(sort_keys=True, separators=(",",":"))`.
     */
    fun canonicalBytes(rawJson: String): ByteArray {
        val obj = JSONObject(rawJson)
        obj.remove("sig")
        obj.remove("dst")
        obj.remove("ttl")
        if (!obj.has("re")) obj.put("re", JSONObject.NULL)
        return CanonicalJson.dumps(obj).toByteArray(Charsets.UTF_8)
    }

    fun signEnvelope(env: PeerEnvelope, identity: Identity): PeerEnvelope {
        val unsigned = if (env.sig == null) env else env.copy(sig = null)
        val sig = identity.sign(canonicalBytes(encode(unsigned)))
        return env.copy(sig = sig)
    }

    /** Whether `rawJson`'s `sig` is a valid signature over it by `publicKeyB64`. */
    fun verifyEnvelope(rawJson: String, publicKeyB64: String): Boolean {
        return try {
            val sig = JSONObject(rawJson).optString("sig", "")
            if (sig.isEmpty()) false
            else Identity.verify(publicKeyB64, canonicalBytes(rawJson), sig)
        } catch (e: Exception) {
            false
        }
    }
}
