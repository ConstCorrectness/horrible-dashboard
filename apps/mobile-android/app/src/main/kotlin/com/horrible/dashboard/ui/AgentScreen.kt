package com.horrible.dashboard.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Stop
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
fun AgentScreen(peerHub: PeerHub, nodeId: String, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var messages by remember { mutableStateOf(listOf<Message>()) }
    var inputText by remember { mutableStateOf("") }
    var isThinking by remember { mutableStateOf(false) }
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
        if (messages.isNotEmpty()) {
            listState.animateScrollToItem(messages.size - 1)
        }
    }

    LaunchedEffect(Unit) {
        if (messages.isEmpty()) {
            addMessage("assistant", "Getting you up to speed...", isStreaming = true)
            isThinking = true
            scope.launch {
                try {
                    peerHub.getSummary(nodeId) { event, delta ->
                        if (event == "token") {
                            updateLastMessage(text = delta, isStreaming = true)
                        }
                    }
                    updateLastMessage(isStreaming = false)
                    isThinking = false
                } catch (e: Exception) {
                    updateLastMessage(text = "\n[Could not get summary]", isStreaming = false)
                    isThinking = false
                }
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        TopAppBar(
            title = { Text("Agent Chat") },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(Icons.Default.ArrowBack, contentDescription = "Back")
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant
            )
        )

        LazyColumn(
            state = listState,
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            item { Spacer(modifier = Modifier.height(12.dp)) }
            items(messages) { msg ->
                ChatBubble(msg)
            }
            item { Spacer(modifier = Modifier.height(12.dp)) }
        }

        Surface(
            tonalElevation = 6.dp,
            shadowElevation = 8.dp
        ) {
            Row(
                modifier = Modifier
                    .padding(12.dp)
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .imePadding(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = inputText,
                    onValueChange = { inputText = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("Ask your dashboard friend...") },
                    maxLines = 4,
                    shape = RoundedCornerShape(24.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = MaterialTheme.colorScheme.primary,
                        unfocusedBorderColor = MaterialTheme.colorScheme.outlineVariant
                    )
                )
                
                Spacer(modifier = Modifier.width(8.dp))
                
                if (isThinking) {
                    FilledIconButton(
                        onClick = {
                            scope.launch {
                                peerHub.cancelAgent(nodeId)
                                isThinking = false
                                updateLastMessage(isStreaming = false)
                                addMessage("assistant", "[Interrupted]")
                            }
                        },
                        colors = IconButtonDefaults.filledIconButtonColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer,
                            contentColor = MaterialTheme.colorScheme.error
                        )
                    ) {
                        Icon(Icons.Default.Stop, contentDescription = "Stop")
                    }
                } else {
                    FilledIconButton(
                        onClick = {
                            val prompt = inputText
                            if (prompt.isBlank()) return@FilledIconButton
                            inputText = ""
                            addMessage("user", prompt)
                            addMessage("assistant", "", isStreaming = true)
                            isThinking = true
                            
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
                                    isThinking = false
                                } catch (e: Exception) {
                                    updateLastMessage(text = "\n[Error: ${e.message}]", isStreaming = false)
                                    isThinking = false
                                }
                            }
                        },
                        shape = CircleShape
                    ) {
                        Icon(Icons.Default.Send, contentDescription = "Send")
                    }
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
    val textColor = if (isUser) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSecondaryContainer
    
    var showReasoning by remember { mutableStateOf(false) }

    Column(modifier = Modifier.fillMaxWidth(), horizontalAlignment = alignment) {
        if (msg.reasoning.isNotBlank()) {
            Surface(
                onClick = { showReasoning = !showReasoning },
                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.padding(bottom = 4.dp)
            ) {
                Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)) {
                    Text(
                        if (showReasoning) "▼ Hide Thought Process" else "▶ Thinking...",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary
                    )
                    AnimatedVisibility(
                        visible = showReasoning,
                        enter = expandVertically(),
                        exit = shrinkVertically()
                    ) {
                        Text(
                            msg.reasoning,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 4.dp)
                        )
                    }
                }
            }
        }
        
        Surface(
            color = bgColor,
            shape = RoundedCornerShape(
                topStart = 16.dp, 
                topEnd = 16.dp, 
                bottomStart = if (isUser) 16.dp else 4.dp, 
                bottomEnd = if (isUser) 4.dp else 16.dp
            ),
            tonalElevation = 1.dp
        ) {
            Text(
                text = if (msg.text.isEmpty() && msg.isStreaming) "..." else msg.text,
                style = MaterialTheme.typography.bodyMedium,
                color = textColor,
                modifier = Modifier.padding(12.dp).widthIn(max = 280.dp)
            )
        }
    }
}
