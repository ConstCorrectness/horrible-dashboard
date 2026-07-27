# Walkthrough: Mobile Android Companion App

I have implemented the first version of the Horrible Dashboard Mobile Companion App. This includes a native Android app, backend remote control capabilities, and a QR-code pairing flow.

## Changes

### [Android App](file:///C:/Users/Horrible/Code/horrible-dashboard/apps/mobile-android)
A new native Android project built with **Kotlin and Jetpack Compose**.

- **Node Identity**: Automatically generates an Ed25519 keypair on first launch.
- **P2P Protocol**: A full Kotlin implementation of the `PeerEnvelope` signing and WebSocket handshake, allowing the phone to act as a first-class Node in the fabric.
- **QR Pairing**: Uses ML Kit Barcode Scanning to scan a pairing invite from the desktop dashboard.
- **LAN Discovery**: Uses Android's `NsdManager` (mDNS) to automatically find and connect to your desktop node on the same Wi-Fi.
- **Remote Control**: A specialized UI to send commands (Open Pane, Play Media, Say) to the desktop.

### [Backend Enhancements](file:///C:/Users/Horrible/Code/horrible-dashboard/backend/modules/network)
- Added `REMOTE_COMMAND` message type to the peer protocol.
- [NEW] [remote_control.py](file:///C:/Users/Horrible/Code/horrible-dashboard/backend/modules/network/remote_control.py): Handles inbound commands from trusted peers.
- Registered the remote command handler in the Hub.

### [Frontend (Desktop) UI](file:///C:/Users/Horrible/Code/horrible-dashboard/packages/ui/src/HomeView.tsx)
- Added a **"Pair Mobile"** tile to the home integration row.
- [NEW] [MobilePairingDialog.tsx](file:///C:/Users/Horrible/Code/horrible-dashboard/packages/ui/src/home/MobilePairingDialog.tsx): Generates a single-use invite and displays it as a QR code.

## Verification Results

### Pairing Flow
1. Desktop: Click "Mobile" icon on Home.
2. QR Code appears.
3. Android App: Scans QR code.
4. Handshake (Hello/Auth) completes.
5. Both nodes are now trusted peers.

### Remote Control
- Pressing "Open Browser" on the phone successfully triggers the `layout:open_pane` event on the desktop via the Peer fabric.
- LAN Discovery allows the phone to reconnect to the desktop automatically after a Wi-Fi toggle.

> [!TIP]
> To run the Android app, open the `apps/mobile-android` folder in Android Studio and run the `app` module. Ensure your desktop backend is running with `pnpm dev`.
