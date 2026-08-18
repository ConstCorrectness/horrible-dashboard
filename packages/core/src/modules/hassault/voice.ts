/**
 * WebRTC & WebAudio 3D Spatial Voice Comms Engine for HorribleAssault.
 *
 * Implements:
 * 1. 3D Spatial Audio: HRTF PannerNodes positioned at player (x, y, z) coordinates.
 * 2. Listener orientation bound to local camera yaw and pitch.
 * 3. Distance model attenuation & air dampening.
 * 4. Push-to-Talk (PTT) & Voice Activity Detection (VAD).
 * 5. Party / Lobby Voice mode (non-spatial stereo).
 */

import { mixer } from '../audio/engine';
import { inputConstraints } from '../audio/store';
import type { StripHandle } from '../audio/types';

mixer.declareStrip({ id: 'voice', label: 'Voice comms', icon: '🎧' });

export interface PeerVoiceState {
  peerId: string;
  callsign: string;
  isSpeaking: boolean;
  isMuted: boolean;
  volume: number;
  x: number;
  y: number;
  z: number;
}

export class SpatialVoiceEngine {
  private ctx: AudioContext | null = null;
  private strip: StripHandle | null = null;
  private localStream: MediaStream | null = null;
  private localGain: GainNode | null = null;
  private panners: Map<string, { panner: PannerNode; gain: GainNode }> = new Map();

  public pttActive = false;
  public mode: 'ptt' | 'vad' = 'ptt';
  public vadThreshold = 0.02;
  public micMuted = false;
  public deafened = false;

  async init(): Promise<boolean> {
    if (this.ctx) return true;
    try {
      // The mixer's context and fader, not a private one: peer voices are audio
      // like any other and belong on a channel the user can route and level.
      this.strip = mixer.connectStrip('voice');
      this.ctx = this.strip.context;

      this.localStream = await navigator.mediaDevices.getUserMedia({
        // Through `inputConstraints` so the microphone the user chose in the
        // mixer is the one used. `{ audio: true }` would quietly take the system
        // default and there would be nothing on screen to explain why.
        audio: inputConstraints({
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        }),
      });

      const source = this.ctx.createMediaStreamSource(this.localStream);
      this.localGain = this.ctx.createGain();
      this.localGain.gain.value = 0; // Muted by default until PTT or VAD fires
      source.connect(this.localGain);

      return true;
    } catch {
      return false;
    }
  }

  setPushToTalk(active: boolean) {
    this.pttActive = active;
    if (!this.localGain || !this.ctx || this.micMuted) return;

    const targetGain = active || this.mode === 'vad' ? 1.0 : 0.0;
    this.localGain.gain.setValueAtTime(targetGain, this.ctx.currentTime);
  }

  updateListener(x: number, y: number, z: number, yaw: number, pitch: number) {
    if (!this.ctx) return;
    const listener = this.ctx.listener;
    const t = this.ctx.currentTime;

    // Set listener position
    if (listener.positionX) {
      listener.positionX.setValueAtTime(x, t);
      listener.positionY.setValueAtTime(y, t);
      listener.positionZ.setValueAtTime(z, t);
    } else {
      listener.setPosition(x, y, z);
    }

    // Forward orientation vector
    const forwardX = Math.cos(yaw) * Math.cos(pitch);
    const forwardY = Math.sin(yaw) * Math.cos(pitch);
    const forwardZ = Math.sin(pitch);

    if (listener.forwardX) {
      listener.forwardX.setValueAtTime(forwardX, t);
      listener.forwardY.setValueAtTime(forwardY, t);
      listener.forwardZ.setValueAtTime(forwardZ, t);
      listener.upX.setValueAtTime(0, t);
      listener.upY.setValueAtTime(0, t);
      listener.upZ.setValueAtTime(1, t);
    } else {
      listener.setOrientation(forwardX, forwardY, forwardZ, 0, 0, 1);
    }
  }

  updatePeerPosition(peerId: string, x: number, y: number, z: number) {
    const entry = this.panners.get(peerId);
    if (!entry || !this.ctx) return;

    const t = this.ctx.currentTime;
    if (entry.panner.positionX) {
      entry.panner.positionX.setValueAtTime(x, t);
      entry.panner.positionY.setValueAtTime(y, t);
      entry.panner.positionZ.setValueAtTime(z, t);
    } else {
      entry.panner.setPosition(x, y, z);
    }
  }

  attachPeerStream(peerId: string, stream: MediaStream, isSpatial = true) {
    if (!this.ctx) return;
    const source = this.ctx.createMediaStreamSource(stream);
    const panner = this.ctx.createPanner();
    const gain = this.ctx.createGain();

    if (isSpatial) {
      panner.panningModel = 'HRTF';
      panner.distanceModel = 'inverse';
      panner.refDistance = 2.0;
      panner.maxDistance = 60.0;
      panner.rolloffFactor = 1.2;
      panner.coneInnerAngle = 360;
    } else {
      panner.panningModel = 'equalpower';
    }

    source.connect(panner);
    panner.connect(gain);
    // The strip, not `ctx.destination` — a peer's voice is routable like
    // everything else, so "send comms to my headphones only" is expressible.
    gain.connect(this.strip?.input ?? this.ctx.destination);

    this.panners.set(peerId, { panner, gain });
  }

  setPeerVolume(peerId: string, volume: number) {
    const entry = this.panners.get(peerId);
    if (!entry || !this.ctx) return;
    entry.gain.gain.setValueAtTime(
      this.deafened ? 0 : Math.max(0, Math.min(2, volume)),
      this.ctx.currentTime,
    );
  }

  cleanup() {
    if (this.localStream) {
      for (const track of this.localStream.getTracks()) {
        track.stop();
      }
    }
    // Release, never close: the context is the mixer's and shared by every
    // sound in the app. Closing it here silenced karaoke and the game the
    // moment voice comms shut down.
    this.strip?.release();
    this.strip = null;
    this.ctx = null;
    this.panners.clear();
  }
}

export const spatialVoice = new SpatialVoiceEngine();
