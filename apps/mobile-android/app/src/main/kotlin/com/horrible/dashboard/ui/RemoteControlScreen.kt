package com.horrible.dashboard.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.horrible.dashboard.network.PeerHub
import com.horrible.dashboard.network.PeerEnvelope
import com.horrible.dashboard.network.Protocol
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

@Composable
fun RemoteControlScreen(peerHub: PeerHub, nodeId: String) {
    val scope = CoroutineScope(Dispatchers.Main)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Alignment.spacedBy(16.dp)
    ) {
        Text("Control: $nodeId", style = MaterialTheme.typography.headlineSmall)

        Button(
            onClick = {
                scope.launch {
                    peerHub.sendCommand(nodeId, "open_pane", mapOf("pane_id" to "browser"))
                }
            },
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Open Browser on Desktop")
        }

        Button(
            onClick = {
                scope.launch {
                    peerHub.sendCommand(nodeId, "play_media", mapOf("title" to "Mobile Song", "url" to "https://..."))
                }
            },
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Play Song on TV")
        }

        var sayText by remember { mutableStateOf("") }
        OutlinedTextField(
            value = sayText,
            onValueChange = { sayText = it },
            label = { Text("Say something") },
            modifier = Modifier.fillMaxWidth()
        )
        Button(
            onClick = {
                scope.launch {
                    peerHub.sendCommand(nodeId, "say", mapOf("text" to sayText))
                }
            },
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Send Voice Notification")
        }
    }
}
