package com.horrible.dashboard.network

import android.content.Context
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log
import android.widget.Toast
import android.os.Build
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class MobileTools(private val context: Context, private val peerHub: PeerHub) {
    private val scope = CoroutineScope(Dispatchers.IO)

    fun handleRemoteCommand(env: PeerEnvelope) {
        val data = env.data
        val command = data["command"] as? String ?: return
        val params = data["params"] as? Map<String, Any> ?: emptyMap()

        Log.i("MobileTools", "Handling remote command: $command")

        when (command) {
            "notify" -> {
                val text = params["text"] as? String ?: "Notification from Desktop"
                vibrate()
                // For simplicity, just show a toast for now. 
                // In a real app we'd use NotificationManager.
                android.os.Handler(context.mainLooper).post {
                    Toast.makeText(context, text, Toast.LENGTH_LONG).show()
                }
            }
            "capture_photo" -> {
                // In a real app, we'd launch a camera intent or use CameraX.
                // For this demo, let's pretend we took a photo of the MSI laptop.
                val requestId = env.msgId
                Log.i("MobileTools", "Capturing photo for request $requestId")
                
                // Simulate a small delay for "capturing"
                android.os.Handler(context.mainLooper).postDelayed({
                    scope.launch {
                        peerHub.sendTo(env.src, Protocol.AGENT_RESULT, mapOf(
                            "request_id" to requestId,
                            "ok" to true,
                            "note" to "Captured photo of your desk.",
                            "image_data" to "BASE64_IMAGE_DATA_MOCK"
                        ), re = requestId)
                    }
                }, 1000)
            }
        }
    }

    private fun vibrate() {
        val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val vibratorManager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
            vibratorManager.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            vibrator.vibrate(VibrationEffect.createOneShot(500, VibrationEffect.DEFAULT_AMPLITUDE))
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(500)
        }
    }
}
