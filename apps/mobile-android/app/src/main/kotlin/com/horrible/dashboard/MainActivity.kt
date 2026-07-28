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
import com.horrible.dashboard.ui.RemoteViewScreen
import com.horrible.dashboard.ui.theme.HorribleDashboardTheme
import androidx.activity.result.contract.ActivityResultContracts
import android.Manifest
import android.content.Intent
import android.net.Uri

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith

sealed class Screen {
    object Pairing : Screen()
    object Control : Screen()
    object Agent : Screen()
    object Friends : Screen()
    object Watch : Screen()
}

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
                val navigationStack = remember { mutableStateListOf<Screen>(if (lastNodeId != null) Screen.Control else Screen.Pairing) }
                val currentScreen = navigationStack.last()
                
                var connectedNodeId by remember { mutableStateOf<String?>(null) }
                var isConnecting by remember { mutableStateOf(false) }
                val invite by initialInvite

                fun navigateTo(screen: Screen) {
                    navigationStack.add(screen)
                }

                fun goBack() {
                    if (navigationStack.size > 1) {
                        navigationStack.removeAt(navigationStack.size - 1)
                    }
                }

                BackHandler(enabled = navigationStack.size > 1) {
                    goBack()
                }

                LaunchedEffect(Unit) {
                    peerHub.onPeerConnected = { nodeId ->
                        connectedNodeId = nodeId
                        isConnecting = false
                        prefs.edit().putString("last_node_id", nodeId).apply()
                    }
                    peerHub.onPeerDisconnected = { nodeId ->
                        if (connectedNodeId == nodeId) {
                            connectedNodeId = null
                        }
                    }
                }

                LaunchedEffect(invite) {
                    invite?.let { 
                        isConnecting = true
                        peerHub.connectWithInvite(it, 
                            onError = { msg ->
                                isConnecting = false
                            },
                            onHandshake = {
                                if (navigationStack.last() is Screen.Pairing) {
                                    navigationStack.clear()
                                    navigationStack.add(Screen.Control)
                                }
                            }
                        )
                    }
                }

                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    Box(modifier = Modifier.padding(innerPadding)) {
                        val currentId = connectedNodeId
                        
                        AnimatedContent(
                            targetState = currentScreen,
                            transitionSpec = {
                                fadeIn(animationSpec = tween(300)) togetherWith fadeOut(animationSpec = tween(300))
                            }
                        ) { screen ->
                            when (screen) {
                                is Screen.Pairing -> PairingScreen(peerHub) {
                                    navigationStack.clear()
                                    navigationStack.add(Screen.Control)
                                }
                                is Screen.Control -> {
                                    if (currentId != null) {
                                        RemoteControlScreen(peerHub, currentId, 
                                            onOpenAgent = { navigateTo(Screen.Agent) },
                                            onOpenFriends = { navigateTo(Screen.Friends) },
                                            onWatchScreen = { navigateTo(Screen.Watch) }
                                        )
                                    } else {
                                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                                CircularProgressIndicator()
                                                Spacer(Modifier.height(16.dp))
                                                Text(if (lastNodeId != null) "Reconnecting to $lastNodeId..." else "Waiting for connection...")
                                                Spacer(Modifier.height(8.dp))
                                                Button(onClick = { 
                                                    prefs.edit().remove("last_node_id").apply()
                                                    navigationStack.clear()
                                                    navigationStack.add(Screen.Pairing)
                                                }) { Text("Back to Pairing") }
                                            }
                                        }
                                    }
                                }
                                is Screen.Agent -> if (currentId != null) {
                                    AgentScreen(peerHub, currentId, onBack = { goBack() })
                                } else {
                                    navigationStack.clear()
                                    navigationStack.add(Screen.Pairing)
                                }
                                is Screen.Friends -> if (currentId != null) {
                                    FriendsScreen(peerHub, currentId, onBack = { goBack() }) { 
                                        navigateTo(Screen.Watch)
                                    }
                                } else {
                                    navigationStack.clear()
                                    navigationStack.add(Screen.Pairing)
                                }
                                is Screen.Watch -> if (currentId != null) {
                                    RemoteViewScreen(peerHub, currentId, onBack = { goBack() })
                                } else {
                                    navigationStack.clear()
                                    navigationStack.add(Screen.Pairing)
                                }
                            }
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
