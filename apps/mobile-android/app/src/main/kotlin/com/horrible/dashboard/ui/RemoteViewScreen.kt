package com.horrible.dashboard.ui

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import com.horrible.dashboard.network.PeerHub
import com.horrible.dashboard.network.Protocol

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RemoteViewScreen(peerHub: PeerHub, nodeId: String, onBack: () -> Unit) {
    var frameData by remember { mutableStateOf<String?>(null) }
    var isWaiting by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    val bitmap = remember(frameData) {
        frameData?.let {
            try {
                val bytes = Base64.decode(it.removePrefix("data:image/jpeg;base64,"), Base64.DEFAULT)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            } catch (e: Exception) {
                null
            }
        }
    }

    LaunchedEffect(nodeId) {
        isWaiting = true
        peerHub.registerFrameListener(nodeId) { data ->
            frameData = data
            isWaiting = false
        }
        peerHub.requestView(nodeId)
    }

    DisposableEffect(nodeId) {
        onDispose {
            peerHub.unregisterFrameListener(nodeId)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Watching: $nodeId") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentAlignment = Alignment.Center
        ) {
            if (bitmap != null) {
                Image(
                    bitmap = bitmap.asImageBitmap(),
                    contentDescription = "Remote Screen",
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit
                )
            } else if (isWaiting) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(16.dp))
                    Text("Waiting for stream...")
                }
            } else if (error != null) {
                Text(error!!, color = MaterialTheme.colorScheme.error)
            }
        }
    }
}
