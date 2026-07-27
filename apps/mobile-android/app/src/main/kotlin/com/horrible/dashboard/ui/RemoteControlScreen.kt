package com.horrible.dashboard.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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
fun RemoteControlScreen(peerHub: PeerHub, nodeId: String, onOpenAgent: () -> Unit, onOpenFriends: () -> Unit) {
    val scope = CoroutineScope(Dispatchers.Main)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text("Control: $nodeId", style = MaterialTheme.typography.headlineSmall)

        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = onOpenAgent,
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.tertiary)
            ) {
                Text("Agent Chat")
            }
            Button(
                onClick = onOpenFriends,
                modifier = Modifier.weight(1f),
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.secondary)
            ) {
                Text("Friends List")
            }
        }

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
