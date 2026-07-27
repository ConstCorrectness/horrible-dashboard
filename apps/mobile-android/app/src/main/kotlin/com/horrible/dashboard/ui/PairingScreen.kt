package com.horrible.dashboard.ui

import android.Manifest
import android.util.Base64
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.horrible.dashboard.network.InviteBundle
import com.horrible.dashboard.network.PeerHub
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import java.util.concurrent.Executors

import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts

@Composable
fun PairingScreen(peerHub: PeerHub, onPaired: () -> Unit) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val cameraExecutor = remember { Executors.newSingleThreadExecutor() }
    
    var hasPermission by remember { 
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        ) 
    }
    var isProcessing by remember { mutableStateOf(false) }
    var isConnecting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    val moshi = remember { Moshi.Builder().addLast(KotlinJsonAdapterFactory()).build() }
    val inviteAdapter = remember { moshi.adapter(InviteBundle::class.java) }

    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
        onResult = { granted -> hasPermission = granted }
    )

    LaunchedEffect(Unit) {
        if (!hasPermission) {
            launcher.launch(Manifest.permission.CAMERA)
        }
    }

    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(
                "Pair with Desktop",
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.padding(16.dp)
            )
            
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(16.dp)
            ) {
                if (hasPermission) {
                    AndroidView(
                        factory = { ctx ->
                            val previewView = PreviewView(ctx)
                            val cameraProviderFuture = ProcessCameraProvider.getInstance(ctx)
                            cameraProviderFuture.addListener({
                                val cameraProvider = cameraProviderFuture.get()
                                val preview = Preview.Builder().build().also {
                                    it.setSurfaceProvider(previewView.surfaceProvider)
                                }

                                val imageAnalysis = ImageAnalysis.Builder()
                                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                                    .build()

                                val scanner = BarcodeScanning.getClient(
                                    BarcodeScannerOptions.Builder()
                                        .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                                        .build()
                                )

                                imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
                                    val mediaImage = imageProxy.image
                                    if (mediaImage != null && !isProcessing) {
                                        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
                                        scanner.process(image)
                                            .addOnSuccessListener { barcodes ->
                                                for (barcode in barcodes) {
                                                    val rawValue = barcode.rawValue ?: continue
                                                    try {
                                                        isProcessing = true
                                                        val invite = if (rawValue.startsWith("horrible://")) {
                                                            android.net.Uri.parse(rawValue).getQueryParameter("invite")
                                                        } else {
                                                            rawValue
                                                        }
                                                        
                                                        if (invite != null) {
                                                            // Invite is base64url-encoded JSON bundle
                                                            val json = String(Base64.decode(invite, Base64.URL_SAFE))
                                                            val bundle = inviteAdapter.fromJson(json)
                                                            if (bundle != null) {
                                                                Log.i("Pairing", "Redeeming invite to ${bundle.address}")
                                                                isConnecting = true
                                                                peerHub.connect(
                                                                    address = bundle.address, 
                                                                    token = bundle.token,
                                                                    onError = { msg ->
                                                                        android.os.Handler(context.mainLooper).post {
                                                                            error = msg
                                                                            isConnecting = false
                                                                            isProcessing = false
                                                                        }
                                                                    },
                                                                    onHandshake = { 
                                                                        android.os.Handler(context.mainLooper).post {
                                                                            onPaired()
                                                                        }
                                                                    }
                                                                )
                                                            } else {
                                                                isProcessing = false
                                                            }
                                                        } else {
                                                            isProcessing = false
                                                        }
                                                    } catch (e: Exception) {
                                                        Log.e("Pairing", "Failed to parse invite", e)
                                                        isProcessing = false
                                                    }
                                                }
                                            }
                                            .addOnCompleteListener {
                                                imageProxy.close()
                                            }
                                    } else {
                                        imageProxy.close()
                                    }
                                }

                                cameraProvider.bindToLifecycle(
                                    lifecycleOwner,
                                    CameraSelector.DEFAULT_BACK_CAMERA,
                                    preview,
                                    imageAnalysis
                                )
                            }, ContextCompat.getMainExecutor(ctx))
                            previewView
                        },
                        modifier = Modifier.fillMaxSize()
                    )
                } else {
                    Text("Camera permission required", modifier = Modifier.align(Alignment.Center))
                }
                
                if (isConnecting) {
                    Box(
                        modifier = Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.5f)),
                        contentAlignment = Alignment.Center
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            CircularProgressIndicator(color = Color.White)
                            Spacer(modifier = Modifier.height(16.dp))
                            Text("Connecting...", color = Color.White)
                        }
                    }
                }
            }
            
            if (error != null) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(error!!, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
                    
                    var manualIp by remember { mutableStateOf("") }
                    OutlinedTextField(
                        value = manualIp,
                        onValueChange = { manualIp = it },
                        label = { Text("Enter Desktop IP:Port (e.g. 10.0.0.18:8100)") },
                        modifier = Modifier.padding(horizontal = 16.dp).fillMaxWidth()
                    )
                    Button(
                        onClick = {
                            var addr = manualIp.trim()
                            if (!addr.startsWith("ws://")) {
                                addr = "ws://$addr"
                            }
                            // Check if port is missing by looking for a colon after the ws:// prefix
                            if (addr.indexOf(":", 6) == -1) { 
                                addr = "$addr:8100"
                            }
                            if (!addr.endsWith("/peer-ws")) {
                                addr = "$addr/peer-ws"
                            }
                            
                            isConnecting = true
                            peerHub.connect(addr) { 
                                android.os.Handler(context.mainLooper).post {
                                    onPaired()
                                }
                            }
                        },
                        modifier = Modifier.padding(16.dp)
                    ) {
                        Text("Connect Manually")
                    }
                }
            }

            Text(
                "Scan the QR code on your desktop dashboard",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(16.dp)
            )
        }
    }
}
