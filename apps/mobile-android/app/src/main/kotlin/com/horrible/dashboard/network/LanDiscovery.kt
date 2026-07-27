package com.horrible.dashboard.network

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log

class LanDiscovery(context: Context, private val peerHub: PeerHub) {
    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val serviceType = "_horrible-peer._tcp."

    private val discoveryListener = object : NsdManager.DiscoveryListener {
        override fun onDiscoveryStarted(regType: String) {
            Log.d("LanDiscovery", "Service discovery started")
        }

        override fun onServiceFound(service: NsdServiceInfo) {
            Log.d("LanDiscovery", "Service found: ${service.serviceName} (${service.serviceType})")
            // Be more permissive with service type matching
            if (service.serviceType.contains("_horrible-peer._tcp")) {
                nsdManager.resolveService(service, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                        Log.e("LanDiscovery", "Resolve failed for ${serviceInfo.serviceName}: $errorCode")
                    }

                    override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                        val host = serviceInfo.host?.hostAddress
                        val port = serviceInfo.port
                        if (host == null) {
                            Log.e("LanDiscovery", "Resolved service has no host address")
                            return
                        }
                        
                        Log.d("LanDiscovery", "Service resolved: $host:$port")
                        
                        val props = serviceInfo.attributes
                        val nodeId = props["node_id"]?.decodeToString()
                        val advertisedAddress = props["address"]?.decodeToString()
                        
                        // If mDNS didn't give us a nodeId, we use the service name as a fallback key
                        val effectiveNodeId = nodeId ?: serviceInfo.serviceName
                        
                        // Build a real address using the resolved IP if the advertised one is localhost
                        val realAddress = if (advertisedAddress != null && !advertisedAddress.contains("localhost") && !advertisedAddress.contains("127.0.0.1")) {
                            advertisedAddress
                        } else {
                            "ws://$host:$port/peer-ws"
                        }
                        
                        Log.i("LanDiscovery", "Discovered peer $effectiveNodeId at $realAddress")
                        peerHub.registerDiscoveredPeer(effectiveNodeId, realAddress)
                    }
                })
            }
        }

        override fun onServiceLost(service: NsdServiceInfo) {
            Log.d("LanDiscovery", "Service lost: ${service.serviceName}")
        }

        override fun onDiscoveryStopped(regType: String) {
            Log.i("LanDiscovery", "Discovery stopped: $regType")
        }

        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
            Log.e("LanDiscovery", "Discovery failed: $errorCode")
            nsdManager.stopServiceDiscovery(this)
        }

        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
            Log.e("LanDiscovery", "Stop discovery failed: $errorCode")
            nsdManager.stopServiceDiscovery(this)
        }
    }

    fun start() {
        nsdManager.discoverServices(serviceType, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
    }

    fun stop() {
        try {
            nsdManager.stopServiceDiscovery(discoveryListener)
        } catch (e: Exception) {
            Log.e("LanDiscovery", "Failed to stop discovery", e)
        }
    }
}
