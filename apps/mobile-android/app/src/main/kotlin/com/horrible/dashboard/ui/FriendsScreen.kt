package com.horrible.dashboard.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Monitor
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.horrible.dashboard.network.ContactsManager
import com.horrible.dashboard.network.PeerHub
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FriendsScreen(peerHub: PeerHub, nodeId: String, onBack: () -> Unit, onWatchPeer: (String) -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val contactsManager = remember { ContactsManager(context, peerHub) }
    var friends by remember { mutableStateOf<List<Map<String, Any>>>(emptyList()) }
    var isLoading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        friends = try {
            peerHub.getFriends(nodeId)
        } catch (e: Exception) {
            emptyList()
        }
        isLoading = false
    }

    Column(modifier = Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text("Friends") },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(Icons.Default.ArrowBack, contentDescription = "Back")
                }
            },
            actions = {
                TextButton(onClick = {
                    scope.launch {
                        contactsManager.discoverFriends(nodeId)
                    }
                }) {
                    Text("Sync Contacts")
                }
            }
        )

        if (isLoading) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        } else if (friends.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("No friends found yet.")
            }
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(friends) { friend ->
                    ListItem(
                        headlineContent = { Text(friend["display_name"] as? String ?: "Unknown") },
                        supportingContent = { Text(friend["status"] as? String ?: "Offline") },
                        leadingContent = { Icon(Icons.Default.Person, contentDescription = null) },
                        trailingContent = {
                            Row {
                                IconButton(onClick = { /* Request Voice Chat */ }) {
                                    Icon(Icons.Default.Mic, contentDescription = "Request Voice")
                                }
                                IconButton(onClick = { 
                                    onWatchPeer(friend["node_id"] as? String ?: "")
                                }) {
                                    Icon(Icons.Default.Monitor, contentDescription = "Watch Screen")
                                }
                                Button(onClick = { /* Open Peer Chat */ }) {
                                    Text("Chat")
                                }
                            }
                        }
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}
