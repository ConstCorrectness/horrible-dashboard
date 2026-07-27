# Implementation Plan: Mobile Android Companion App (LAN & Remote Control)

Initialize a new Android module `apps/mobile-android` to serve as a mobile companion for the `horrible-dashboard` ecosystem. The app will act as a "Node" in the peer-to-peer fabric, allowing users to monitor their main node, interact with their agent, and remotely control media or other capabilities using a "QR-first" pairing flow and LAN auto-discovery.

## User Review Required

> [!IMPORTANT]
> **Trust & Pairing**: I am proposing a **QR-code based pairing flow**. The Desktop node will generate an invite (Ed25519 pairing token) and display it as a QR code. The mobile app will scan this to establish permanent P2P trust. This avoids the need for a shared Google Account for *pairing*, though we can still use the Google Account for *cloud sync* later.

> [!TIP]
> **Remote Control**: To support "Play this song on the TV", I'll implement a **Remote Capability** system. When a trusted peer (the phone) connects, it can invoke specific "Remote Tools" on the host (the desktop/TV).

## Proposed Changes

### [Component: Mobile Android App]

#### [NEW] [apps/mobile-android](file:///C:/Users/Horrible/Code/horrible-dashboard/apps/mobile-android)
Initialize the Android project with Compose.

- `src/main/kotlin/com/horrible/dashboard/`
    - `network/`:
        - `PeerHub.kt`: Port of the Python `PeerHub`.
        - `LanDiscovery.kt`: mDNS discovery using NsdManager.
        - `Protocol.kt`: Signing and serialization.
    - `ui/`:
        - `PairingScreen.kt`: QR code scanner to redeem invites.
        - `RemoteControlScreen.kt`: UI for controlling other nodes (Media/Agent).

### [Component: Desktop/Backend Enhancements]

#### [MODIFY] [protocol.py](file:///C:/Users/Horrible/Code/horrible-dashboard/backend/modules/network/protocol.py)
Add a new `REMOTE_COMMAND` message type.

#### [NEW] [remote_control.py](file:///C:/Users/Horrible/Code/horrible-dashboard/backend/modules/network/remote_control.py)
A module that handles inbound `REMOTE_COMMAND` envelopes from trusted peers and executes local actions (e.g., controlling the visualizer or media players).

#### [MODIFY] [HomeView.tsx](file:///C:/Users/Horrible/Code/horrible-dashboard/packages/ui/src/HomeView.tsx)
Add a "Pair Mobile" button that generates an invite and shows a QR code.

## Verification Plan

### Manual Verification
- **Pairing**: Desktop -> "Pair Mobile" -> Scan with Phone.
- **LAN Discovery**: Turn off phone Wi-Fi, turn it back on. Phone should auto-reconnect to Desktop via mDNS.
- **Remote Action**: Phone -> Press "Play" -> Desktop logs "Playing song..." (or triggers a real action).
