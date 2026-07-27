package com.horrible.dashboard

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.horrible.dashboard.network.Identity
import com.horrible.dashboard.network.LanDiscovery
import com.horrible.dashboard.network.PeerHub
import com.horrible.dashboard.ui.PairingScreen
import com.horrible.dashboard.ui.RemoteControlScreen
import com.horrible.dashboard.ui.theme.HorribleDashboardTheme
import androidx.compose.runtime.*

class MainActivity : ComponentActivity() {
    private lateinit var identity: Identity
    private lateinit var peerHub: PeerHub
    private lateinit var lanDiscovery: LanDiscovery

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        identity = Identity.loadOrCreate(this)
        peerHub = PeerHub(this, identity)
        lanDiscovery = LanDiscovery(this, peerHub)
        lanDiscovery.start()

        enableEdgeToEdge()
        setContent {
            HorribleDashboardTheme {
                var screen by remember { mutableStateOf("pairing") }
                var selectedNodeId by remember { mutableStateOf<String?>(null) }

                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    Box(modifier = Modifier.padding(innerPadding)) {
                        when (screen) {
                            "pairing" -> PairingScreen(peerHub) {
                                screen = "control"
                            }
                            "control" -> RemoteControlScreen(peerHub, selectedNodeId ?: "Desktop")
                        }
                    }
                }
            }
        }
    }

    override fun onDestroy() {
        super.onCreate()
        lanDiscovery.stop()
    }
}
