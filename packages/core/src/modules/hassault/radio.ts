/**
 * Who is talking right now, for the in-match radio indicator.
 *
 * Three sources, because there are three ways to be heard. The V key held here
 * is you, known locally and instantly. Teammates keying their radio arrive as the
 * server's `voice` relay (`SessionState.radio`). And anyone in the lobby's voice
 * mesh is *measured*: their audio is over the speaking threshold
 * (`VoiceView.speaking`). A teammate in the mesh shows up in both of the last two,
 * so the list is deduplicated by name.
 */
import type { VoiceView } from './lobby-voice';
import type { LobbyState } from './session';

export interface RadioSpeaker {
  id: string;
  name: string;
  self: boolean;
}

export function radioSpeakers(
  voice: Pick<VoiceView, 'talking' | 'speaking'>,
  radio: Record<string, string>,
  lobby: Pick<LobbyState, 'you' | 'members'> | null,
): RadioSpeaker[] {
  const out: RadioSpeaker[] = [];
  const seen = new Set<string>();
  const add = (id: string, name: string, self: boolean) => {
    const key = self ? '\0self' : name.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ id, name, self });
  };
  const me = lobby?.you ?? '';
  if (voice.talking || (me && voice.speaking.includes(me))) add('self', 'You', true);
  for (const [id, name] of Object.entries(radio)) add(id, name, false);
  for (const id of voice.speaking) {
    if (id === me) continue;
    const member = lobby?.members.find((m) => m.id === id);
    if (member) add(id, member.name, false);
  }
  return out;
}
