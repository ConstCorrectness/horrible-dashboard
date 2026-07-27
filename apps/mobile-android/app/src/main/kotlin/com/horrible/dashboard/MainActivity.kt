package com.horrible.dashboard

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.horrible.dashboard.network.Identity
import com.horrible.dashboard.network.LanDiscovery
import com.horrible.dashboard.network.PeerHub
import com.horrible.dashboard.ui.PairingScreen
import com.horrible.dashboard.ui.RemoteControlScreen
import com.horrible.dashboard.ui.AgentScreen
import com.horrible.dashboard.ui.FriendsScreen
import com.horrible.dashboard.ui.theme.HorribleDashboardTheme
import androidx.activity.result.contract.ActivityResultContracts
import android.Manifest
import android.content.Intent
import android.net.Uri

class MainActivity : ComponentActivity() {
    private lateinit var identity: Identity
    private lateinit var peerHub: PeerHub
    private lateinit var lanDiscovery: LanDiscovery

    private var initialInvite = mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        identity = Identity.loadOrCreate(this)
        peerHub = PeerHub(this, identity)
        
        val prefs = getSharedPreferences("horrible", MODE_PRIVATE)
        var lastNodeId = prefs.getString("last_node_id", null)

        lanDiscovery = LanDiscovery(this, peerHub)
        lanDiscovery.start()

        handleIntent(intent)

        enableEdgeToEdge()
        setContent {
            HorribleDashboardTheme {
                var screen by remember { mutableStateOf(if (lastNodeId != null) "control" else "pairing") }
                var selectedNodeId by remember { mutableStateOf(lastNodeId) }
                val invite by initialInvite

                LaunchedEffect(Unit) {
                    peerHub.onPeerConnected = { nodeId ->
                        selectedNodeId = nodeId
                        prefs.edit().putString("last_node_id", nodeId).apply()
                    }
                }

                LaunchedEffect(invite) {
                    invite?.let { 
                        peerHub.connectWithInvite(it, 
                            onError = { msg ->
                                // Optional: handle background connect error
                            },
                            onHandshake = {
                                screen = "control"
                            }
                        )
                    }
                }

                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    Box(modifier = Modifier.padding(innerPadding)) {
                        val currentId = selectedNodeId
                        
                        when (screen) {
                            "pairing" -> PairingScreen(peerHub) {
                                screen = "control"
                            }
                            "control" -> {
                                if (currentId != null) {
                                    RemoteControlScreen(peerHub, currentId, 
                                        onOpenAgent = { screen = "agent" },
                                        onOpenFriends = { screen = "friends" }
                                    )
                                } else {
                                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                            CircularProgressIndicator()
                                            Spacer(Modifier.height(16.dp))
                                            Text("Waiting for connection...")
                                            Button(onClick = { screen = "pairing" }) { Text("Back to Pairing") }
                                        }
                                    }
                                }
                            }
                            "agent" -> if (currentId != null) AgentScreen(peerHub, currentId) else screen = "pairing"
                            "friends" -> if (currentId != null) FriendsScreen(peerHub, currentId) else screen = "pairing"
                        }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_VIEW) {
            intent.data?.getQueryParameter("invite")?.let {
                initialInvite.value = it
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        lanDiscovery.stop()
    }
}
