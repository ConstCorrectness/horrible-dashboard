/**
 * Conversational agent pane: a transcript you talk to that reasons about the live
 * layout and widget contents (the backend orchestrator pulls that context with its
 * read tools) and acts on them. Turns stream reasoning + content live; the prior
 * user/assistant turns replay as `history` so the conversation is multi-turn.
 *
 * Transcripts persist as named **sessions** (backend chat module) — auto-saved each
 * turn, restored on mount, switchable via the session bar. `/`-prefixed inputs run
 * locally as **slash commands** (see slash.ts) and are shown but never persisted or
 * sent to the model. Permission prompts render globally (ApprovalPrompts); code
 * edits surface as an accept/decline diff in the editor — not here.
 * See docs/modules/agent-chat.md.
 */
import { useEffect, useRef, useState, type FormEvent } from 'react';

import { Avatar3D, DEFAULT_AVATAR_MOOD, DEFAULT_AVATAR_MOODS } from '../../Avatar3D';
import { useSetting } from '../../settings';
import { getAgentRoster, getAgentStatus, type AgentStatus, type RosterAgent } from './api';
import { askAgent, type AgentTurn } from './orchestrator-client';
import {
  createSession,
  deleteSession,
  getSession,
  getSessions,
  saveSession,
  setActiveSession,
  type ChatMessage,
  type ChatSessionMeta,
} from './sessions';
import { matchSlash, runSlash } from './slash';
import { claimPendingChatSession, onOpenChatSession } from './openSession';
import { listRoots, listDir } from '../files/api';
import { registry, type OpenPaneInfo } from '../../registry';

const AVATAR_MOODS = Object.keys(DEFAULT_AVATAR_MOODS);

interface ChatTurn {
  role: 'user' | 'assistant' | 'system';
  text: string;
  /** Streamed reasoning/thinking for an assistant turn (`reasoning_content`). */
  reasoning?: string;
  /** Mutating tools the agent ran during an assistant turn. */
  actions?: string[];
  /** Slash-command echo/output: shown but not persisted or replayed to the model. */
  ephemeral?: boolean;
}

/** Persisted turns only (drop ephemeral slash output and system lines). */
function toMessages(turns: ChatTurn[]): ChatMessage[] {
  return turns
    .filter((t) => !t.ephemeral && (t.role === 'user' || t.role === 'assistant'))
    .map((t) => ({
      role: t.role as 'user' | 'assistant',
      content: t.text,
      reasoning: t.reasoning,
      actions: t.actions,
    }));
}

function toTurns(messages: ChatMessage[]): ChatTurn[] {
  return messages.map((m) => ({
    role: m.role,
    text: m.content,
    reasoning: m.reasoning,
    actions: m.actions,
  }));
}

async function getWorkspaceFiles(): Promise<string[]> {
  try {
    const roots = await listRoots();
    const files: string[] = [];
    const queue: string[] = roots.map((r) => r.path);
    let count = 0;
    while (queue.length > 0 && count < 1000) {
      const current = queue.shift()!;
      try {
        const res = await listDir(current);
        for (const entry of res.entries) {
          if (entry.kind === 'dir') {
            if (
              entry.name !== 'node_modules' &&
              entry.name !== '.git' &&
              entry.name !== '.venv' &&
              entry.name !== '__pycache__' &&
              entry.name !== '.pytest_cache' &&
              entry.name !== 'dist' &&
              entry.name !== 'build'
            ) {
              queue.push(entry.path);
            }
          } else {
            files.push(entry.path);
            count++;
          }
        }
      } catch (err) {
        console.error('Failed to list dir', current, err);
      }
    }
    return files;
  } catch (err) {
    console.error('Failed to list roots', err);
    return [];
  }
}

interface ReasoningBlockProps {
  reasoning: string;
  hasText: boolean;
}

function ReasoningBlock({ reasoning, hasText }: ReasoningBlockProps) {
  const [open, setOpen] = useState(!hasText);
  const [prevHasText, setPrevHasText] = useState(hasText);

  if (hasText !== prevHasText) {
    setPrevHasText(hasText);
    if (hasText) {
      setOpen(false);
    }
  }

  return (
    <details
      className="agent-reasoning"
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary>Reasoning</summary>
      <div className="agent-reasoning-body">{reasoning}</div>
    </details>
  );
}

export function ChatWidget() {
  const [status, setStatus] = useState<AgentStatus | 'loading' | 'backend-down'>('loading');
  const [sessions, setSessions] = useState<ChatSessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  // The roster agent this pane is talking to; each agent keeps its own sessions.
  const [roster, setRoster] = useState<RosterAgent[]>([]);
  const [agentId, setAgentId] = useState('main');
  const scrollRef = useRef<HTMLDivElement>(null);
  const turnsRef = useRef<ChatTurn[]>([]);
  turnsRef.current = turns;
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;
  const agentIdRef = useRef('main');
  agentIdRef.current = agentId;
  // The avatar (default on) cycles through its mood animations; the setting turns
  // it off for a plain text pane (see agentModule settings).
  const animateAvatar = useSetting<boolean>('agent.avatarAnimation') ?? true;
  const [mood, setMood] = useState(DEFAULT_AVATAR_MOOD);

  // Autocomplete states
  const [showSuggestions, setShowSuggestions] = useState<'files' | 'panes' | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [triggerIndex, setTriggerIndex] = useState(-1);
  const [selectedSuggestionIndex, setSelectedSuggestionIndex] = useState(0);
  const [workspaceFiles, setWorkspaceFiles] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getAgentStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
    getAgentRoster()
      .then(setRoster)
      .catch(() => {
        /* backend down — the picker just shows the orchestrator */
      });
  }, []);

  // Restore the selected agent's active session (on mount, after a pane remount,
  // and whenever the user switches agents — each agent has its own sessions).
  useEffect(() => {
    let cancelled = false;
    void getSessions(agentId)
      .then(async (list) => {
        if (cancelled) return;
        setSessions(list.sessions);
        if (!list.active) {
          setActiveId(null);
          setTurns([]);
          return;
        }
        setActiveId(list.active);
        const session = await getSession(list.active);
        if (!cancelled) setTurns(toTurns(session.messages));
      })
      .catch(() => {
        /* backend down — start fresh; persistence resumes when it returns */
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

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
  const slashMatches = prompt.startsWith('/') ? matchSlash(prompt) : [];

  const refreshSessions = async () => {
    try {
      setSessions((await getSessions(agentIdRef.current)).sessions);
    } catch {
      /* keep current list */
    }
  };

  const newSession = async () => {
    setTurns([]);
    try {
      const session = await createSession(undefined, agentIdRef.current);
      setActiveId(session.id);
      activeIdRef.current = session.id;
      await refreshSessions();
    } catch {
      setActiveId(null);
    }
  };

  const switchSession = async (id: string) => {
    if (!id || id === activeIdRef.current) return;
    try {
      await setActiveSession(id);
      setActiveId(id);
      setTurns(toTurns((await getSession(id)).messages));
    } catch {
      /* leave current */
    }
  };

  // Open a specific conversation when the git provenance pane (or anything) requests it
  // via openChatSession — claiming a request buffered before this widget mounted.
  useEffect(() => {
    const pending = claimPendingChatSession();
    if (pending) void switchSession(pending);
    return onOpenChatSession((id) => void switchSession(id));
    // switchSession reads live refs; only the once-on-mount wiring matters here.
  }, []);

  const removeSession = async (id: string) => {
    try {
      const list = await deleteSession(id);
      setSessions(list.sessions);
      if (id !== activeIdRef.current) return;
      setActiveId(list.active);
      setTurns(list.active ? toTurns((await getSession(list.active)).messages) : []);
    } catch {
      /* leave current */
    }
  };

  // Lazily create a session on the first real message, titled from the prompt.
  const ensureSession = async (firstPrompt: string): Promise<void> => {
    if (activeIdRef.current) return;
    try {
      const session = await createSession(firstPrompt.slice(0, 40), agentIdRef.current);
      setActiveId(session.id);
      activeIdRef.current = session.id;
      await refreshSessions();
    } catch {
      /* backend down — proceed without persistence */
    }
  };

  const persist = async () => {
    const id = activeIdRef.current;
    if (!id) return;
    try {
      await saveSession(id, { messages: toMessages(turnsRef.current) });
      await refreshSessions();
    } catch {
      /* best-effort */
    }
  };

  const getFilteredItems = () => {
    if (showSuggestions === 'files') {
      return workspaceFiles
        .filter((f) => f.toLowerCase().includes(searchQuery.toLowerCase()))
        .slice(0, 10);
    }
    if (showSuggestions === 'panes') {
      const openPanes = registry.layoutController?.listOpenPanes() ?? [];
      return openPanes
        .filter(
          (p) =>
            p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
            p.instanceId.toLowerCase().includes(searchQuery.toLowerCase()),
        )
        .slice(0, 10);
    }
    return [];
  };

  const selectSuggestion = (item: string | OpenPaneInfo) => {
    const inputEl = inputRef.current;
    if (!inputEl) return;

    let insertText = '';

    if (showSuggestions === 'files') {
      const path = item as string;
      insertText = `@${path} `;
    } else if (showSuggestions === 'panes') {
      const pane = item as OpenPaneInfo;
      insertText = `pane:${pane.instanceId} `;
    }

    const start = triggerIndex;
    const end = inputEl.selectionStart ?? 0;
    const val = prompt;
    const newValue = val.substring(0, start) + insertText + val.substring(end);
    setPrompt(newValue);
    setShowSuggestions(null);

    // Focus input and set cursor position after a timeout
    const newCursorPos = start + insertText.length;
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
        inputRef.current.selectionStart = newCursorPos;
        inputRef.current.selectionEnd = newCursorPos;
      }
    }, 0);
  };

  const handleInputChange = async (value: string) => {
    setPrompt(value);

    const inputEl = inputRef.current;
    if (!inputEl) return;

    const selectionStart = inputEl.selectionStart ?? 0;
    const textBeforeCursor = value.substring(0, selectionStart);

    // Check for @ symbol (files)
    const atMatch = textBeforeCursor.match(/@([^\s]*)$/);
    // Check for pane: prefix (panes)
    const paneMatch = textBeforeCursor.match(/pane:([^\s]*)$/);

    if (atMatch) {
      const query = atMatch[1];
      setSearchQuery(query);
      setTriggerIndex(selectionStart - query.length - 1); // trigger is '@'
      setShowSuggestions('files');
      setSelectedSuggestionIndex(0);

      // Lazily load files
      if (workspaceFiles.length === 0) {
        const list = await getWorkspaceFiles();
        setWorkspaceFiles(list);
      }
    } else if (paneMatch) {
      const query = paneMatch[1];
      setSearchQuery(query);
      setTriggerIndex(selectionStart - query.length - 5); // trigger is 'pane:'
      setShowSuggestions('panes');
      setSelectedSuggestionIndex(0);
    } else {
      setShowSuggestions(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (showSuggestions) {
      const items = getFilteredItems();
      if (items.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setSelectedSuggestionIndex((prev) => (prev + 1) % items.length);
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          setSelectedSuggestionIndex((prev) => (prev - 1 + items.length) % items.length);
        } else if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          selectSuggestion(items[selectedSuggestionIndex]);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          setShowSuggestions(null);
        }
      } else {
        if (e.key === 'Escape') {
          e.preventDefault();
          setShowSuggestions(null);
        }
      }
    }
  };

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const text = prompt.trim();
    if (!text || busy) return;

    // Slash command: run locally, render as ephemeral system output, no model turn.
    if (text.startsWith('/')) {
      setPrompt('');
      setTurns((prev) => [...prev, { role: 'user', text, ephemeral: true }]);
      const out = await runSlash(text, { newSession, setAgent: setAgentId });
      setTurns((prev) => [...prev, { role: 'system', text: out, ephemeral: true }]);
      return;
    }

    if (!ready) return;
    setPrompt('');
    setBusy(true);
    await ensureSession(text);

    // History is the prior real turns (text only); a new user turn and an empty
    // assistant turn we stream into are appended after.
    const history: AgentTurn[] = turnsRef.current
      .filter((t) => t.role !== 'system' && !t.ephemeral)
      .map((t) => ({ role: t.role as 'user' | 'assistant', content: t.text }));
    const assistantIndex = turnsRef.current.length + 1;
    setTurns((prev) => [...prev, { role: 'user', text }, { role: 'assistant', text: '' }]);

    const patch = (fn: (t: ChatTurn) => ChatTurn) =>
      setTurns((prev) => prev.map((t, i) => (i === assistantIndex ? fn(t) : t)));

    // Delegated sub-agents stream under the parent turn: note the hand-off once,
    // then fold their deltas into the reasoning disclosure.
    const delegatesSeen = new Set<string>();
    try {
      await askAgent(
        text,
        {
          onToken: (delta) => patch((t) => ({ ...t, text: t.text + delta })),
          onReasoning: (delta) => patch((t) => ({ ...t, reasoning: (t.reasoning ?? '') + delta })),
          onDelegateToken: (delegateId, delta) => {
            if (delegateId && !delegatesSeen.has(delegateId)) {
              delegatesSeen.add(delegateId);
              patch((t) => ({
                ...t,
                actions: [...(t.actions ?? []), `Delegated to ${delegateId}`],
                reasoning: `${t.reasoning ?? ''}\n[${delegateId}] `,
              }));
            }
            patch((t) => ({ ...t, reasoning: (t.reasoning ?? '') + delta }));
          },
          // The final answer is authoritative; fall back to the streamed text if empty.
          onAnswer: (answer) => patch((t) => ({ ...t, text: answer || t.text })),
          onAction: (note) => patch((t) => ({ ...t, actions: [...(t.actions ?? []), note] })),
          onError: (msg) => patch((t) => ({ ...t, text: `⚠ ${msg}` })),
        },
        history,
        { agentId: agentIdRef.current },
      );
    } finally {
      setBusy(false);
      void persist();
    }
  };

  const canSend = !busy && prompt.trim().length > 0 && (ready || prompt.startsWith('/'));

  return (
    <div className="agent-chat">
      <div className="agent-session-bar">
        {roster.length > 1 && (
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            aria-label="Agent"
            title={roster.find((a) => a.id === agentId)?.description ?? ''}
            disabled={busy}
          >
            {roster.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        )}
        <select
          value={activeId ?? ''}
          onChange={(e) => void switchSession(e.target.value)}
          aria-label="Chat session"
        >
          {(sessions.length === 0 || !activeId) && <option value="">New chat</option>}
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
        <button type="button" title="New chat" onClick={() => void newSession()}>
          ＋
        </button>
        <button
          type="button"
          title="Delete chat"
          disabled={!activeId}
          onClick={() => activeId && void removeSession(activeId)}
        >
          🗑
        </button>
      </div>
      {animateAvatar && (
        <div className="agent-chat-avatar">
          <Avatar3D size={120} mood={mood} />
        </div>
      )}
      <div className="agent-chat-log" ref={scrollRef}>
        {turns.length === 0 && (
          <p className="dashboard-hint">
            Ask me about your layout or a widget&apos;s contents, or tell me to arrange panes or
            edit an open buffer. Type <code>/help</code> for commands.
          </p>
        )}
        {turns.map((turn, i) =>
          turn.role === 'system' ? (
            <pre key={i} className="agent-system">
              {turn.text}
            </pre>
          ) : (
            <div key={i} className={`agent-msg agent-msg-${turn.role}`}>
              {turn.reasoning && (
                <ReasoningBlock reasoning={turn.reasoning} hasText={!!turn.text} />
              )}
              {turn.actions && turn.actions.length > 0 && (
                <ul className="agent-actions">
                  {turn.actions.map((a, j) => (
                    <li key={j}>✓ {a}</li>
                  ))}
                </ul>
              )}
              {(turn.text || turn.role === 'user' || (turn.role === 'assistant' && busy)) && (
                <div className="agent-bubble">
                  {turn.text || (turn.role === 'assistant' && busy ? '…' : '')}
                </div>
              )}
            </div>
          ),
        )}
      </div>
      {slashMatches.length > 0 && (
        <ul className="agent-slash-suggest">
          {slashMatches.map((c) => (
            <li key={c.name}>
              <button type="button" onClick={() => setPrompt(`/${c.name} `)}>
                <span className="agent-slash-name">/{c.name}</span>
                <span className="agent-slash-desc">{c.description}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {showSuggestions && getFilteredItems().length > 0 && (
        <ul className="agent-slash-suggest">
          {getFilteredItems().map((item, idx) => {
            const isSelected = idx === selectedSuggestionIndex;
            const itemStyle: React.CSSProperties = isSelected
              ? { background: 'color-mix(in srgb, var(--accent) 22%, var(--bg-hover))' }
              : {};

            if (showSuggestions === 'files') {
              const path = item as string;
              const parts = path.split(/[/\\]/);
              const name = parts[parts.length - 1];
              const dir = parts.slice(0, -1).join('/');

              return (
                <li key={path}>
                  <button type="button" style={itemStyle} onClick={() => selectSuggestion(path)}>
                    <span className="agent-slash-name">@{name}</span>
                    <span className="agent-slash-desc">{dir}</span>
                  </button>
                </li>
              );
            } else {
              const pane = item as OpenPaneInfo;
              return (
                <li key={pane.instanceId}>
                  <button type="button" style={itemStyle} onClick={() => selectSuggestion(pane)}>
                    <span className="agent-slash-name">pane:{pane.title}</span>
                    <span className="agent-slash-desc">({pane.instanceId})</span>
                  </button>
                </li>
              );
            }
          })}
        </ul>
      )}
      <form className="agent-chat-input" onSubmit={(e) => void send(e)}>
        <input
          ref={inputRef}
          value={prompt}
          onChange={(e) => handleInputChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            ready ? 'Ask the agent…  (/ for commands)' : 'Agent not ready — / for commands'
          }
          disabled={busy}
        />
        <button type="submit" disabled={!canSend}>
          {busy ? '…' : '➤'}
        </button>
      </form>
      {status === 'backend-down' && (
        <p className="widget-error">Backend unreachable — is it running on port 8000?</p>
      )}
    </div>
  );
}
