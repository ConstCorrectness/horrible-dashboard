package com.horrible.dashboard.network

import com.squareup.moshi.Json
import com.squareup.moshi.JsonClass
import java.util.UUID

const val PROTOCOL_VERSION = 1

@JsonClass(generateAdapter = true)
data class PeerEnvelope(
    @Json(name = "v") val version: Int = PROTOCOL_VERSION,
    val type: String,
    @Json(name = "msg_id") val msgId: String = UUID.randomUUID().toString().replace("-", ""),
    val re: String? = null,
    val src: String,
    val dst: String? = null,
    val ts: Double = System.currentTimeMillis() / 1000.0,
    val ttl: Int = 8,
    val data: Map<String, Any> = emptyMap(),
    val sig: String? = null
)

@JsonClass(generateAdapter = true)
data class PeerInfo(
    @Json(name = "node_id") val nodeId: String,
    @Json(name = "node_name") val nodeName: String,
    @Json(name = "public_key") val publicKey: String,
    val transport: String,
    val address: String? = null,
    val status: String,
    val trusted: Boolean = false,
    val capabilities: List<String> = emptyList()
)

@JsonClass(generateAdapter = true)
data class InviteBundle(
    val address: String,
    val token: String,
    @Json(name = "node_id") val nodeId: String
)
