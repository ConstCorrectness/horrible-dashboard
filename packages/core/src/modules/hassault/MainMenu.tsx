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

/**
 * Labels only — deliberately no descriptions.
 *
 * A hint under every item is explanation where the reader came for an action, and
 * it is also redundant: the panel one click away *is* the explanation, and it is
 * the accurate one. `GameMenu`'s `TABS` never had hints, so this is the two menus
 * converging rather than a style choice made twice.
 */
const SECTIONS: { id: MenuSection; label: string }[] = [
  { id: 'play', label: 'Play' },
  { id: 'armory', label: 'Armor & Skins' },
  { id: 'servers', label: 'Servers' },
  { id: 'friends', label: 'Friends' },
  { id: 'settings', label: 'Settings' },
  { id: 'controls', label: 'Controls' },
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
  /** Join the best match anyone is running, or host one when there is none. */
  onQuickPlay: (bots: number) => void;
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
  /**
   * Whether Play, Train and Host open the native window instead of playing here.
   *
   * Shown rather than silent: the two clients do not look alike — the native one
   * has no HUD, no weapon model and no sound — so a button that quietly opened a
   * different window would read as the game having broken.
   */
  nativeClient: boolean;
  /** What the last native launch said, success or reason. */
  nativeStatus: string | null;
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
            {props.account?.username ?? '—'}
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
          {section === 'settings' && (
            <>
              <SettingsPanel />
              <NativeClientRow {...props} />
            </>
          )}
          {section === 'controls' && (
            <ControlsPanel bindings={props.controls} onChange={props.onControls} />
          )}
        </div>
      </div>

      <div style={footer}>{describeControls(props.controls)} · Esc opens the pause menu</div>
    </div>
  );
}

// ---- the native client ------------------------------------------------------

/**
 * The experimental native client, and an honest description of it.
 *
 * It used to sit in **Play**, next to Train and Host, advertising "native C++ /
 * Vulkan, 1,000Hz+ raw input, sub-tick UDP networking directly to this match
 * server", while being a software framebuffer walking a hardcoded 16×16 grid with
 * no map loading and no networking at all.
 *
 * It is now real as far as it goes, and this copy tracks exactly how far: same
 * maps, same matches, a `wgpu` renderer, raw mouse input and client-side
 * prediction — and no HUD, no weapon view model and no sound. A row that
 * overstated it once has to keep understating it: somebody who launches this
 * expecting the finished client files a bug against the game, not the stage.
 *
 * See docs/modules/hassault.mdx.
 */
function NativeClientRow(props: MainMenuProps) {
  const [launching, setLaunching] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  return (
    <div style={{ marginTop: '1rem' }}>
      <h4 style={panel.heading}>Native client (experimental)</h4>
      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Launch the native client</span>
          <span style={panel.dim}>
            A separate window: same maps, same match, rendered on the GPU with raw mouse input and
            no frame cap. It has no HUD, no weapon model and no sound yet, so this pane is still
            the complete game.
          </span>
          {status && (
            <span
              style={{
                fontSize: '0.72rem',
                // A pid is the one unambiguous signal it really started; anything
                // else the route returns is a reason it didn't.
                color: status.includes('PID')
                  ? 'var(--success, #4ade80)'
                  : 'var(--danger, #f87171)',
                marginTop: '0.2rem',
              }}
            >
              {status}
            </span>
          )}
        </div>
        <button
          onClick={async () => {
            setLaunching(true);
            setStatus(null);
            try {
              const res = await launchNativeFps({
                // The room we are actually in, or none — the client asks the node
                // for a match on this map. `'session_host'` used to go here, which
                // named no room that has ever existed.
                // Explicitly a join, because this row is "open it once and see",
                // not "change how I play". Train and Host reach the same route
                // with their own mode when the setting above is on.
                mode: 'join',
                room_id: props.room,
                map_name: props.mapName,
                username: props.account?.username || undefined,
                max_fps: 240,
              });
              setStatus(res.message || (res.launched ? 'Launched' : 'Binary not found'));
            } catch (err) {
              setStatus(err instanceof Error ? err.message : 'Launch failed');
            } finally {
              setLaunching(false);
            }
          }}
          disabled={launching}
        >
          {launching ? 'Launching…' : 'Launch'}
        </button>
      </div>
    </div>
  );
}

// ---- play -------------------------------------------------------------------

/** Bot counts offered. Not a number input: nobody wants to type "3". */
const BOT_COUNTS = [0, 1, 3, 5, 7];

/**
 * The things people came here to do, and the map they will do them on.
 *
 * **Train** is deliberately not a match: it is one player on a map with static
 * dummies, which is what learning the movement wants. The chained-jump timing and
 * the shoot-jump are the sort of thing you practise alone.
 *
 * Every row here is a label and its controls, with **no prose underneath**. What a
 * row does is either obvious from its verb or discovered by pressing it, and a
 * paragraph explaining the momentum system to somebody standing at the door is
 * advertising copy in the wrong building. It is not lost — it lives in
 * docs/modules/hassault.mdx, where a reader is actually reading.
 */
function PlaySection(props: MainMenuProps) {
  const [bots, setBots] = useState(3);
  const bundled = props.maps.filter((m) => m.source === 'bundled');
  const installed = props.maps.filter((m) => m.source !== 'bundled');
  const chosen = props.maps.find((m) => m.name === props.mapName);

  return (
    <div>
      {props.nativeClient && (
        <div style={panel.notice}>
          Playing in the <strong>native client</strong> — Play, Train and Host open a separate
          window. No HUD, no weapon model and no sound yet. Turn it off in Settings.
        </div>
      )}
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
      {/* First, and the only one with an accent, because it is the answer to the
          question most people arrive with. Everything below it is a way of being
          specific about something this decides for you. */}
      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Quick play</span>
        </div>
        <button
          onClick={() => props.onQuickPlay(bots)}
          disabled={!props.ready || props.loadoutError !== ''}
          title={props.loadoutError ? 'No loadout — nothing would be able to fire' : undefined}
          style={primary}
        >
          Play
        </button>
      </div>

      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Train</span>
        </div>
        {/* Plain, not accented: exactly one thing on this screen should look
            like the thing to press, and Quick play is it. */}
        <button onClick={props.onTrain} disabled={!props.ready}>
          Enter
        </button>
      </div>

      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Host a match</span>
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
          >
            Host
          </button>
        </div>
      </div>

      {props.nativeStatus && (
        <div
          style={{
            ...panel.dim,
            marginTop: '0.5rem',
            // A pid is the one unambiguous signal it really started; everything
            // else the route returns is a reason it did not.
            color: props.nativeStatus.includes('PID')
              ? 'var(--success, #4ade80)'
              : 'var(--danger, #f87171)',
          }}
        >
          {props.nativeStatus}
        </div>
      )}

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
  alignItems: 'center',
  textAlign: 'left',
  padding: '0.5rem 0.55rem',
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
