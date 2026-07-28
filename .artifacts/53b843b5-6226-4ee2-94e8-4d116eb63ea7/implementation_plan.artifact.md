# Implementation Plan: Chatbot UI, Fun Navigation & Peer Interaction

Upgrade the Android app with a "fun" chatbot-style UI, proper back-button navigation, and the ability to request text/voice chat or "Watch" a friend's dashboard screen.

## User Review Required

> [!IMPORTANT]
> **Interruption & Cancellation**: I will implement `AGENT_CANCEL` in the backend. When you hit "Stop" on your phone, the desktop agent immediately halts its current generation.

> [!TIP]
> **Remote View (Watch Screen)**: I'm proposing a feature where you can request to "Watch" a friend's dashboard. If they "Let you in," their backend will start a frame-stream (JPEG screenshots) relayed over the peer wire to your phone, similar to how the Browser pane works.

## Proposed Features

### 1. "Fun" Chatbot UI (Android)
- **Collapsible Reasoning**: Hide the agent's long thought process inside a "Thinking..." toggle at the top of each bubble.
- **Real-Time Stop Button**: A dedicated button to interrupt the agent mid-stream.
- **Glassmorphic Bubbles**: Modern, translucent chat bubbles with distinct styling for friends vs. your own agent.

### 2. Robust Navigation
- **Navigation Stack**: Move from a simple `screen` state to a list-based stack so the **System Back Button** and gestures work intuitively.
- **Animated Transitions**: Use `AnimatedContent` for smooth cross-fades and slide-ins between screens.
- **One-Handed Top Bar**: Consistent `TopAppBar` with back arrows on all sub-screens.

### 3. Peer-to-Peer Social Collab
- **Request to Talk**: A button on the Friends list to send a "Request Voice/Chat" notification.
- **Remote View**: A "Request to Watch Screen" flow. If the friend accepts, you get a live (JPEG) feed of their active dashboard panes.
- **Handshake Logic**: New `REQUEST_VIEW` and `ACCEPT_VIEW` messages in the peer protocol.

## Proposed Changes

### [Component: Android UI]

#### [MODIFY] [MainActivity.kt](file:///C:/Users/Horrible/Code/horrible-dashboard/apps/mobile-android/app/src/main/kotlin/com/horrible/dashboard/MainActivity.kt)
Implement `NavHost` or a custom `mutableStateListOf` stack to manage screen transitions and back-press handling.

#### [MODIFY] [AgentScreen.kt](file:///C:/Users/Horrible/Code/horrible-dashboard/apps/mobile-android/app/src/main/kotlin/com/horrible/dashboard/ui/AgentScreen.kt)
Update `ChatBubble` to support collapsible `reasoning` and add a "Stop" button.

#### [NEW] [RemoteViewScreen.kt](file:///C:/Users/Horrible/Code/horrible-dashboard/apps/mobile-android/app/src/main/kotlin/com/horrible/dashboard/ui/RemoteViewScreen.kt)
A dedicated screen to display the JPEG frame stream from a peer's dashboard.

### [Component: Backend & P2P]

#### [MODIFY] [protocol.py](file:///C:/Users/Horrible/Code/horrible-dashboard/backend/modules/network/protocol.py)
Add `AGENT_CANCEL`, `VIEW_REQUEST`, and `VIEW_FRAME` message types.

#### [MODIFY] [remote_control.py](file:///C:/Users/Horrible/Code/horrible-dashboard/backend/modules/network/remote_control.py)
Implement handlers for accepting/rejecting view requests and starting the frame pump.

## Verification Plan

### Automated Tests
- **Backstack Test**: Verify that pushing 3 screens and hitting back 3 times returns you to the Pairing screen.
- **Frame Stream Test**: Verify that the backend can push a raw byte array (JPEG) to a peer link without crashing.

### Manual Verification
- **Fun Navigation**: Verify that the phone's back gesture works smoothly.
- **Reasoning Toggle**: Ask the agent a hard question -> Verify the reasoning is hidden until tapped.
- **Watch Screen**: Use two nodes -> Request view from Phone -> Accept on Desktop -> See desktop screen on Phone.
