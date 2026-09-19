/**
 * The in-match chat box: everyone's messages and your team's, in one list.
 *
 * Renders the server's copy of each line (`SessionState.chat`) and nothing
 * else, so the order and the text are what every other player saw. Closed, it
 * shows the last few lines and lets them fade; open, it shows the scrollback
 * and an input.
 *
 * Text rules that are bugs if skipped:
 *
 * - **Grapheme-counted, not `maxLength`.** `maxLength` counts UTF-16 units and
 *   cuts a ZWJ emoji mid-sequence; `clampChat` cuts between clusters.
 * - **IME composition is not a send.** Enter that *confirms* a Japanese or
 *   Chinese candidate arrives with `isComposing` set; treating it as submit
 *   sends half a word.
 * - **Names and messages are bidi-isolated** (`<bdi>`), so an Arabic name
 *   cannot reorder the tag or the message that follows it. The server already
 *   strips the override characters; isolation handles text that is right-to-left
 *   legitimately.
 *
 * See docs/modules/hassault.mdx.
 */
import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';

import { CHAT_FONT, MAX_GRAPHEMES, clampChat, graphemeCount } from './chat-text';
import type { ChatLine } from './session';

/** How long a line stays up with the box closed, and how long it takes to go. */
const VISIBLE_MS = 9000;
const FADE_MS = 1500;
/** Lines shown with the box closed. */
const CLOSED_LINES = 6;

export type ChatChannel = 'all' | 'team';

export interface ChatBoxProps {
  lines: ChatLine[];
  /** `null` when the input is closed. */
  channel: ChatChannel | null;
  selfId: string;
  /** CSS colours by team number. */
  teamColors: string[];
  notice: string;
  onSend: (text: string, team: boolean) => void;
  onClose: () => void;
  /** Tab while typing flips between everyone and team. */
  onChannel: (channel: ChatChannel) => void;
}

export function ChatBox(props: ChatBoxProps) {
  const open = props.channel !== null;
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);
  // A clock for fading, on a timer rather than rAF: rAF is not the pane's to
  // take here, and it stops in a background tab anyway.
  const [now, setNow] = useState(() => performance.now());

  useEffect(() => {
    if (!open) return;
    setDraft('');
    // After paint, so the key that opened the box has finished its default.
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  const newest = props.lines.at(-1)?.at ?? 0;
  useEffect(() => {
    if (open) return;
    if (performance.now() - newest > VISIBLE_MS + FADE_MS) return;
    const id = window.setInterval(() => setNow(performance.now()), 250);
    return () => window.clearInterval(id);
  }, [open, newest]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [props.lines.length, open]);

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // Composition first: the Enter that picks an IME candidate is not a send.
    if (e.nativeEvent.isComposing || e.key === 'Process') return;
    if (e.key === 'Enter') {
      e.preventDefault();
      const text = draft.trim();
      if (text) props.onSend(text, props.channel === 'team');
      props.onClose();
    } else if (e.key === 'Tab') {
      e.preventDefault();
      props.onChannel(props.channel === 'team' ? 'all' : 'team');
    }
    // Escape is handled by the pane's own key ladder, which sees it first.
  };

  const shown = open ? props.lines : props.lines.slice(-CLOSED_LINES);
  const count = graphemeCount(draft);

  return (
    <div style={box} aria-label="Match chat">
      <div
        ref={logRef}
        role="log"
        aria-live="polite"
        style={{
          ...log,
          ...(open ? logOpen : null),
        }}
      >
        {shown.map((line) => {
          const age = now - line.at;
          const opacity = open
            ? 1
            : age < VISIBLE_MS
              ? 1
              : Math.max(0, 1 - (age - VISIBLE_MS) / FADE_MS);
          if (opacity <= 0) return null;
          return (
            <div
              key={line.id}
              style={{ ...row, opacity }}
              title={new Date(line.ts).toLocaleTimeString()}
            >
              <span style={{ ...tag, color: line.isTeam ? TEAM_TAG : ALL_TAG }}>
                {line.isTeam ? 'TEAM' : 'ALL'}
              </span>
              <bdi
                style={{
                  ...name,
                  color: props.teamColors[line.team] ?? 'var(--text-secondary, #94a3b8)',
                }}
              >
                {line.senderName || 'someone'}
                {line.senderId === props.selfId ? ' (you)' : ''}
              </bdi>
              <span style={{ opacity: 0.6 }}>:</span> <bdi style={msg}>{line.text}</bdi>
            </div>
          );
        })}
      </div>

      {open && (
        <div style={inputRow}>
          <button
            type="button"
            className="hd-chat-channel"
            onClick={() => props.onChannel(props.channel === 'team' ? 'all' : 'team')}
            style={{
              ...channelChip,
              color: props.channel === 'team' ? TEAM_TAG : ALL_TAG,
            }}
            title="Tab switches between everyone and your team"
          >
            {props.channel === 'team' ? 'TEAM' : 'ALL'}
          </button>
          <input
            ref={inputRef}
            type="text"
            value={draft}
            onChange={(e) => setDraft(clampChat(e.target.value))}
            onKeyDown={onKeyDown}
            onBlur={props.onClose}
            aria-label={props.channel === 'team' ? 'Message your team' : 'Message everyone'}
            placeholder={props.channel === 'team' ? 'Message your team…' : 'Message everyone…'}
            autoComplete="off"
            spellCheck
            dir="auto"
            style={input}
          />
          {count > MAX_GRAPHEMES - 40 && (
            <span style={counter}>
              {count}/{MAX_GRAPHEMES}
            </span>
          )}
        </div>
      )}
      {open && props.notice && <div style={noticeStyle}>Not sent — {props.notice}.</div>}
    </div>
  );
}

const TEAM_TAG = 'var(--success, #4ade80)';
const ALL_TAG = 'var(--text-secondary, #94a3b8)';
const MONO = 'var(--font-mono, "JetBrains Mono", Consolas, monospace)';

const box: CSSProperties = {
  position: 'absolute',
  left: 12,
  bottom: 96,
  width: 'min(460px, 45%)',
  zIndex: 3,
  fontFamily: CHAT_FONT,
  fontSize: '12.5px',
  color: 'var(--text, #e8eaf2)',
  pointerEvents: 'none',
};

const log: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 2,
  maxHeight: 180,
  overflow: 'hidden',
  textShadow: '0 1px 2px rgba(0,0,0,.9)',
};

const logOpen: CSSProperties = {
  maxHeight: 260,
  overflowY: 'auto',
  pointerEvents: 'auto',
  padding: '0.4rem 0.5rem',
  background: 'rgba(5,6,9,.62)',
  borderTop: '2px solid var(--accent, #6ea8fe)',
};

const row: CSSProperties = {
  lineHeight: 1.45,
  // Long words (a URL, a row of emoji) wrap rather than pushing the box wider.
  overflowWrap: 'anywhere',
  transition: 'opacity 200ms linear',
};

const tag: CSSProperties = {
  fontFamily: MONO,
  fontSize: '10.5px',
  letterSpacing: '0.08em',
  marginRight: '0.4rem',
};

const name: CSSProperties = { fontWeight: 600 };
const msg: CSSProperties = { whiteSpace: 'pre-wrap' };

const inputRow: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.4rem',
  marginTop: 4,
  pointerEvents: 'auto',
};

const channelChip: CSSProperties = {
  fontFamily: MONO,
  fontSize: '10.5px',
  letterSpacing: '0.08em',
  background: 'rgba(5,6,9,.8)',
  border: '1px solid rgba(150,160,190,.22)',
  borderRadius: 4,
  padding: '0 0.45rem',
  height: 30,
  cursor: 'pointer',
};

const input: CSSProperties = {
  flex: 1,
  minWidth: 0,
  padding: '0 0.6rem',
  fontFamily: CHAT_FONT,
  background: 'rgba(5,6,9,.8)',
};

const counter: CSSProperties = {
  fontFamily: MONO,
  fontSize: '10.5px',
  color: 'var(--text-dim, #8b93a7)',
};

const noticeStyle: CSSProperties = {
  marginTop: 2,
  fontSize: '11px',
  color: 'var(--danger, #f87171)',
  pointerEvents: 'none',
};
