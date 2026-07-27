package com.horrible.dashboard.network

import android.content.Context
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory

class PeerHub(private val context: Context, private val identity: Identity) {
    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    
    private val moshi = Moshi.Builder()
        .addLast(KotlinJsonAdapterFactory())
        .build()
    private val envelopeAdapter = moshi.adapter(PeerEnvelope::class.java)

    private val peers = ConcurrentHashMap<String, PeerSession>()
    private val scope = CoroutineScope(Dispatchers.IO)

    fun connect(address: String, token: String? = null) {
        val request = Request.Builder().url(address).build()
        val session = PeerSession(identity, envelopeAdapter)
        
        client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                session.webSocket = webSocket
                scope.launch {
                    try {
                        handshake(session, token)
                        peers[session.nodeId!!] = session
                        Log.i("PeerHub", "Connected to ${session.nodeId}")
                    } catch (e: Exception) {
                        Log.e("PeerHub", "Handshake failed", e)
                        webSocket.close(1000, "Handshake failed")
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val env = envelopeAdapter.fromJson(text) ?: return
                scope.launch {
                    session.handleInbound(env)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                peers.remove(session.nodeId ?: "")
                Log.i("PeerHub", "Disconnected: $reason")
            }
        })
    }

    fun disconnect(nodeId: String) {
        peers[nodeId]?.webSocket?.close(1000, "User disconnected")
    }

    suspend fun sendCommand(nodeId: String, command: String, params: Map<String, Any> = emptyMap()) {
        val session = peers[nodeId] ?: throw Exception("No such peer")
        val env = PeerEnvelope(
            type = Protocol.REMOTE_COMMAND,
            src = identity.nodeId,
            dst = nodeId,
            data = mapOf(
                "command" to command,
                "params" to params
            )
        )
        session.send(Protocol.signEnvelope(env, identity))
    }

    private suspend fun handshake(session: PeerSession, token: String?) {
        val myNonce = UUID_HEX()
        val hello = PeerEnvelope(
            type = Protocol.HELLO,
            src = identity.nodeId,
            data = mapOf(
                "node_name" to android.os.Build.MODEL,
                "public_key" to identity.publicKey,
                "capabilities" to listOf("mobile"),
                "nonce" to myNonce
            )
        )
        session.send(Protocol.signEnvelope(hello, identity))

        val ack = session.nextMessage()
        if (ack.type != Protocol.HELLO_ACK) throw Exception("Expected hello_ack")
        
        val publicKey = ack.data["public_key"] as? String ?: throw Exception("No public key")
        if (ack.data["echo"] != myNonce) throw Exception("Nonce mismatch")
        
        session.nodeId = ack.src
        session.publicKey = publicKey
        val theirNonce = ack.data["nonce"] as? String ?: ""

        val auth = PeerEnvelope(
            type = Protocol.AUTH,
            src = identity.nodeId,
            dst = ack.src,
            data = mapOf(
                "echo" to theirNonce,
                "token" to token
            )
        )
        session.send(Protocol.signEnvelope(auth, identity))

        val result = session.nextMessage()
        if (result.type != Protocol.AUTH_RESULT || result.data["ok"] != true) {
            throw Exception("Auth failed: ${result.data["reason"]}")
        }
    }

    private fun UUID_HEX() = java.util.UUID.randomUUID().toString().replace("-", "")
}

class PeerSession(private val identity: Identity, private val adapter: com.squareup.moshi.JsonAdapter<PeerEnvelope>) {
    var webSocket: WebSocket? = null
    var nodeId: String? = null
    var publicKey: String? = null
    
    private val inboundQueue = kotlinx.coroutines.channels.Channel<PeerEnvelope>(16)
    private val pendingRequests = ConcurrentHashMap<String, CompletableDeferred<PeerEnvelope>>()

    suspend fun send(env: PeerEnvelope) {
        webSocket?.send(adapter.toJson(env))
    }

    suspend fun nextMessage(): PeerEnvelope = inboundQueue.receive()

    suspend fun handleInbound(env: PeerEnvelope) {
        // Verify signature if we have the key
        val key = publicKey ?: (env.data["public_key"] as? String)
        if (key != null) {
            if (!Protocol.verifyEnvelope(env, key)) {
                Log.w("PeerSession", "Signature verification failed from ${env.src}")
                return
            }
        }

        if (env.re != null) {
            pendingRequests.remove(env.re)?.complete(env)
            return
        }

        when (env.type) {
            Protocol.PING -> {
                send(Protocol.signEnvelope(
                    PeerEnvelope(type = Protocol.PONG, src = identity.nodeId, dst = env.src, re = env.msgId),
                    identity
                ))
            }
            else -> inboundQueue.send(env)
        }
    }
}
