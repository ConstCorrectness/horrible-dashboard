package com.horrible.dashboard.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.horrible.dashboard.network.PeerHub
import kotlinx.coroutines.launch

data class Message(
    val role: String,
    val text: String,
    val reasoning: String = "",
    val isStreaming: Boolean = false
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AgentScreen(peerHub: PeerHub, nodeId: String) {
    val scope = rememberCoroutineScope()
    var messages by remember { mutableStateOf(listOf<Message>()) }
    var inputText by remember { mutableStateOf("") }
    val listState = rememberLazyListState()

    fun addMessage(role: String, text: String, reasoning: String = "", isStreaming: Boolean = false) {
        messages = messages + Message(role, text, reasoning, isStreaming)
    }

    fun updateLastMessage(text: String = "", reasoning: String = "", isStreaming: Boolean = false) {
        if (messages.isEmpty()) return
        val last = messages.last()
        messages = messages.dropLast(1) + last.copy(
            text = last.text + text,
            reasoning = last.reasoning + reasoning,
            isStreaming = isStreaming
        )
    }

    LaunchedEffect(messages.size) {
        listState.animateScrollToItem(if (messages.isEmpty()) 0 else messages.size - 1)
    }

    LaunchedEffect(Unit) {
        if (messages.isEmpty()) {
            addMessage("assistant", "Getting you up to speed...", isStreaming = true)
            scope.launch {
                try {
                    val summary = peerHub.getSummary(nodeId) { event, delta ->
                        if (event == "token") {
                            updateLastMessage(text = delta, isStreaming = true)
                        }
                    }
                    updateLastMessage(isStreaming = false)
                } catch (e: Exception) {
                    updateLastMessage(text = "\n[Could not get summary]", isStreaming = false)
                }
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text("Agent: $nodeId") },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant
            )
        )

        LazyColumn(
            state = listState,
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            item { Spacer(modifier = Modifier.height(8.dp)) }
            items(messages) { msg ->
                ChatBubble(msg)
            }
            item { Spacer(modifier = Modifier.height(8.dp)) }
        }

        Surface(
            tonalElevation = 2.dp,
            shadowElevation = 8.dp
        ) {
            Row(
                modifier = Modifier
                    .padding(8.dp)
                    .fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = inputText,
                    onValueChange = { inputText = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Ask your friend...") },
                    maxLines = 4
                )
                IconButton(
                    onClick = {
                        val prompt = inputText
                        if (prompt.isBlank()) return@IconButton
                        inputText = ""
                        addMessage("user", prompt)
                        addMessage("assistant", "", isStreaming = true)
                        
                        scope.launch {
                            try {
                                peerHub.askAgent(nodeId, prompt) { event, delta ->
                                    if (event == "token") {
                                        updateLastMessage(text = delta, isStreaming = true)
                                    } else if (event == "reasoning") {
                                        updateLastMessage(reasoning = delta, isStreaming = true)
                                    }
                                }
                                updateLastMessage(isStreaming = false)
                            } catch (e: Exception) {
                                updateLastMessage(text = "\n[Error: ${e.message}]", isStreaming = false)
                            }
                        }
                    }
                ) {
                    Icon(Icons.Default.Send, contentDescription = "Send")
                }
            }
        }
    }
}

@Composable
fun ChatBubble(msg: Message) {
    val isUser = msg.role == "user"
    val alignment = if (isUser) Alignment.End else Alignment.Start
    val bgColor = if (isUser) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.secondaryContainer
    
    Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = alignment) {
        if (msg.reasoning.isNotBlank()) {
            Box(
                modifier = Modifier
                    .padding(bottom = 4.dp, start = 32.dp, end = 32.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(Color.Black.copy(alpha = 0.05f))
                    .padding(8.dp)
            ) {
                Text(
                    msg.reasoning,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                )
            }
        }
        
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(
                    topStart = 16.dp, 
                    topEnd = 16.dp, 
                    bottomStart = if (isUser) 16.dp else 0.dp, 
                    bottomEnd = if (isUser) 0.dp else 16.dp
                ))
                .background(bgColor)
                .padding(12.dp)
                .widthIn(max = 280.dp)
        ) {
            Text(
                text = if (msg.text.isEmpty() && msg.isStreaming) "..." else msg.text,
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}
