/**
 * Conversational agent pane: a transcript you talk to that reasons about the live
 * layout and widget contents (the backend orchestrator pulls that context with its
 * read tools) and acts on them. Each turn replays the prior user/assistant messages
 * as `history` so the conversation is multi-turn; mutating tools the agent runs show
 * as a per-turn action log. Permission prompts render globally (ApprovalPrompts), and
 * code edits surface as an accept/decline diff in the editor — not here.
 * See docs/modules/agent-chat.md.
 */
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { Avatar3D, DEFAULT_AVATAR_MOOD, DEFAULT_AVATAR_MOODS } from '../../Avatar3D';
import { useSetting } from '../../settings';
import { getAgentStatus, type AgentStatus } from './api';
import { askAgent, type AgentTurn } from './orchestrator-client';

const AVATAR_MOODS = Object.keys(DEFAULT_AVATAR_MOODS);

interface ChatTurn {
  role: 'user' | 'assistant';
  text: string;
  /** Mutating tools the agent ran during an assistant turn. */
  actions?: string[];
}

export function ChatWidget() {
  const [status, setStatus] = useState<AgentStatus | 'loading' | 'backend-down'>('loading');
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // The avatar (default on) cycles through its mood animations; the setting turns
  // it off for a plain text pane (see agentModule settings).
  const animateAvatar = useSetting<boolean>('agent.avatarAnimation') ?? true;
  const [mood, setMood] = useState(DEFAULT_AVATAR_MOOD);

  useEffect(() => {
    getAgentStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
  }, []);

  useEffect(() => {
    if (!animateAvatar) return;
    let i = 0;
    const id = setInterval(() => {
      i = (i + 1) % AVATAR_MOODS.length;
      setMood(AVATAR_MOODS[i]);
    }, 6000);
    return () => clearInterval(id);
  }, [animateAvatar]);

  // Keep the latest turn in view as the transcript grows.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns]);

  const ready = typeof status === 'object' && status.configured && status.reachable;

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const text = prompt.trim();
    if (!text || busy || !ready) return;
    setPrompt('');
    setBusy(true);

    // History is the conversation so far (text only); the new user turn and an empty
    // assistant turn we stream into are appended before the call.
    const history: AgentTurn[] = turns.map((t) => ({ role: t.role, content: t.text }));
    const assistantIndex = turns.length + 1;
    setTurns((prev) => [...prev, { role: 'user', text }, { role: 'assistant', text: '' }]);

    const patch = (fn: (t: ChatTurn) => ChatTurn) =>
      setTurns((prev) => prev.map((t, i) => (i === assistantIndex ? fn(t) : t)));

    try {
      await askAgent(
        text,
        {
          onAnswer: (answer) => patch((t) => ({ ...t, text: answer })),
          onAction: (note) => patch((t) => ({ ...t, actions: [...(t.actions ?? []), note] })),
          onError: (msg) => patch((t) => ({ ...t, text: `⚠ ${msg}` })),
        },
        history,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="agent-chat">
      {animateAvatar && (
        <div className="agent-chat-avatar">
          <Avatar3D size={120} mood={mood} />
        </div>
      )}
      <div className="agent-chat-log" ref={scrollRef}>
        {turns.length === 0 && (
          <p className="dashboard-hint">
            Ask me about your layout or a widget&apos;s contents, or tell me to arrange panes or
            edit an open buffer.
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`agent-msg agent-msg-${turn.role}`}>
            {turn.actions && turn.actions.length > 0 && (
              <ul className="agent-actions">
                {turn.actions.map((a, j) => (
                  <li key={j}>✓ {a}</li>
                ))}
              </ul>
            )}
            <div className="agent-bubble">
              {turn.text || (turn.role === 'assistant' && busy ? '…' : '')}
            </div>
          </div>
        ))}
      </div>
      <form className="agent-chat-input" onSubmit={(e) => void send(e)}>
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={ready ? 'Ask the agent…' : 'Agent not ready — finish setup on Home'}
          disabled={!ready || busy}
        />
        <button type="submit" disabled={!ready || busy || !prompt.trim()}>
          {busy ? '…' : '➤'}
        </button>
      </form>
      {status === 'backend-down' && (
        <p className="widget-error">Backend unreachable — is it running on port 8000?</p>
      )}
    </div>
  );
}
