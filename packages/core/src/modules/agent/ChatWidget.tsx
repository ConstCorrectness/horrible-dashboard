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
import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent } from 'react';

import { Avatar3D, DEFAULT_AVATAR_MOOD, DEFAULT_AVATAR_MOODS } from '../../Avatar3D';
import { dialogs } from '../../dialogs';
import { IconPlus, IconSend, IconTrash } from '../../glyphs';
import { useSetting } from '../../settings';
import { AgentReadiness } from './AgentReadiness';
import { getAgentRoster, getAgentStatus, type AgentStatus, type RosterAgent } from './api';
import { chatState, updateChat, useAgentChat, type ChatTurn } from './chat-state';
import { compactHistory, MAX_HISTORY_TURNS } from './history';
import { ModelPicker } from './ModelPicker';
import { askAgent } from './orchestrator-client';
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
import { agentForWorkspace, setWorkspaceAgent } from '../../layout/persistence';
import { useWorkspaces } from '../../workspace-store';

const AVATAR_MOODS = Object.keys(DEFAULT_AVATAR_MOODS);

/** Opening prompts for an empty transcript. Each one exercises a different half of
 *  what makes this agent unlike a chat box: layout control, pane reading, editing. */
const AGENT_STARTERS = [
  'Open the terminal and the file explorer side by side',
  'What is on screen right now?',
  'Summarise the file open in the editor',
];

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
  // The roster agent this pane is talking to; each agent keeps its own sessions.
  // It follows the *workspace*: a preset declares the persona its layout is for
  // (`FramePreset.agent`), so switching to Data Entry or Data Ops switches who answers.
  const [roster, setRoster] = useState<RosterAgent[]>([]);
  const { activeId: workspaceId } = useWorkspaces();
  const [agentId, setAgentId] = useState(() => agentForWorkspace(workspaceId));
  // Which agent the async handlers below are for. They outlive the render that
  // created them (a streaming turn, a session create), so they must never read
  // `agentId` from the closure.
  const agentIdRef = useRef(agentId);
  agentIdRef.current = agentId;

  // The conversation itself lives outside this component — see chat-state.ts. A
  // pane that keeps its transcript in `useState` loses it every time the shell
  // unmounts it, and the agent's own layout tools are one of the things that do.
  const { sessions, activeId, turns, prompt, busy, restore } = useAgentChat(agentId);
  const setSessions = (v: ChatSessionMeta[]) => updateChat(agentIdRef.current, { sessions: v });
  const setActiveId = (v: string | null) => updateChat(agentIdRef.current, { activeId: v });
  const setPrompt = (v: string) => updateChat(agentIdRef.current, { prompt: v });
  const setBusy = (v: boolean) => updateChat(agentIdRef.current, { busy: v });
  const setRestore = (v: 'loading' | 'ok' | 'failed') =>
    updateChat(agentIdRef.current, { restore: v });
  const setTurns = (v: ChatTurn[] | ((prev: ChatTurn[]) => ChatTurn[])) =>
    updateChat(agentIdRef.current, (prev) => ({
      turns: typeof v === 'function' ? v(prev.turns) : v,
    }));
  /** The transcript as it is *now*, not as this render saw it. */
  const liveTurns = (): ChatTurn[] => chatState(agentIdRef.current).turns;
  /** The active session id as it is now. Async handlers read and write it here. */
  const liveActiveId = (): string | null => chatState(agentIdRef.current).activeId;

  const scrollRef = useRef<HTMLDivElement>(null);
  // Index of the first turn still inside the replay window, or -1 while the whole
  // conversation still fits. Derived from `turns` with the same bound `send` uses,
  // so the seam drawn in the transcript is the seam the model actually gets.
  const compactionBoundary = (() => {
    const persisted = turns.filter((t) => t.role !== 'system' && !t.ephemeral);
    const omitted = persisted.length - MAX_HISTORY_TURNS;
    return omitted > 0 ? turns.indexOf(persisted[omitted]) : -1;
  })();
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

  /** Re-ask the node for its agent status. The readiness banner's Retry runs this,
   *  so a user who just started their model server can recover without a reload. */
  const refreshStatus = () => {
    setStatus('loading');
    getAgentStatus()
      .then(setStatus)
      .catch(() => setStatus('backend-down'));
  };

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

  // Follow the workspace: each preset workspace opens as its declared persona
  // (or the user's override for it). Switching workspaces re-points the pane,
  // which then reloads that agent's own sessions through the effect below.
  useEffect(() => {
    setAgentId(agentForWorkspace(workspaceId));
  }, [workspaceId]);

  /** Picking an agent by hand overrides the workspace's declared persona, and the
   * override sticks so switching away and back doesn't undo the choice. */
  const pickAgent = (id: string) => {
    setWorkspaceAgent(workspaceId, id);
    setAgentId(id);
  };

  /**
   * Load the selected agent's conversations and restore its active one.
   *
   * The failure path is the whole point. This used to swallow the error and leave
   * the pane showing an empty transcript, which is indistinguishable from "your
   * conversation was deleted" — and it was worse than cosmetic: with no `activeId`,
   * the next message `ensureSession` sent created a *second* session, so the real
   * history was orphaned rather than merely hidden. `restore` records the
   * difference so the pane can say which one happened and refuse to fork.
   */
  const loadSessions = useCallback(async (): Promise<boolean> => {
    const agent = agentIdRef.current;
    setRestore('loading');
    try {
      const list = await getSessions(agent);
      setSessions(list.sessions);
      if (!list.active) {
        setActiveId(null);
        setTurns([]);
      } else if (list.active !== chatState(agent).activeId || chatState(agent).turns.length === 0) {
        // Only refetch a transcript we do not already hold. Overwriting the live
        // one is how a turn in flight gets truncated: the fetch returns the last
        // *saved* state (the previous turn), so a reload landing mid-stream
        // replaces a growing answer with the conversation as it was before the
        // question — indistinguishable from the agent losing its place.
        setActiveId(list.active);
        setTurns(toTurns((await getSession(list.active)).messages));
      }
      updateChat(agent, { restore: 'ok', loaded: true });
      return true;
    } catch {
      // Deliberately leaves `sessions`/`turns` untouched: whatever was on screen is
      // closer to the truth than a blank pane.
      setRestore('failed');
      return false;
    }
  }, []);

  // Restore when this agent's conversations have not been read yet, and whenever
  // the user switches agents — each agent has its own sessions.
  //
  // It deliberately does NOT run on every mount any more. The shell unmounts this
  // pane routinely (a workspace switch, a tab going inactive, the agent's own
  // layout tools restructuring the tree it lives in), and re-reading the node on
  // each of those is what made the chat appear to reset: for the length of the
  // round trip the pane showed the starter prompts, and anything not yet saved —
  // the turn being streamed right then — was replaced by the last saved state.
  // The transcript is in the store now, so a remount has nothing to restore.
  useEffect(() => {
    if (chatState(agentId).loaded) return;
    let cancelled = false;
    void (async () => {
      const ok = await loadSessions();
      // One retry: boot races the backend (the Tauri shell spawns it alongside the
      // UI, `pnpm dev` restarts it on every save), and a pane that gives up on the
      // first refused connection shows an empty chat for a backend that is fine a
      // second later.
      if (!ok && !cancelled) {
        await new Promise((r) => setTimeout(r, 700));
        if (!cancelled) await loadSessions();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, loadSessions]);

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
            await refreshSessions();
    } catch {
      setActiveId(null);
    }
  };

  const switchSession = async (id: string) => {
    if (!id || id === liveActiveId()) return;
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
      if (id !== liveActiveId()) return;
      setActiveId(list.active);
      setTurns(list.active ? toTurns((await getSession(list.active)).messages) : []);
    } catch {
      /* leave current */
    }
  };

  /**
   * Ask before destroying a transcript.
   *
   * There is no undo behind `deleteSession`, and the control sits beside "New
   * chat", so the cost of a misclick is a conversation gone with no way back.
   * The dialog names the conversation rather than saying "this chat", because
   * the whole risk is having the wrong one selected.
   */
  const confirmRemoveSession = async () => {
    const id = liveActiveId();
    if (!id) return;
    const title = sessions.find((s) => s.id === id)?.title;
    const ok = await dialogs.confirm({
      title: title ? `Delete “${title}”?` : 'Delete this chat?',
      message: 'The transcript is removed from this node. This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (ok) await removeSession(id);
  };

  // Lazily create a session on the first real message, titled from the prompt.
  const ensureSession = async (firstPrompt: string): Promise<void> => {
    if (liveActiveId()) return;
    // A null `activeId` means "no conversation yet" only when the list was actually
    // read. If the read failed, creating one here would fork the history: the real
    // session is still on the node, and this turn would start a second one beside
    // it. Try the read once more and adopt what it finds first.
    if (restore === 'failed') {
      await loadSessions();
      if (liveActiveId()) return;
    }
    try {
      const session = await createSession(firstPrompt.slice(0, 40), agentIdRef.current);
      setActiveId(session.id);
            await refreshSessions();
    } catch {
      /* backend down — proceed without persistence */
    }
  };

  const persist = async () => {
    const id = liveActiveId();
    if (!id) return;
    try {
      await saveSession(id, { messages: toMessages(liveTurns()) });
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
      const out = await runSlash(text, { newSession, setAgent: pickAgent });
      setTurns((prev) => [...prev, { role: 'system', text: out, ephemeral: true }]);
      return;
    }

    if (!ready) return;
    setPrompt('');
    setBusy(true);
    await ensureSession(text);

    // History is the prior real turns (text only), **compacted** — replaying the
    // whole transcript let the provider silently truncate from the front, taking
    // the system prompt and group guides with it. A new user turn and an empty
    // assistant turn we stream into are appended after.
    const { history } = compactHistory(
      liveTurns()
        .filter((t) => t.role !== 'system' && !t.ephemeral)
        .map((t) => ({ role: t.role as 'user' | 'assistant', content: t.text })),
    );
    const assistantIndex = liveTurns().length + 1;
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
            onChange={(e) => pickAgent(e.target.value)}
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
        {/* Both were unlabelled emoji (＋ and 🗑) sitting 24px apart, and the
            destructive one fired immediately. Now they are drawn glyphs with
            accessible names, and deleting names the conversation it is about to
            destroy — there is no undo behind it. */}
        <ModelPicker
          agentId={agentId}
          status={typeof status === 'object' ? status : null}
          disabled={busy}
        />
        <button
          type="button"
          title="New chat"
          aria-label="New chat"
          onClick={() => void newSession()}
        >
          <IconPlus />
        </button>
        <button
          type="button"
          title="Delete this chat"
          aria-label="Delete this chat"
          className="agent-session-delete"
          disabled={!activeId}
          onClick={() => void confirmRemoveSession()}
        >
          <IconTrash />
        </button>
      </div>
      {restore === 'failed' && (
        <div className="agent-restore-failed" role="alert">
          <span>
            Couldn’t load your conversations — the backend didn’t answer. They’re still saved on
            this node.
          </span>
          <button type="button" onClick={() => void loadSessions()}>
            Retry
          </button>
        </div>
      )}
      {animateAvatar && (
        <div className="agent-chat-avatar">
          <Avatar3D size={120} mood={mood} />
        </div>
      )}
      <div className="agent-chat-log" ref={scrollRef}>
        {turns.length === 0 && (
          // An empty transcript says what this agent can do that a chat box can't
          // — it drives the layout and reads the panes. Three real prompts do that
          // faster than a sentence describing it, and they are clickable, so the
          // first turn costs no typing.
          <div className="agent-chat-starters">
            <p className="agent-chat-starters-lead">
              I can see your open panes and rearrange them, read what a widget is showing, and edit
              an open buffer.
            </p>
            <ul className="agent-chat-starter-list">
              {AGENT_STARTERS.map((s) => (
                <li key={s}>
                  <button
                    type="button"
                    className="agent-chat-starter"
                    onClick={() => {
                      setPrompt(s);
                      inputRef.current?.focus();
                    }}
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
            <p className="agent-chat-starters-foot">
              Type <code>/help</code> for commands.
            </p>
          </div>
        )}
        {turns.map((turn, i) => (
          <Fragment key={i}>
            {i === compactionBoundary && (
              // Where the agent's memory of this conversation starts. Silent
              // compaction is indistinguishable from an agent that forgot, so the
              // seam is shown rather than inferred from odd answers.
              <p className="agent-compacted">Earlier messages are no longer sent to the agent</p>
            )}
            {turn.role === 'system' ? (
              <pre className="agent-system">{turn.text}</pre>
            ) : (
              <div className={`agent-msg agent-msg-${turn.role}`}>
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
            )}
          </Fragment>
        ))}
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
      <AgentReadiness status={status} onRetry={refreshStatus} />
      <form className="agent-chat-input" onSubmit={(e) => void send(e)}>
        <input
          ref={inputRef}
          value={prompt}
          onChange={(e) => handleInputChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask the agent…  (/ for commands)"
          disabled={busy}
        />
        <button type="submit" disabled={!canSend} aria-label={busy ? 'Sending' : 'Send'}>
          {busy ? '…' : <IconSend />}
        </button>
      </form>
    </div>
  );
}
