package com.horrible.dashboard.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.horrible.dashboard.network.PeerHub
import kotlinx.coroutines.launch

import com.horrible.dashboard.network.ContactsManager
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FriendsScreen(peerHub: PeerHub, nodeId: String) {
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
                            Button(onClick = { /* Challenge to game or chat */ }) {
                                Text("Chat")
                            }
                        }
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}
