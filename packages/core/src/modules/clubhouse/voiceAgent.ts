/**
 * Client for the server-side Clubhouse voice agent (`backend/modules/clubhouse/voice.py`).
 *
 * The pane no longer decides whether the agent speaks, what it remembers, or what
 * context a turn gets — it reports what happened in the room and renders the answer.
 * That split is what lets the same policy serve voice and chat, and what makes the
 * agent's silence explainable: every turn comes back with a `reason`.
 */
import { apiUrl } from '../../origin';

/**
 * When the agent answers.
 *
 * - `addressed`: only when someone says its name.
 * - `conversational`: also joins in, but waits `cooldownS` between replies.
 * - `always`: answers every line, no waiting.
 */
export type VoicePosture = 'addressed' | 'conversational' | 'always';

export interface VoiceAgentConfig {
  enabled: boolean;
  posture: VoicePosture;
  persona: string;
  temperature: number;
  maxTokens: number;
  memoryTurns: number;
  /** Seconds between unprompted replies. Only the `conversational` posture reads it. */
  cooldownS: number;
  respondToVoice: boolean;
  respondToChat: boolean;
  speak: boolean;
  postToChat: boolean;
  robotEmojiPrefix: boolean;
  ttsVoice: string;
  ttsRate: string;
  ttsPitch: string;
  ttsVolume: string;
  /** Per-room model override. Blank means the dashboard's own agent model. */
  model: string;
  /** How long a pause has to last before it ends someone's sentence. */
  endpointingDelayMs: number;
  /** Whether a person starting to talk stops the agent mid-sentence. */
  allowBargeIn: boolean;
}

export const DEFAULT_VOICE_CONFIG: VoiceAgentConfig = {
  enabled: false,
  posture: 'addressed',
  persona:
    'You are a participant in a live Clubhouse audio room. You are speaking out loud to a room of people, not writing.',
  temperature: 0.7,
  maxTokens: 160,
  memoryTurns: 12,
  cooldownS: 6,
  respondToVoice: true,
  respondToChat: false,
  speak: true,
  postToChat: true,
  robotEmojiPrefix: false,
  ttsVoice: 'en-US-ChristopherNeural',
  ttsRate: '+0%',
  ttsPitch: '+0Hz',
  ttsVolume: '+0%',
  model: '',
  endpointingDelayMs: 750,
  allowBargeIn: true,
};

/** Bounds the panel offers for the pause that ends a sentence (ms). */
export const ENDPOINTING_RANGE = { min: 400, max: 1500, step: 50 } as const;

const POSTURES: readonly VoicePosture[] = ['addressed', 'conversational', 'always'];

function clamp(value: unknown, fallback: number, lo: number, hi: number): number {
  const n = typeof value === 'string' && value.trim() === '' ? NaN : Number(value);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
}

/**
 * Turn whatever was saved (or pushed by an older pane) into a valid config.
 *
 * The saved shape has changed: wake words, "turn eagerness", the thinking filler, the
 * silence timeout, the interject posture and its interval, and the look-up settings
 * are gone. A config written before that carries those keys; they are dropped here,
 * not carried forward, and the interject posture becomes the nearest one that remains.
 * Anything missing or malformed falls back to the default rather than throwing, since
 * this reads `localStorage`, which anyone can have hand-edited.
 */
export function normalizeVoiceConfig(raw: unknown): VoiceAgentConfig {
  const d = DEFAULT_VOICE_CONFIG;
  const r: Record<string, unknown> =
    raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const str = (v: unknown, fallback: string): string =>
    typeof v === 'string' ? v : fallback;
  const bool = (v: unknown, fallback: boolean): boolean =>
    typeof v === 'boolean' ? v : fallback;

  let posture = str(r.posture, d.posture);
  if (posture === 'interject') posture = 'conversational';

  // An older config expressed the pause as a "turn eagerness" preset and only wrote the
  // number when it was changed by hand.
  const presetDelay = { fast: 400, normal: 750, patient: 1200 } as const;
  const legacyDelay =
    typeof r.turnEagerness === 'string'
      ? presetDelay[r.turnEagerness as keyof typeof presetDelay]
      : undefined;

  return {
    enabled: bool(r.enabled, d.enabled),
    posture: (POSTURES as readonly string[]).includes(posture)
      ? (posture as VoicePosture)
      : d.posture,
    persona: str(r.persona, d.persona),
    temperature: clamp(r.temperature, d.temperature, 0, 2),
    maxTokens: Math.round(clamp(r.maxTokens, d.maxTokens, 20, 1000)),
    memoryTurns: Math.round(clamp(r.memoryTurns, d.memoryTurns, 0, 40)),
    cooldownS: clamp(r.cooldownS, d.cooldownS, 0, 120),
    respondToVoice: bool(r.respondToVoice, d.respondToVoice),
    respondToChat: bool(r.respondToChat, d.respondToChat),
    speak: bool(r.speak, d.speak),
    postToChat: bool(r.postToChat, d.postToChat),
    robotEmojiPrefix: bool(r.robotEmojiPrefix, d.robotEmojiPrefix),
    ttsVoice: str(r.ttsVoice, d.ttsVoice) || d.ttsVoice,
    ttsRate: str(r.ttsRate, d.ttsRate) || d.ttsRate,
    ttsPitch: str(r.ttsPitch, d.ttsPitch) || d.ttsPitch,
    ttsVolume: str(r.ttsVolume, d.ttsVolume) || d.ttsVolume,
    model: str(r.model, d.model).trim(),
    endpointingDelayMs: Math.round(
      clamp(
        r.endpointingDelayMs ?? legacyDelay,
        d.endpointingDelayMs,
        ENDPOINTING_RANGE.min,
        ENDPOINTING_RANGE.max,
      ),
    ),
    allowBargeIn: bool(r.allowBargeIn, d.allowBargeIn),
  };
}

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
}

export interface VoiceTurnResult {
  spoke: boolean;
  reason: string;
  reply: string;
  notice: string | null;
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

/**
 * One row of the agent's debug log. The server writes these at each decision (the gate
 * said no, a reply was spoken, a reply was thrown away, the model failed); the pane adds
 * its own for what only it can see (speech recognition and playback errors, barge-in).
 */
export interface VoiceEvent {
  kind: 'skipped' | 'replied' | 'silent' | 'command' | 'error' | 'pane';
  text: string;
  reason: string;
  source: 'voice' | 'chat' | 'nudge';
  speaker: string;
  /** Generation time, when there was one. */
  ms: number | null;
  model: string;
  detail: string;
  ts: number;
}

export async function getVoiceState(channel: string): Promise<{
  config: VoiceAgentConfig;
  turns: VoiceStateTurn[];
  events: VoiceEvent[];
  lastReplyTs: number | null;
}> {
  const res = await fetch(
    apiUrl(`/api/clubhouse/voice/state?channel=${encodeURIComponent(channel)}`),
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** One leg of the voice pipeline, as `GET /voice/health` reports it. */
export interface VoiceLegHealth {
  ok: boolean;
  /** `ok` means nothing when this is false — the honest rendering is "unknown". */
  certain: boolean;
  detail: string;
  fix: string;
}

export interface VoiceHealth {
  ok: boolean;
  ears: VoiceLegHealth;
  model: VoiceLegHealth;
  mouth: VoiceLegHealth;
  provider: string;
  endpoint: string;
  model_name: string;
}

/**
 * Whether a turn would work right now, without taking one.
 *
 * Polled while a room is open: the three legs are separate processes that stop for
 * unrelated reasons, and from a chair every one of them looks like an agent that has
 * gone quiet. The room's own model override is honoured, so `channel` is passed.
 */
export async function getVoiceHealth(channel: string): Promise<VoiceHealth> {
  const res = await fetch(
    apiUrl(`/api/clubhouse/voice/health?channel=${encodeURIComponent(channel)}`),
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
