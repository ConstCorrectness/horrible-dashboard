package com.horrible.dashboard.network

import android.content.Context
import android.util.Log
import java.security.MessageDigest

class ContactsManager(private val context: Context, private val peerHub: PeerHub) {

    fun hashIdentifier(id: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(id.trim().lowercase().toByteArray())
        return digest.joinToString("") { "%02x".format(it) }
    }

    suspend fun discoverFriends(nodeId: String) {
        // In a real app, we'd read contacts from the ContentResolver here.
        // For now, let's simulate hashing a few mock emails.
        val mocks = listOf("rob@example.com", "horrible@dashboard.io")
        val hashes = mocks.map { hashIdentifier(it) }
        
        Log.i("Contacts", "Discovering friends from ${hashes.size} contacts")
        
        peerHub.sendCommand(nodeId, "match_contacts", mapOf(
            "hashes" to hashes
        ))
    }
}
