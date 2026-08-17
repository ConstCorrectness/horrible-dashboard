/**
 * The main menu: the game's front door, and where Escape → *Exit to menu* returns.
 *
 * Replaces what used to be a single **Deploy** button. That button was fine while
 * the only thing to do was walk around a map, but the game now has matches on other
 * people's machines, bots, a friends roster and four screens of settings — and a
 * game that drops you straight into a level has nowhere to put any of it. So this
 * is a real menu: pick a map, start something, find something, or change how it
 * plays.
 *
 * It renders **over the live scene**, like the sign-in screen does: the map behind
 * it is the one you are about to play, still orbiting (see `backdrop.ts`). That is
 * the whole reason the boot sequence finishes the world *before* asking anything —
 * the menu is a layer on a game, not a screen in front of one.
 *
 * Layout is a section list on the left and the section's panel on the right, which
 * is what every game of this kind does, and for a good reason: the actions people
 * came for stay one click deep no matter how much settings grow.
 *
 * See docs/modules/hassault.mdx.
 */
import { useState, type CSSProperties } from 'react';

import type { Invitee, MapSummary, MatchInvite, SessionInfo } from './api';
import { launchNativeFps } from './api';
import { describeControls, type Bindings } from './controls';
import {
  ControlsPanel,
  FriendsPanel,
  ServerBrowserPanel,
  SettingsPanel,
  styles as panel,
} from './menu-panels';
import { ArmoryMarketplace } from './panels/ArmoryMarketplace';
import type { MatchPeer } from './session';

export type MenuSection = 'play' | 'armory' | 'servers' | 'friends' | 'settings' | 'controls';

const SECTIONS: { id: MenuSection; label: string; hint: string }[] = [
  { id: 'play', label: 'Play', hint: 'Pick a map and start' },
  { id: 'armory', label: 'Armory & Skins', hint: 'CS-style float wear, rare drops & trade-up contracts' },
  { id: 'servers', label: 'Servers', hint: 'Matches here, on the LAN, and on friends’ machines' },
  { id: 'friends', label: 'Friends', hint: 'Who is about, and who invited you' },
  { id: 'settings', label: 'Settings', hint: 'Sensitivity, view, sound' },
  { id: 'controls', label: 'Controls', hint: 'Rebind every key' },
];

export interface MainMenuProps {
  account: SessionInfo | null;
  maps: MapSummary[];
  mapName: string;
  onMapName: (name: string) => void;
  controls: Bindings;
  onControls: (next: Bindings) => void;
  /** Who is in the match we are in, if we are in one. */
  peers: MatchPeer[];
  playerId: string;
  room: string;
  /** In a match we are hosting — the only state an invite makes sense from. */
  hosting: boolean;
  online: boolean;
  invitees: Invitee[];
  invites: MatchInvite[];
  botSkill: string;
  onBotSkill: (skill: string) => void;
  /** Enter the world alone: no match and no server, against static dummies. */
  onTrain: () => void;
  /** Host a match on `mapName` and enter it, optionally with bots already in. */
  onHost: (bots: number) => void;
  /** Join a match wherever it is running, and enter the world. */
  onJoin: (room: string, map: string, host: string) => void;
  onInvite: (friendCode: string) => void;
  onDismissInvite: (room: string) => void;
  /** Whether a map is loaded and playable at all. */
  ready: boolean;
  error: string | null;
  /**
   * Why the weapon list is missing, when it is missing.
   *
   * Its own field rather than folded into `error`, because it is a different
   * severity: the map still loads and the movement still works, but nothing can
   * shoot. Hosting is blocked on it — a match nobody can fire in wastes
   * everyone's time, not just yours — while training stays open, because
   * practising the movement without a gun is still practising the movement.
   */
  loadoutError: string;
}

export function MainMenu(props: MainMenuProps) {
  const [section, setSection] = useState<MenuSection>('play');

  return (
    <div style={sheet}>
      <div style={header}>
        <div>
          <div style={wordmark}>
            Horrible<span style={{ color: 'var(--accent, #6ea8fe)' }}>Assault</span>
          </div>
          <div style={{ ...panel.dim, fontFamily: MONO, letterSpacing: '0.12em' }}>
            {props.account?.callsign ?? '—'}
            {props.online && <span style={panel.badge}>in a match</span>}
          </div>
        </div>
      </div>

      <div style={body}>
        <nav style={nav} aria-label="Main menu">
          {SECTIONS.map((entry) => (
            <button
              key={entry.id}
              onClick={() => setSection(entry.id)}
              style={{ ...navItem, ...(section === entry.id ? navItemActive : null) }}
            >
              <span style={{ fontSize: '0.9rem' }}>{entry.label}</span>
              <span style={{ ...panel.dim, fontSize: '0.68rem' }}>{entry.hint}</span>
            </button>
          ))}
        </nav>

        <div style={content}>
          {props.error && <div style={panel.error}>{props.error}</div>}
          {props.loadoutError && (
            <div style={panel.error}>
              No weapons loaded — nothing will fire. {props.loadoutError}
            </div>
          )}
          {section === 'play' && <PlaySection {...props} />}
          {section === 'armory' && <ArmoryMarketplace />}
          {section === 'servers' && (
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
          {section === 'friends' && (
            <FriendsPanel
              invitees={props.invitees}
              invites={props.invites}
              hosting={props.hosting}
              onInvite={props.onInvite}
              onAccept={(invite) => props.onJoin(invite.room, invite.map, invite.host)}
              onDismiss={props.onDismissInvite}
            />
          )}
          {section === 'settings' && <SettingsPanel />}
          {section === 'controls' && (
            <ControlsPanel bindings={props.controls} onChange={props.onControls} />
          )}
        </div>
      </div>

      <div style={footer}>{describeControls(props.controls)} · Esc opens the pause menu</div>
    </div>
  );
}

// ---- play -------------------------------------------------------------------

/** Bot counts offered. Not a number input: nobody wants to type "3". */
const BOT_COUNTS = [0, 1, 3, 5, 7];

/**
 * The two things people came here to do, and the map they will do them on.
 *
 * **Train** is deliberately first and deliberately not a match: it is one player on
 * a map with nothing to shoot, which is what learning the movement wants. The
 * chained-jump timing and the shoot-jump are the sort of thing you practise alone
 * before you try them with somebody aiming at you.
 */
function PlaySection(props: MainMenuProps) {
  const [bots, setBots] = useState(3);
  const [launchingNative, setLaunchingNative] = useState(false);
  const [nativeStatus, setNativeStatus] = useState<string | null>(null);
  const bundled = props.maps.filter((m) => m.source === 'bundled');
  const installed = props.maps.filter((m) => m.source !== 'bundled');
  const chosen = props.maps.find((m) => m.name === props.mapName);

  return (
    <div>
      <h4 style={panel.heading}>Map</h4>
      <select
        value={props.mapName}
        onChange={(e) => props.onMapName(e.target.value)}
        style={{ width: '100%', padding: '0.4rem' }}
        aria-label="Map"
      >
        {/* Grouped so it is obvious which maps ship with the app and which came
            from your own AssaultCube — they are different in kind, not just in
            name. The second group is absent without an install. */}
        <optgroup label={`Bundled (${bundled.length})`}>
          {bundled.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </optgroup>
        {installed.length > 0 && (
          <optgroup label={`AssaultCube (${installed.length})`}>
            {installed.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      {chosen && (
        <div style={{ ...panel.dim, marginTop: '0.3rem' }}>
          {/* `size` is the map *file's* byte count, not a grid dimension — the grid
              size only arrives with `MapInfo`, one request later. Saying "cubes"
              here would be confidently wrong. */}
          {(chosen.size / 1024).toFixed(0)} KB ·{' '}
          {chosen.source === 'bundled'
            ? 'ships with the app'
            : `from your AssaultCube install (${chosen.source})`}
        </div>
      )}

      <h4 style={panel.heading}>Start</h4>
      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Train</span>
          <span style={panel.dim}>
            Alone on the map, nothing shooting back. Where you learn the chained jump and the
            shoot-jump before anyone is watching.
          </span>
        </div>
        <button onClick={props.onTrain} disabled={!props.ready} style={primary}>
          Enter
        </button>
      </div>

      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Host a match</span>
          <span style={panel.dim}>
            Runs on this machine. Friends see it in their own server browser and you can invite them
            from the Friends section.
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <select
            value={bots}
            onChange={(e) => setBots(Number(e.target.value))}
            aria-label="Bots"
            style={{ width: 92 }}
          >
            {BOT_COUNTS.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? 'no bots' : `${n} bot${n === 1 ? '' : 's'}`}
              </option>
            ))}
          </select>
          <select
            value={props.botSkill}
            onChange={(e) => props.onBotSkill(e.target.value)}
            aria-label="Bot skill"
            style={{ width: 84 }}
            disabled={bots === 0}
          >
            <option value="easy">easy</option>
            <option value="normal">normal</option>
            <option value="hard">hard</option>
          </select>
          <button
            onClick={() => props.onHost(bots)}
            disabled={!props.ready || props.loadoutError !== ''}
            title={props.loadoutError ? 'No loadout — nothing would be able to fire' : undefined}
            style={primary}
          >
            Host
          </button>
        </div>
      </div>

      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#38bdf8', fontWeight: 700 }}>
            ⚡ Native High-Performance Client
          </span>
          <span style={panel.dim}>
            Launch the native C++ / Vulkan client with 1,000Hz+ Raw Input (`WM_INPUT`), 240+ FPS, and sub-tick UDP networking directly to this match server.
          </span>
          {nativeStatus && (
            <span style={{ fontSize: '0.72rem', color: nativeStatus.includes('PID') ? '#4ade80' : '#f87171', marginTop: '0.2rem' }}>
              {nativeStatus}
            </span>
          )}
        </div>
        <button
          onClick={async () => {
            setLaunchingNative(true);
            setNativeStatus(null);
            try {
              const res = await launchNativeFps({
                room_id: props.room || 'session_host',
                map_name: props.mapName,
                callsign: props.account?.callsign || undefined,
                raw_input: true,
                max_fps: 240,
              });
              setNativeStatus(res.message || (res.launched ? 'Launched!' : 'Binary not found'));
            } catch (err) {
              setNativeStatus(err instanceof Error ? err.message : 'Launch failed');
            } finally {
              setLaunchingNative(false);
            }
          }}
          disabled={!props.ready || launchingNative}
          style={{ ...primary, background: '#0284c7', borderColor: '#38bdf8' }}
        >
          {launchingNative ? 'Launching…' : '⚡ Launch Native FPS'}
        </button>
      </div>

      <h4 style={panel.heading}>Tactical Equipment</h4>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '0.4rem', marginBottom: '0.6rem' }}>
        <div style={{ background: 'var(--bg-tertiary, #161b22)', padding: '0.4rem 0.6rem', borderRadius: 4, border: '1px solid var(--border-dim, #30363d)' }}>
          <div style={{ fontWeight: 700, fontSize: '0.78rem' }}>💨 Smoke Grenade</div>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>6.5c radius · 16s duration</div>
        </div>
        <div style={{ background: 'var(--bg-tertiary, #161b22)', padding: '0.4rem 0.6rem', borderRadius: 4, border: '1px solid var(--border-dim, #30363d)' }}>
          <div style={{ fontWeight: 700, fontSize: '0.78rem' }}>⚡ Flashbang</div>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>24c radius · 4.5s blind</div>
        </div>
        <div style={{ background: 'var(--bg-tertiary, #161b22)', padding: '0.4rem 0.6rem', borderRadius: 4, border: '1px solid var(--border-dim, #30363d)' }}>
          <div style={{ fontWeight: 700, fontSize: '0.78rem' }}>💣 HE Frag Grenade</div>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>8.5c radius · 105 max dmg</div>
        </div>
      </div>

      {props.invites.length > 0 && (
        <>
          <h4 style={panel.heading}>Waiting for you</h4>
          {props.invites.map((invite) => (
            <div key={invite.room} style={panel.row}>
              <div style={panel.rowMain}>
                <span>
                  <strong>{invite.hostName}</strong> invited you to <code>{invite.map}</code>
                </span>
              </div>
              <button
                onClick={() => props.onJoin(invite.room, invite.map, invite.host)}
                style={primary}
              >
                Join
              </button>
            </div>
          ))}
        </>
      )}

      <h4 style={panel.heading}>The movement</h4>
      <div style={panel.dim}>
        Momentum decides where a jump lands, not the keys — air control is deliberately weak. Land
        and jump again inside a quarter second <em>while strafing</em> and you keep 25% more speed,
        capped at 125%; miss the timing and you keep none of it. Crouching is silent and steadies a
        shot for 40% of your speed, and crouching once already airborne costs nothing at all. Firing
        shoves you opposite your aim, so a shotgun at the floor is a second jump — and a long drop
        costs health on the way down.
      </div>
    </div>
  );
}

// ---- styles -----------------------------------------------------------------

const MONO = 'var(--font-mono, "JetBrains Mono", Consolas, monospace)';
const HAIR = 'rgba(150,160,190,.22)';

const sheet: CSSProperties = {
  width: 'min(820px, 94%)',
  maxHeight: '92%',
  display: 'flex',
  flexDirection: 'column',
  borderRadius: 10,
  border: `1px solid ${HAIR}`,
  // Blurred rather than opaque, for the same reason the sign-in card is: the map
  // orbiting behind this is the point of rendering the menu over a live scene.
  background: 'rgba(5,6,9,.7)',
  backdropFilter: 'blur(12px) saturate(1.1)',
  WebkitBackdropFilter: 'blur(12px) saturate(1.1)',
  boxShadow: '0 22px 60px rgba(0,0,0,.5)',
  color: 'var(--text, #e8eaf2)',
  fontSize: '0.82rem',
  overflow: 'hidden',
};

const header: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '0.9rem 1.1rem',
  borderBottom: `1px solid ${HAIR}`,
};

const wordmark: CSSProperties = {
  fontSize: '1.1rem',
  fontWeight: 700,
  letterSpacing: '0.2em',
  textTransform: 'uppercase',
};

const body: CSSProperties = { display: 'flex', minHeight: 0, flex: 1 };

const nav: CSSProperties = {
  width: 190,
  flexShrink: 0,
  display: 'flex',
  flexDirection: 'column',
  gap: '0.15rem',
  padding: '0.7rem',
  borderRight: `1px solid ${HAIR}`,
};

const navItem: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'flex-start',
  gap: 1,
  textAlign: 'left',
  padding: '0.45rem 0.55rem',
  borderRadius: 6,
  border: '1px solid transparent',
  background: 'transparent',
  color: 'var(--text)',
  cursor: 'pointer',
};

const navItemActive: CSSProperties = {
  background: 'rgba(110,168,254,0.14)',
  // The full shorthand, not `borderColor`: these objects are spread over
  // `navItem`, which sets `border`, and React warns (rightly) about mixing a
  // shorthand with one of its longhands in the same computed style.
  border: '1px solid var(--accent, #6ea8fe)',
};

const content: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflowY: 'auto',
  padding: '0.7rem 1.1rem',
};

const footer: CSSProperties = {
  padding: '0.5rem 1.1rem',
  borderTop: `1px solid ${HAIR}`,
  color: 'var(--text-dim)',
  fontSize: '0.72rem',
};

const primary: CSSProperties = {
  background: 'var(--accent, #6ea8fe)',
  border: '1px solid transparent',
  color: '#08111f',
  fontWeight: 600,
  padding: '0.35rem 0.9rem',
  borderRadius: 5,
  cursor: 'pointer',
};
