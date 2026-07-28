package com.horrible.dashboard.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.horrible.dashboard.network.PeerHub
import kotlinx.coroutines.launch

data class DashboardApp(
    val name: String,
    val icon: ImageVector,
    val color: Color,
    val onClick: () -> Unit
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RemoteControlScreen(
    peerHub: PeerHub, 
    nodeId: String, 
    onOpenAgent: () -> Unit, 
    onOpenFriends: () -> Unit,
    onWatchScreen: () -> Unit
) {
    val scope = rememberCoroutineScope()
    var searchInput by remember { mutableStateOf("") }

    val apps = listOf(
        DashboardApp("Agent", Icons.Default.AutoMode, Color(0xFF6C5CE7), onOpenAgent),
        DashboardApp("Friends", Icons.Default.People, Color(0xFFFD79A8), onOpenFriends),
        DashboardApp("Monitor", Icons.Default.Monitor, Color(0xFF00B894), onWatchScreen),
        // `pane_id` is the key remote_control.handle_remote_command reads, and the
        // value is a *view* id from the module registry — not a module id.
        DashboardApp("Browser", Icons.Default.Public, Color(0xFF0984E3)) {
            scope.launch { peerHub.sendCommand(nodeId, "open_pane", mapOf("pane_id" to "browser.view")) }
        },
        DashboardApp("Media", Icons.Default.PlayCircle, Color(0xFFE17055)) {
            // Generic play command or open media library
            scope.launch { peerHub.sendCommand(nodeId, "open_pane", mapOf("pane_id" to "library.panel")) }
        },
        DashboardApp("Settings", Icons.Default.Settings, Color(0xFF636E72)) {
            scope.launch { peerHub.sendCommand(nodeId, "open_pane", mapOf("pane_id" to "settings.home")) }
        }
    )

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
    ) {
        // "Desktop" Header
        TopAppBar(
            title = {
                Column {
                    Text("Horrible Dashboard", style = MaterialTheme.typography.titleMedium)
                    Text("Linked: $nodeId", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surface
            )
        )

        Column(
            modifier = Modifier
                .padding(16.dp)
                .fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                "Let's jump in",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.padding(vertical = 16.dp)
            )

            // The big search/ask box like on desktop
            OutlinedTextField(
                value = searchInput,
                onValueChange = { searchInput = it },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 24.dp),
                placeholder = { Text("Ask your dashboard friend...") },
                trailingIcon = {
                    IconButton(onClick = {
                        if (searchInput.isNotBlank()) {
                            onOpenAgent()
                            // We'd pass the initial prompt if AgentScreen supported it
                        }
                    }) {
                        Icon(Icons.Default.Send, contentDescription = "Ask")
                    }
                },
                shape = RoundedCornerShape(24.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedContainerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                    unfocusedContainerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f),
                    unfocusedBorderColor = Color.Transparent
                )
            )

            // App Grid
            LazyVerticalGrid(
                columns = GridCells.Fixed(3),
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(4.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                items(apps) { app ->
                    DashboardAppTile(app)
                }
            }
        }
    }
}

@Composable
fun DashboardAppTile(app: DashboardApp) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .clip(RoundedCornerShape(16.dp))
            .clickable { app.onClick() }
            .padding(8.dp)
    ) {
        Box(
            modifier = Modifier
                .size(64.dp)
                .background(app.color.copy(alpha = 0.15f), RoundedCornerShape(16.dp))
                .border(1.dp, app.color.copy(alpha = 0.3f), RoundedCornerShape(16.dp)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = app.icon,
                contentDescription = app.name,
                tint = app.color,
                modifier = Modifier.size(32.dp)
            )
        }
        Text(
            text = app.name,
            style = MaterialTheme.typography.labelMedium,
            modifier = Modifier.padding(top = 8.dp),
            textAlign = TextAlign.Center,
            fontWeight = FontWeight.Medium
        )
    }
}
