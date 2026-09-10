/**
 * Client for the server-side Clubhouse voice agent (`backend/modules/clubhouse/voice.py`).
 *
 * The pane no longer decides whether the agent speaks, what it remembers, or what
 * context a turn gets — it reports what happened in the room and renders the answer.
 * That split is what lets the same policy serve voice and chat, and what makes the
 * agent's silence explainable: every turn comes back with a `reason`.
 */
import { apiUrl } from '../../origin';

export type VoicePosture = 'addressed' | 'conversational' | 'always' | 'interject';
export type RetrievalMode = 'off' | 'command' | 'auto';

export type TurnEagerness = 'fast' | 'normal' | 'patient';

export interface VoiceAgentConfig {
  enabled: boolean;
  posture: VoicePosture;
  wakeWords: string[];
  persona: string;
  temperature: number;
  maxTokens: number;
  memoryTurns: number;
  cooldownS: number;
  respondToVoice: boolean;
  respondToChat: boolean;
  retrieval: RetrievalMode;
  library: string;
  speak: boolean;
  postToChat: boolean;
  robotEmojiPrefix: boolean;
  ttsVoice: string;
  ttsRate: string;
  ttsPitch: string;
  ttsVolume?: string;
  model?: string;
  turnEagerness: TurnEagerness;
  endpointingDelayMs: number;
  thinkingFiller: boolean;
  silenceTimeoutS: number;
  allowBargeIn: boolean;
  /**
   * Seconds one person must hold the floor before the agent may cut in mid-sentence.
   * Only consulted under the `interject` posture — every other posture waits for the
   * pause, so this is inert rather than merely unused there.
   */
  interjectAfterS: number;
}

export const DEFAULT_VOICE_CONFIG: VoiceAgentConfig = {
  enabled: false,
  posture: 'addressed',
  wakeWords: ['agent', 'assistant', 'bot'],
  persona:
    'You are a participant in a live Clubhouse audio room. You are speaking out loud to a room of people, not writing.',
  temperature: 0.7,
  maxTokens: 160,
  memoryTurns: 12,
  cooldownS: 6,
  respondToVoice: true,
  respondToChat: false,
  retrieval: 'command',
  library: 'default',
  speak: true,
  postToChat: true,
  robotEmojiPrefix: false,
  ttsVoice: 'en-US-ChristopherNeural',
  ttsRate: '+0%',
  ttsPitch: '+0Hz',
  ttsVolume: '+0%',
  model: '',
  turnEagerness: 'normal',
  endpointingDelayMs: 750,
  thinkingFiller: true,
  silenceTimeoutS: 0,
  allowBargeIn: true,
  interjectAfterS: 6,
};

/** One member of the room as the pane currently sees them. */
export interface VoiceRoomMember {
  user_id: number;
  name: string;
  is_speaker: boolean;
  is_moderator: boolean;
  is_muted: boolean;
  hand_raised: boolean;
  speaking: boolean;
  bio?: string | null;
}

export interface VoiceRoom {
  topic: string | null;
  club: string | null;
  members: VoiceRoomMember[];
  my_user_id: number | null;
  my_name: string;
  /** Title of the music the agent is playing into the room, if any. */
  music?: string | null;
}

/** Something the server asks the pane to do besides speaking. */
export type VoiceAction =
  | { type: 'music.play'; songId: string; title: string; ready: boolean }
  | { type: 'music.stop' | 'music.pause' | 'music.resume' }
  /** `value` sets an absolute level (0–1); `step` moves it ("louder" / "quieter"). */
  | { type: 'music.volume'; value?: number; step?: number };

export interface VoiceTurnResult {
  spoke: boolean;
  reason: string;
  reply: string;
  notice: string | null;
  retrieved: boolean;
  filler?: string | null;
  actions?: VoiceAction[];
}

export interface MusicStatus {
  song_id: string;
  status: string;
  title: string;
  error: string | null;
}

/**
 * A chat sender as the agent should hear them: `Name (@handle)`.
 *
 * `Anonymous` and `Unknown` are the pane's own placeholders, not names — passing one
 * through would tell the model somebody called "Anonymous" is talking.
 */
export function chatSpeakerLabel(comment: {
  userName?: string | null;
  username?: string | null;
}): string | undefined {
  const rawName = comment.userName?.trim() ?? '';
  const name = rawName === 'Anonymous' || rawName === 'Unknown' ? '' : rawName;
  const handle = comment.username?.trim().replace(/^@/, '') ?? '';
  if (name && handle) return `${name} (@${handle})`;
  if (handle) return `@${handle}`;
  return name || undefined;
}

/** Where a song the agent resolved is served from (the karaoke catalog's media route). */
export function musicUrl(songId: string): string {
  return apiUrl(`/api/karaoke/media/${encodeURIComponent(songId)}`);
}

/** Whether a song the agent asked for has finished downloading. */
export async function getMusicStatus(songId: string): Promise<MusicStatus> {
  const res = await fetch(apiUrl(`/api/clubhouse/voice/music/${encodeURIComponent(songId)}`));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as MusicStatus;
}

export interface VoiceStateTurn {
  role: 'room' | 'agent';
  text: string;
  speaker: string;
  source: 'voice' | 'chat' | 'nudge';
  ts: number;
}

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* keep the status */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/** Report one utterance and get back what to do about it. */
export function takeVoiceTurn(params: {
  channel: string;
  text: string;
  speaker?: string;
  speakerId?: number | null;
  source?: 'voice' | 'chat' | 'nudge';
  /** A human pressed "Speak Now" — bypasses posture and cooldown, not echo checks. */
  force?: boolean;
  /**
   * The speaker has not stopped talking — this was flushed mid-breath. The server
   * drops it unless the room is on the `interject` posture, and never remembers it:
   * the finished utterance follows and contains it.
   */
  partial?: boolean;
  room: VoiceRoom;
  /** Active client-side agent config to ensure server session stays perfectly synchronized. */
  config?: VoiceAgentConfig;
  /** Explicitly flag whether this utterance is known to be the agent's own speech (echo) vs operator. */
  isSelf?: boolean;
  signal?: AbortSignal;
}): Promise<VoiceTurnResult> {
  return post<VoiceTurnResult>(
    '/api/clubhouse/voice/turn',
    {
      channel: params.channel,
      text: params.text,
      speaker: params.speaker ?? '',
      speaker_id: params.speakerId ?? null,
      source: params.source ?? 'voice',
      force: params.force ?? false,
      partial: params.partial ?? false,
      room: params.room,
      config: params.config,
      is_self: params.isSelf,
    },
    params.signal,
  );
}

export function pushVoiceConfig(channel: string, config: VoiceAgentConfig): Promise<unknown> {
  return post('/api/clubhouse/voice/config', { channel, config });
}

export function resetVoiceMemory(
  channel: string,
  options?: { resetPersona?: boolean; persona?: string },
): Promise<{ cleared: boolean; persona?: string }> {
  return post<{ cleared: boolean; persona?: string }>('/api/clubhouse/voice/reset', {
    channel,
    reset_persona: options?.resetPersona ?? false,
    persona: options?.persona,
  });
}

export async function getVoiceState(
  channel: string,
): Promise<{ config: VoiceAgentConfig; turns: VoiceStateTurn[]; lastReplyTs: number | null }> {
  const res = await fetch(
    apiUrl(`/api/clubhouse/voice/state?channel=${encodeURIComponent(channel)}`),
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
