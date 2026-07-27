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
    private val inviteAdapter = moshi.adapter(InviteBundle::class.java)

    private val peers = ConcurrentHashMap<String, PeerSession>()
    private val scope = CoroutineScope(Dispatchers.IO)
    private val mobileTools = MobileTools(context, this)
    
    // Cache for LAN addresses discovered via mDNS
    private val discoveredLanAddresses = ConcurrentHashMap<String, String>()
    
    var onPeerConnected: ((String) -> Unit)? = null

    fun registerDiscoveredPeer(nodeId: String, address: String) {
        Log.d("PeerHub", "Registering discovered LAN address for $nodeId: $address")
        discoveredLanAddresses[nodeId] = address
    }

    fun connect(
        address: String, 
        token: String? = null, 
        onError: ((String) -> Unit)? = null,
        onHandshake: ((String) -> Unit)? = null
    ) {
        val request = Request.Builder().url(address).build()
        val session = PeerSession(identity, envelopeAdapter) { env ->
            mobileTools.handleRemoteCommand(env)
        }
        
        client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                session.webSocket = webSocket
                scope.launch {
                    try {
                        // Add a timeout to the handshake
                        kotlinx.coroutines.withTimeout(15000) {
                            handshake(session, token)
                        }
                        val nodeId = session.nodeId!!
                        peers[nodeId] = session
                        Log.i("PeerHub", "Connected to $nodeId")
                        onPeerConnected?.invoke(nodeId)
                        onHandshake?.invoke(nodeId)
                    } catch (e: Exception) {
                        Log.e("PeerHub", "Handshake failed", e)
                        onError?.invoke("Handshake failed: ${e.message}")
                        webSocket.close(1000, "Handshake failed")
                    }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e("PeerHub", "WebSocket connection failed", t)
                val isLocal = address.contains("10.") || address.contains("192.168.")
                val msg = if (isLocal) {
                    "Connection failed. Ensure your desktop dashboard is running with 'pnpm dev:lan' (host 0.0.0.0)."
                } else {
                    "Connection failed: ${t.message}"
                }
                onError?.invoke(msg)
                peers.remove(session.nodeId ?: "")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val env = envelopeAdapter.fromJson(text) ?: return
                // The raw text travels with the parsed envelope: signatures are
                // verified over the bytes as they arrived, never over a re-encode.
                scope.launch {
                    session.handleInbound(text, env)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                peers.remove(session.nodeId ?: "")
                Log.i("PeerHub", "Disconnected: $reason")
            }
        })
    }

    fun connectWithInvite(invite: String, onError: ((String) -> Unit)? = null, onHandshake: ((String) -> Unit)? = null) {
        try {
            val json = String(android.util.Base64.decode(invite, android.util.Base64.URL_SAFE))
            val bundle = inviteAdapter.fromJson(json) ?: return
            
            var address = bundle.address
            val isLocalhost = address.contains("localhost") || address.contains("127.0.0.1")
            
            if (isLocalhost) {
                // Try specific mapping first
                var realAddress = discoveredLanAddresses[bundle.nodeId]
                
                // Fallback: If we have exactly one discovered peer, it's likely the one we want
                if (realAddress == null && discoveredLanAddresses.size == 1) {
                    realAddress = discoveredLanAddresses.values.first()
                    Log.i("PeerHub", "Using only discovered peer as localhost fallback: $realAddress")
                } else if (realAddress == null && discoveredLanAddresses.isNotEmpty()) {
                    // Last resort: take the most recent one
                    realAddress = discoveredLanAddresses.values.last()
                    Log.i("PeerHub", "Using most recent discovered peer as localhost fallback: $realAddress")
                }

                if (realAddress != null) {
                    Log.i("PeerHub", "Redirecting localhost invite for ${bundle.nodeId} to LAN address: $realAddress")
                    address = realAddress
                } else {
                    Log.w("PeerHub", "Invite has localhost but no LAN peers discovered yet via mDNS")
                }
            }
            
            connect(address, bundle.token, onError, onHandshake)
        } catch (e: Exception) {
            Log.e("PeerHub", "Failed to connect with invite", e)
            onError?.invoke("Failed to parse invite: ${e.message}")
        }
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

    /**
     * Send a request and await the peer's reply.
     *
     * The desktop replies with `re` set to the **envelope's msg_id** and tags any
     * `agent_stream` frames with the `request_id` field, so the two only correlate
     * on one key if `request_id` *is* the msg_id — hence the explicit `msgId` here.
     * Listeners are registered before the send, or a fast reply lands before there
     * is anything waiting for it.
     */
    private suspend fun request(
        session: PeerSession,
        type: String,
        data: Map<String, Any>,
        onStream: ((String, String) -> Unit)? = null,
    ): PeerEnvelope {
        val requestId = UUID_HEX()
        val deferred = CompletableDeferred<PeerEnvelope>()
        session.pendingRequests[requestId] = deferred
        if (onStream != null) session.streamListeners[requestId] = onStream
        try {
            val env = PeerEnvelope(
                type = type,
                msgId = requestId,
                src = identity.nodeId,
                dst = session.nodeId,
                data = data + mapOf("request_id" to requestId),
            )
            session.send(Protocol.signEnvelope(env, identity))
            return deferred.await()
        } finally {
            session.streamListeners.remove(requestId)
            session.pendingRequests.remove(requestId)
        }
    }

    suspend fun askAgent(nodeId: String, prompt: String, onStream: (String, String) -> Unit): String {
        val session = peers[nodeId] ?: throw Exception("No such peer")
        val result = request(session, Protocol.AGENT_REQUEST, mapOf("prompt" to prompt), onStream)
        return result.data["text"] as? String ?: ""
    }

    suspend fun getFriends(nodeId: String): List<Map<String, Any>> {
        val session = peers[nodeId] ?: throw Exception("No such peer")
        val result = request(
            session,
            Protocol.REMOTE_COMMAND,
            mapOf("command" to "get_friends", "params" to emptyMap<String, Any>()),
        )
        @Suppress("UNCHECKED_CAST") // the peer's `friends` payload is untyped JSON
        return result.data["friends"] as? List<Map<String, Any>> ?: emptyList()
    }

    suspend fun getSummary(nodeId: String, onStream: (String, String) -> Unit): String {
        val session = peers[nodeId] ?: throw Exception("No such peer")
        val result = request(
            session,
            Protocol.REMOTE_COMMAND,
            mapOf("command" to "get_summary", "params" to emptyMap<String, Any>()),
            onStream,
        )
        return result.data["text"] as? String ?: ""
    }

    suspend fun sendTo(nodeId: String, type: String, data: Map<String, Any>, re: String? = null) {
        val session = peers[nodeId] ?: return
        val env = PeerEnvelope(
            type = type,
            src = identity.nodeId,
            dst = nodeId,
            data = data,
            re = re
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

        val authData = mutableMapOf<String, Any>(
            "echo" to theirNonce
        )
        if (token != null) authData["token"] = token

        val auth = PeerEnvelope(
            type = Protocol.AUTH,
            src = identity.nodeId,
            dst = ack.src,
            data = authData
        )
        session.send(Protocol.signEnvelope(auth, identity))

        val result = session.nextMessage()
        if (result.type != Protocol.AUTH_RESULT || result.data["ok"] != true) {
            throw Exception("Auth failed: ${result.data["reason"]}")
        }
    }

    private fun UUID_HEX() = java.util.UUID.randomUUID().toString().replace("-", "")
}

class PeerSession(
    private val identity: Identity, 
    private val adapter: com.squareup.moshi.JsonAdapter<PeerEnvelope>,
    private val onRemoteCommand: (PeerEnvelope) -> Unit
) {
    var webSocket: WebSocket? = null
    var nodeId: String? = null
    var publicKey: String? = null
    
    private val inboundQueue = kotlinx.coroutines.channels.Channel<PeerEnvelope>(16)
    val pendingRequests = ConcurrentHashMap<String, CompletableDeferred<PeerEnvelope>>()
    val streamListeners = ConcurrentHashMap<String, (String, String) -> Unit>()

    suspend fun send(env: PeerEnvelope) {
        webSocket?.send(adapter.toJson(env))
    }

    suspend fun nextMessage(): PeerEnvelope = inboundQueue.receive()

    suspend fun handleInbound(raw: String, env: PeerEnvelope) {
        // Until the handshake pins the peer's key, trust-on-first-use from the key
        // the envelope carries — but only if its fingerprint really is `src`, the
        // same self-certifying check the desktop runs on us.
        val key = publicKey ?: (env.data["public_key"] as? String)?.takeIf {
            Identity.fingerprint(it) == env.src
        }
        if (key == null) {
            Log.w("PeerSession", "No usable key for ${env.type} from ${env.src}; dropping")
            return
        }
        if (!Protocol.verifyEnvelope(raw, key)) {
            Log.w("PeerSession", "Signature verification failed from ${env.src} (${env.type})")
            return
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
            Protocol.REMOTE_COMMAND -> {
                onRemoteCommand(env)
            }
            Protocol.AGENT_STREAM -> {
                val requestId = env.data["request_id"] as? String
                val event = env.data["event"] as? String
                val delta = env.data["delta"] as? String
                if (requestId != null && event != null && delta != null) {
                    streamListeners[requestId]?.invoke(event, delta)
                }
            }
            else -> inboundQueue.send(env)
        }
    }
}
