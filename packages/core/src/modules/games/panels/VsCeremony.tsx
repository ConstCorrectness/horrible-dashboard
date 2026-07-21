import { useState } from 'react';
import { createPortal } from 'react-dom';

import type { Build } from '../agentBuild';

/**
 * The **lock-in ceremony** — the gamified pre-match moment between building your agent
 * and watching it play. A full-screen VS overlay (your agent vs the opponent), a "lock
 * in" clash, then "Enter arena" hands off to a real match. Rendered via a portal so it
 * covers the whole app regardless of the builder pane's size. See docs/modules/games.mdx
 * ("build your agent") and AgentBuilderPanel.tsx.
 *
 * Accent = you, cyan = opponent — the same VS colour logic the live board uses. This
 * overlay deliberately commits to a dark, single look (a match-intro screen), so it
 * doesn't theme to light.
 *
 * The portal mounts on `document.body`, i.e. **outside** the `.games-theme` subtree, so
 * the class is carried on the root below to pick up the module's display type. `--accent`
 * itself now comes from `:root` either way, so "you" tracks the global theme rather than
 * drifting to a second hardcoded hex.
 */

// You = the module's voltage. Aligning these means "you" reads the same here as it does
// on every button in the pane.
const YOU = 'var(--accent)';
const OPP = '#2fe3cf';

const KEYFRAMES = `
@keyframes vs-in-l { from { opacity: 0; transform: translateX(-42px); } to { opacity: 1; transform: none; } }
@keyframes vs-in-r { from { opacity: 0; transform: translateX(42px); } to { opacity: 1; transform: none; } }
@keyframes vs-clash-l { 40% { transform: translateX(20px); } to { transform: none; } }
@keyframes vs-clash-r { 40% { transform: translateX(-20px); } to { transform: none; } }
@keyframes vs-flash { 30% { opacity: 0.5; } to { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .vs-card, .vs-flash { animation: none !important; }
}
`;

function chips(build: Build): string[] {
  const out = ['context'];
  if (build.tools > 0) out.push(`${build.tools} tools`);
  if (build.rag) out.push('rag');
  if (build.memory) out.push('memory');
  out.push(build.model ?? 'default model');
  return out;
}

const btn: React.CSSProperties = {
  fontFamily: 'ui-monospace, monospace',
  fontSize: '0.82rem',
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  padding: '0.7rem 1.3rem',
  borderRadius: 10,
  cursor: 'pointer',
  border: '1px solid #3a3f52',
  background: 'transparent',
  color: '#e9eaf2',
};

function Fighter({
  side,
  name,
  meta,
  chipList,
  hot,
  power,
  clashing,
}: {
  side: 'you' | 'opp';
  name: string;
  meta: string;
  chipList: string[];
  hot: boolean;
  power: number;
  clashing: boolean;
}) {
  const color = side === 'you' ? YOU : OPP;
  const anim = clashing
    ? `vs-clash-${side === 'you' ? 'l' : 'r'} 0.5s ease both`
    : `vs-in-${side === 'you' ? 'l' : 'r'} 0.5s ease both`;
  return (
    <div
      className="vs-card"
      style={{
        flex: '1 1 240px',
        textAlign: 'left',
        padding: 18,
        borderRadius: 14,
        background: '#12141d',
        border: `1px solid ${color}80`,
        boxShadow: `0 0 34px -14px ${color}`,
        animation: anim,
      }}
    >
      <div
        style={{
          fontFamily: 'ui-monospace, monospace',
          fontSize: '0.62rem',
          letterSpacing: '0.2em',
          textTransform: 'uppercase',
          color,
        }}
      >
        {side === 'you' ? '◢ You' : 'Opponent ◣'}
      </div>
      {/* The fighter's name is the one editorial line in the ceremony — everything
          around it stays mono, which is what makes the serif read as a billing. */}
      <div
        style={{
          fontFamily: 'var(--games-display)',
          fontSize: '1.6rem',
          lineHeight: 1.1,
          margin: '8px 0 4px',
          overflowWrap: 'anywhere',
        }}
      >
        {name}
      </div>
      <div style={{ fontFamily: 'ui-monospace, monospace', fontSize: '0.72rem', color: '#8b90a6' }}>
        {meta}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 12 }}>
        {chipList.map((c) => (
          <span
            key={c}
            style={{
              fontFamily: 'ui-monospace, monospace',
              fontSize: '0.6rem',
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '4px 7px',
              borderRadius: 6,
              background: '#191c28',
              color: hot ? `${color}` : '#8b90a6',
              border: `1px solid ${hot ? color + '66' : '#2a2e3e'}`,
            }}
          >
            {c}
          </span>
        ))}
      </div>
      <div
        style={{
          marginTop: 12,
          fontFamily: 'ui-monospace, monospace',
          fontSize: '0.72rem',
          letterSpacing: '0.1em',
          color: '#565b73',
        }}
      >
        POWER <b style={{ color: '#f5b942', fontSize: '0.95rem' }}>{power}</b>
      </div>
    </div>
  );
}

export function VsCeremony({
  agentName,
  gameName,
  build,
  power,
  onEnter,
  onClose,
}: {
  agentName: string;
  gameName: string;
  build: Build;
  power: number;
  onEnter: () => void;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<'ready' | 'locked'>('ready');
  const [clashing, setClashing] = useState(false);

  const lockIn = () => {
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    setClashing(true);
    setTimeout(() => setPhase('locked'), reduce ? 10 : 520);
  };

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Lock in your agent"
      className="games-theme"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'grid',
        placeItems: 'center',
        padding: 20,
        background: 'rgba(9, 6, 4, 0.86)',
        backdropFilter: 'blur(6px)',
      }}
    >
      <style>{KEYFRAMES}</style>
      <div
        className="vs-flash"
        style={{
          position: 'fixed',
          inset: 0,
          background: '#fff',
          opacity: 0,
          pointerEvents: 'none',
          animation: clashing ? 'vs-flash 0.5s ease' : 'none',
        }}
      />
      <div style={{ width: 'min(760px, 100%)', textAlign: 'center' }}>
        <div
          style={{
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.7rem',
            letterSpacing: '0.3em',
            textTransform: 'uppercase',
            color: '#8b90a6',
          }}
        >
          {gameName} — lock in your agent
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            margin: '16px 0',
            flexWrap: 'wrap',
            justifyContent: 'center',
          }}
        >
          <Fighter
            side="you"
            name={agentName}
            meta="Silver II · your build"
            chipList={chips(build)}
            hot
            power={power}
            clashing={clashing}
          />
          <div
            style={{
              fontWeight: 800,
              fontSize: '2rem',
              color: '#e9eaf2',
              textShadow: `0 0 20px ${YOU}80, 0 0 20px ${OPP}66`,
            }}
          >
            VS
          </div>
          <Fighter
            side="opp"
            name="sparring mirror"
            meta="self-play · a copy of your agent"
            chipList={['context', 'baseline']}
            hot={false}
            power={Math.max(1, power - 3)}
            clashing={clashing}
          />
        </div>

        <div
          style={{
            minHeight: 26,
            fontFamily: 'ui-monospace, monospace',
            fontSize: '0.8rem',
            letterSpacing: '0.16em',
            textTransform: 'uppercase',
            color: '#46d38a',
          }}
        >
          {phase === 'locked' ? '◆ Match ready' : ' '}
        </div>

        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 14 }}>
          <button type="button" style={btn} onClick={onClose}>
            ◂ Keep editing
          </button>
          {phase === 'ready' ? (
            <button
              type="button"
              onClick={lockIn}
              style={{
                ...btn,
                border: 'none',
                fontWeight: 700,
                color: '#140a06',
                background: `linear-gradient(180deg, #ff9166, ${YOU})`,
              }}
            >
              Lock in ▸
            </button>
          ) : (
            <button
              type="button"
              onClick={onEnter}
              style={{
                ...btn,
                border: 'none',
                fontWeight: 700,
                color: '#140a06',
                background: `linear-gradient(180deg, #ff9166, ${YOU})`,
              }}
            >
              Enter arena ▸
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
