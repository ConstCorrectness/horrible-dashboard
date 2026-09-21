/**
 * The pause menu — what Escape opens.
 *
 * Four tabs, and the same four panels the main menu shows: a game's settings screen
 * and its *paused* settings screen differ only in what surrounds them, so they are
 * one implementation (`menu-panels.tsx`) composed twice. The one thing that is only
 * here is the way out — **Exit to menu**, which puts you back at the front door
 * without closing the pane.
 *
 * It is a plain DOM overlay rather than anything drawn in WebGL, which is the whole
 * reason it can exist at all: pointer lock is released while it is open, so the
 * mouse is a mouse again and every control here is an ordinary focusable element.
 * That also means the *keyboard* is the shell's again — which is why the rebind rows
 * capture through a focused input instead of a window listener that would fight the
 * dispatcher.
 *
 * See docs/modules/hassault.mdx.
 */
import { useState } from 'react';

import type { Invitee, MapSummary, MatchInvite } from './api';
import type { Bindings } from './controls';
import {
  ControlsPanel,
  FriendsPanel,
  ServerBrowserPanel,
  SettingsPanel,
  styles as panel,
} from './menu-panels';
import type { MatchPeer } from './session';

export type MenuTab = 'settings' | 'servers' | 'friends' | 'controls';

const TABS: { id: MenuTab; label: string }[] = [
  { id: 'settings', label: 'Settings' },
  { id: 'servers', label: 'Servers' },
  { id: 'friends', label: 'Friends' },
  { id: 'controls', label: 'Controls' },
];

export interface GameMenuProps {
  /** Whether we are in a match, and whether it is ours to invite people into. */
  online: boolean;
  hosting: boolean;
  room: string;
  /** Maps this node can actually load, so an unjoinable row says why. */
  maps: MapSummary[];
  peers: MatchPeer[];
  playerId: string;
  invitees: Invitee[];
  invites: MatchInvite[];
  controls: Bindings;
  onControls: (next: Bindings) => void;
  /** Join a match: `host` is empty for one on this node. */
  onJoin: (room: string, map: string, host: string) => void;
  onLeave: () => void;
  onInvite: (friendCode: string) => void;
  onDismissInvite: (room: string) => void;
  /** Answer an invite: a lobby invite takes a seat, a match invite joins it. */
  onAcceptInvite: (invite: MatchInvite) => void;
  onResume: () => void;
  /** Back to the main menu — leaves the match on the way out. */
  onExitToMenu: () => void;
  /** Share match invite link */
  onShare?: () => void;
  onOpenStudio?: () => void;
  onOpenArmory?: () => void;
  onOpenConsole?: () => void;
}

export function GameMenu(props: GameMenuProps) {
  const [tab, setTab] = useState<MenuTab>('settings');

  return (
    <div
      style={styles.backdrop}
      onClick={props.onResume}
      title="Click outside to resume game"
    >
      <div style={styles.sheet} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <strong style={{ letterSpacing: '0.14em', fontSize: '0.95rem' }}>PAUSED</strong>
          <div style={{ display: 'flex', gap: '0.35rem', marginLeft: '0.6rem', flexWrap: 'wrap' }}>
            {props.onOpenStudio && (
              <button
                type="button"
                onClick={props.onOpenStudio}
                style={{ ...styles.tab, color: 'rgb(56, 189, 248)' }}
                title="Edit current map and position in 3D Level Studio"
              >
                ◈ 3D Studio
              </button>
            )}
            {props.onOpenArmory && (
              <button
                type="button"
                onClick={props.onOpenArmory}
                style={{ ...styles.tab, color: 'rgb(245, 158, 11)' }}
                title="Open Armory & Weapon Skins"
              >
                ⚔ Armory
              </button>
            )}
            {props.onOpenConsole && (
              <button
                type="button"
                onClick={props.onOpenConsole}
                style={styles.tab}
                title="Open Developer Console"
              >
                ⌨ Console
              </button>
            )}
          </div>
          <div style={styles.tabs}>
            {TABS.map((entry) => (
              <button
                key={entry.id}
                onClick={() => setTab(entry.id)}
                style={{ ...styles.tab, ...(tab === entry.id ? styles.tabActive : null) }}
              >
                {entry.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={props.onResume}
            style={styles.resume}
            title="Resume game (Esc or click outside)"
          >
            ▶ Resume
          </button>
        </div>

        <div style={styles.body}>
          {tab === 'settings' && <SettingsPanel />}
          {tab === 'servers' && (
            <ServerBrowserPanel
              maps={props.maps}
              peers={props.peers}
              playerId={props.playerId}
              room={props.room}
              hosting={props.hosting}
              onJoin={props.onJoin}
              onInvite={props.onInvite}
            />
          )}
          {tab === 'friends' && (
            <FriendsPanel
              invitees={props.invitees}
              invites={props.invites}
              hosting={props.hosting}
              onInvite={props.onInvite}
              onAccept={props.onAcceptInvite}
              onDismiss={props.onDismissInvite}
            />
          )}
          {tab === 'controls' && (
            <ControlsPanel bindings={props.controls} onChange={props.onControls} />
          )}
        </div>

        <div style={styles.footer}>
          <span>Esc or click outside to resume · hold Esc to give mouse back to app</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
            <button
              type="button"
              onClick={props.onResume}
              style={styles.resumeFooter}
              title="Resume game"
            >
              ▶ Resume Game
            </button>
            {props.onShare && (
              <button
                type="button"
                onClick={props.onShare}
                style={{ ...styles.resumeFooter, background: 'rgba(255,255,255,0.12)', color: 'var(--text)' }}
                title="Copy share link for friends/guests"
              >
                🔗 Share Match
              </button>
            )}
            {props.online && <button onClick={props.onLeave}>Leave match</button>}
            <button onClick={props.onExitToMenu}>Exit to menu</button>
          </span>
        </div>
      </div>
    </div>
  );
}

// ---- styles -----------------------------------------------------------------
//
// Inline, like the rest of this pane. The row/heading/keycap styles the panels
// share live in `menu-panels.tsx`; what is here is only this menu's own frame.

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'absolute',
    inset: 0,
    zIndex: 60,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(8,10,14,0.78)',
    backdropFilter: 'blur(2px)',
    cursor: 'pointer',
  },
  sheet: {
    width: 'min(700px, 92%)',
    maxHeight: '86%',
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(13,17,23,0.97)',
    border: '1px solid var(--border, #2a2a2a)',
    borderRadius: 8,
    color: 'var(--text)',
    fontSize: '0.82rem',
    boxShadow: '0 18px 50px rgba(0,0,0,0.5)',
    cursor: 'default',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: '0.5rem',
    padding: '0.6rem 0.8rem',
    borderBottom: '1px solid var(--border, #2a2a2a)',
  },
  tabs: { display: 'flex', gap: '0.25rem', marginLeft: 'auto', flexWrap: 'wrap' },
  tab: {
    background: 'transparent',
    border: '1px solid transparent',
    color: 'var(--text-dim)',
    padding: '0.25rem 0.6rem',
    borderRadius: 5,
    cursor: 'pointer',
  },
  tabActive: {
    background: 'rgba(110,168,254,0.14)',
    // The shorthand, not `borderColor`: spread over `tab`, which sets `border`.
    border: '1px solid var(--accent, #6ea8fe)',
    color: 'var(--text)',
  },
  resume: {
    marginLeft: '0.4rem',
    flexShrink: 0,
    background: 'var(--accent, #6ea8fe)',
    color: '#ffffff',
    border: 'none',
    padding: '0.3rem 0.85rem',
    borderRadius: 5,
    fontWeight: 700,
    cursor: 'pointer',
    fontSize: '0.82rem',
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.35rem',
    boxShadow: '0 2px 8px rgba(110, 168, 254, 0.35)',
  },
  resumeFooter: {
    background: 'var(--accent, #6ea8fe)',
    color: '#ffffff',
    border: 'none',
    padding: '0.3rem 0.85rem',
    borderRadius: 5,
    fontWeight: 700,
    cursor: 'pointer',
    fontSize: '0.78rem',
    display: 'inline-flex',
    alignItems: 'center',
    gap: '0.35rem',
  },
  body: { padding: '0.7rem 0.8rem', overflowY: 'auto', minHeight: 0 },
  footer: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.6rem',
    padding: '0.5rem 0.8rem',
    borderTop: '1px solid var(--border, #2a2a2a)',
    color: 'var(--text-dim)',
    fontSize: '0.74rem',
  },
  // Re-exported so a reader of this file can see the panels use the same rows.
  dim: panel.dim,
};
