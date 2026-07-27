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
            Log.d("LanDiscovery", "Service found: ${service.serviceName}")
            if (service.serviceType == serviceType || service.serviceType == "${serviceType}local.") {
                nsdManager.resolveService(service, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
                        Log.e("LanDiscovery", "Resolve failed: $errorCode")
                    }

                    override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
                        Log.d("LanDiscovery", "Service resolved: ${serviceInfo.host}:${serviceInfo.port}")
                        val props = serviceInfo.attributes
                        val nodeId = props["node_id"]?.decodeToString()
                        val address = props["address"]?.decodeToString()
                        
                        if (nodeId != null && address != null) {
                            Log.i("LanDiscovery", "Discovered peer $nodeId at $address")
                            // Auto-connect if not already connected
                            peerHub.connect(address)
                        }
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
