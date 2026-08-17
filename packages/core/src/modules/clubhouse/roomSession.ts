/**
 * A live Clubhouse room, owned by the **pane** rather than by a mounted component.
 *
 * `useClubhouseVoice` used to hold the connection in `useRef`s and tear it all down
 * in an unmount cleanup. A pane unmounts far more often than it closes — only the
 * active tab of an area renders, a split re-parents the subtree, and a workspace
 * switch replaces the whole frame — so looking at another tab for four seconds ran
 * the destructive path: closed the `AudioContext`, stopped the mic tracks, killed the
 * VAD interval and STT recorder, left Agora, unsubscribed PubNub, and called
 * `leaveClubhouseChannel()`, which hits the real API so **everyone else in the room
 * sees you leave**.
 *
 * That is the same class of bug as `TerminalPane` killing its PTY on a tab switch,
 * and it has the same fix: `layout/pane-lifetime`. The connection lives here, is
 * created once per pane, and is disposed only when the layout says the pane genuinely
 * closed. The hook attaches and detaches.
 *
 * Because the session outlives the component, **room state lives here too**. If it
 * stayed in `useState`, a remount would show an empty room sitting on top of a live
 * connection — the participant list, the chat backlog and the mute state would all
 * reset while the audio kept playing. Subscribers are notified on change and the hook
 * mirrors into React.
 */
import AgoraRTC, { type IAgoraRTCClient, type ILocalAudioTrack } from 'agora-rtc-sdk-ng';
import type PubNub from 'pubnub';

import { leaveClubhouseChannel } from './api';

export interface LiveUserState {
  userId: number;
  handRaised: boolean;
  isSpeaker: boolean;
  isMuted: boolean;
}

export interface ChatComment {
  id: string;
  userName: string;
  userPhoto: string | null;
  text: string;
  timestamp: number;
}

export interface FloatingReaction {
  id: string;
  emoji: string;
  x: number;
  y: number;
}

export interface SpeakerInvite {
  moderatorId: number;
  moderatorName: string;
  moderatorPhoto: string | null;
}

/** Everything a renderer of this room needs. Replaced wholesale on each change. */
export interface RoomState {
  joined: boolean;
  activeChannel: string | null;
  isMuted: boolean;
  handRaised: boolean;
  loading: boolean;
  error: string | null;
  /** Speech pipeline problems (missing `voice` extra, backend down, decode failure). */
  voiceError: string | null;
  comments: ChatComment[];
  activeReactions: FloatingReaction[];
  liveUsers: LiveUserState[];
  speakerInvite: SpeakerInvite | null;
  speakingVolumes: Record<number, number>;
}

export const EMPTY_ROOM_STATE: RoomState = {
  joined: false,
  activeChannel: null,
  isMuted: false,
  handRaised: false,
  loading: false,
  error: null,
  voiceError: null,
  comments: [],
  activeReactions: [],
  liveUsers: [],
  speakerInvite: null,
  speakingVolumes: {},
};

/**
 * The pane's room. Holds the connection resources and the room state; notifies
 * subscribers so any number of mounts can render it.
 */
export class ClubhouseRoomSession {
  // --- connection resources (previously the hook's refs) ---
  audioCtx: AudioContext | null = null;
  agentAudioDest: MediaStreamAudioDestinationNode | null = null;
  sttDest: MediaStreamAudioDestinationNode | null = null;
  sttRecorder: MediaRecorder | null = null;
  physicalMicStream: MediaStream | null = null;
  humanGain: GainNode | null = null;
  rtcClient: IAgoraRTCClient | null = null;
  localAudioTrack: ILocalAudioTrack | null = null;
  pubnub: PubNub | null = null;
  pingInterval: ReturnType<typeof setInterval> | null = null;
  volumeInterval: ReturnType<typeof setInterval> | null = null;
  vadInterval: ReturnType<typeof setInterval> | null = null;

  // --- agent audio ---
  agentAudioSource: AudioBufferSourceNode | null = null;
  isAgentSpeaking = false;
  agentTtsAbort: AbortController | null = null;

  myProfile: { name: string; photoUrl: string | null; userId: number | null } | null = null;

  /**
   * The current mount's callbacks, reassigned on every render.
   *
   * The connection outlives any one mount, so its PubNub listeners and STT recorder
   * must not close over the handlers from the render that opened the room — after a
   * remount those belong to a dead component and their `setState` calls go nowhere.
   * Reading through the session means the live connection always calls the handlers
   * of whatever is mounted now, and calls nothing when nothing is.
   */
  handlers: {
    onTranscribe?: (text: string) => void;
    onBargeIn?: () => void;
    onSpeakerInvite?: (invite: SpeakerInvite) => void;
    onHandRaise?: (userId: number, userName: string) => void;
    onVoiceError?: (message: string) => void;
  } = {};

  chunkIntervalMs = 5000;
  /** Distinct speech-pipeline failures already reported, so a per-chunk failure
   *  raises one alarm rather than one every few seconds. */
  reportedVoiceErrors = new Set<string>();

  state: RoomState = EMPTY_ROOM_STATE;
  private listeners = new Set<() => void>();

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getState = (): RoomState => this.state;

  /** Replace part of the state and notify. Identity changes so `useSyncExternalStore`
   *  sees it; that is why every field is copied rather than mutated in place. */
  patch(next: Partial<RoomState>): void {
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener();
  }

  /** Set one field. The `useState` setter's plain-value form. */
  set<K extends keyof RoomState>(key: K, value: RoomState[K]): void {
    this.patch({ [key]: value } as Partial<RoomState>);
  }

  /** Update one field from its previous value — the `setX(prev => …)` form. */
  update<K extends keyof RoomState>(key: K, fn: (prev: RoomState[K]) => RoomState[K]): void {
    this.patch({ [key]: fn(this.state[key]) } as Partial<RoomState>);
  }

  updateLiveUser(userId: number, patch: Partial<Omit<LiveUserState, 'userId'>>): void {
    const existing = this.state.liveUsers.find((u) => u.userId === userId);
    this.patch({
      liveUsers: existing
        ? this.state.liveUsers.map((u) => (u.userId === userId ? { ...u, ...patch } : u))
        : [
            ...this.state.liveUsers,
            { userId, handRaised: false, isSpeaker: false, isMuted: false, ...patch },
          ],
    });
  }

  removeLiveUser(userId: number): void {
    this.patch({ liveUsers: this.state.liveUsers.filter((u) => u.userId !== userId) });
  }

  reportVoiceError(message: string, notify?: (m: string) => void): void {
    if (this.reportedVoiceErrors.has(message)) return;
    this.reportedVoiceErrors.add(message);
    this.patch({ voiceError: message });
    notify?.(message);
  }

  /**
   * Tear down every resource and tell Clubhouse we left.
   *
   * The **only** caller is `pane-lifetime`'s dispose (a real pane close) and the
   * user's explicit Leave. Never an unmount.
   */
  async teardown(): Promise<void> {
    const channel = this.state.activeChannel;

    for (const key of ['pingInterval', 'volumeInterval', 'vadInterval'] as const) {
      const handle = this[key];
      if (handle) clearInterval(handle);
      this[key] = null;
    }

    if (this.sttRecorder) {
      try {
        if (this.sttRecorder.state !== 'inactive') this.sttRecorder.stop();
      } catch {
        /* already stopped */
      }
      this.sttRecorder = null;
    }
    if (this.localAudioTrack) {
      this.localAudioTrack.close();
      this.localAudioTrack = null;
    }
    if (this.physicalMicStream) {
      this.physicalMicStream.getTracks().forEach((t) => t.stop());
      this.physicalMicStream = null;
    }
    if (this.audioCtx) {
      try {
        await this.audioCtx.close();
      } catch {
        /* already closed */
      }
      this.audioCtx = null;
    }
    this.agentAudioDest = null;
    this.sttDest = null;
    this.humanGain = null;
    this.agentAudioSource = null;
    this.isAgentSpeaking = false;

    if (this.rtcClient) {
      try {
        await this.rtcClient.leave();
      } catch (err) {
        console.error('Error leaving Agora:', err);
      }
      this.rtcClient = null;
    }
    if (this.pubnub) {
      try {
        this.pubnub.unsubscribeAll();
      } catch (err) {
        console.error('Error unsubscribing PubNub:', err);
      }
      this.pubnub = null;
    }

    if (channel) {
      try {
        await leaveClubhouseChannel(channel);
      } catch (err) {
        console.warn('Could not notify Clubhouse leave:', err);
      }
    }
    this.state = EMPTY_ROOM_STATE;
    for (const listener of this.listeners) listener();
  }
}

/** `create` for `usePaneSession`. */
export function createRoomSession(): ClubhouseRoomSession {
  return new ClubhouseRoomSession();
}

/**
 * `dispose` for `usePaneSession` — runs when the pane genuinely closes.
 *
 * Fire-and-forget because pane-lifetime's dispose is synchronous; the awaited work
 * is a `leave` call whose result nothing can act on by then anyway.
 */
export function disposeRoomSession(session: ClubhouseRoomSession): void {
  void session.teardown();
}

/** Publishing a mixed track needs Agora's custom-track factory; kept here so the
 *  session module owns every piece of the connection. */
export function createMixedTrack(stream: MediaStream): ILocalAudioTrack {
  return AgoraRTC.createCustomAudioTrack({ mediaStreamTrack: stream.getAudioTracks()[0] });
}
