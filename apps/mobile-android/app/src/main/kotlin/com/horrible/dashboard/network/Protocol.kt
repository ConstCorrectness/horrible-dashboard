package com.horrible.dashboard.network

import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import java.util.TreeMap

object Protocol {
    private val moshi = Moshi.Builder()
        .addLast(KotlinJsonAdapterFactory())
        .build()

    // Message types
    const val HELLO = "hello"
    const val HELLO_ACK = "hello_ack"
    const val AUTH = "auth"
    const val AUTH_RESULT = "auth_result"
    const val PRESENCE = "presence"
    const val PING = "ping"
    const val PONG = "pong"
    const val AGENT_REQUEST = "agent_request"
    const val AGENT_RESULT = "agent_result"
    const val REMOTE_COMMAND = "remote_command"

    fun canonicalBytes(env: PeerEnvelope): ByteArray {
        // Authenticated fields: v, type, msg_id, re, src, ts, data
        // Excluded: sig, dst, ttl
        val payload = TreeMap<String, Any?>()
        payload["v"] = env.version
        payload["type"] = env.type
        payload["msg_id"] = env.msgId
        if (env.re != null) payload["re"] = env.re
        payload["src"] = env.src
        payload["ts"] = env.ts
        payload["data"] = env.data

        // Convert to compact JSON with sorted keys
        val adapter = moshi.adapter(Map::class.java)
        val json = adapter.toJson(payload)
        // Moshi's toJson might include spaces depending on setup, but the default is compact.
        // We need to ensure it matches Python's json.dumps(..., separators=(',', ':'))
        return json.toByteArray(Charsets.UTF_8)
    }

    fun signEnvelope(env: PeerEnvelope, identity: Identity): PeerEnvelope {
        val bytes = canonicalBytes(env)
        val sig = identity.sign(bytes)
        return env.copy(sig = sig)
    }

    fun verifyEnvelope(env: PeerEnvelope, publicKeyB64: String): Boolean {
        if (env.sig == null) return false
        val bytes = canonicalBytes(env)
        return Identity.verify(publicKeyB64, bytes, env.sig)
    }
}
