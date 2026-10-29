/**
 * The panels the main menu and the pause menu both show.
 *
 * They exist as one file because they are one thing. A game's settings screen and
 * its *paused* settings screen differ only in what surrounds them, and two copies
 * would drift — one gaining a slider the other never got. So the panels live here
 * and the two menus compose them: `MainMenu` in front of the orbiting map,
 * `GameMenu` over the frozen world.
 *
 * Styling is inline, like the rest of this pane: it is one self-contained document
 * view with no stylesheet of its own, and adding one for menus that only exist over
 * a WebGL canvas would put their appearance two files away from their markup.
 *
 * See docs/modules/hassault.mdx.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { isSettingOverridden, setSetting, useSetting } from '../../settings';
import { addFriend, searchDirectory, type DirectoryEntry } from '../social/api';
import { browseServers, ONLINE_HOST, type BrowseMatch, type MapSummary } from './api';
import {
  ACTIONS,
  CONTROL_GROUPS,
  SLOTS,
  boundTo,
  defaultControls,
  isDefaultControls,
  keyLabel,
  setBinding,
  type Bindings,
  type GameAction,
} from './controls';
import type { MatchInvite, Invitee } from './api';
import {
  deviceHints,
  effectiveSpec,
  GRAPHICS_CHOICES,
  GRAPHICS_OPTIONS,
  resolveTier,
  setGraphicsOverrides,
  useGraphicsChoice,
  useGraphicsOverrides,
  withOverride,
  type GraphicsOverrides,
  type GraphicsTier,
  type TierSpec,
} from './graphics';
import {
  CROSSHAIR_KEYS,
  COLOR_NAMES,
  CUSTOM,
  DEFAULT_SPEC,
  NAMED_COLORS,
  PRESETS,
  STYLES,
  decodeCrosshair,
  encodeCrosshair,
  hexRgb,
  hudUnit,
  coneGapPx,
  rgbHex,
  settingsFromSpec,
  type CrosshairSpec,
  type CrosshairStyle,
  type DecodeFailure,
} from './crosshair';
import { CrosshairShapes, useCrosshairSpec } from './crosshair-view';
import { VoiceCommsPanel } from './panels/VoiceCommsPanel';
import { RADAR_DEFAULTS, RADAR_KEYS, readRadarSettings } from './radar-view';
import type { MatchPeer } from './session';

/** How often the browser re-asks while it is the visible panel. */
const BROWSE_INTERVAL_MS = 6000;

/**
 * Whether this node answers LAN discovery.
 *
 * Owned by the network module, not by this game — the fabric is what finds peers,
 * and a second discovery mechanism for one pane would be a second thing to debug.
 * Surfaced here because "play on LAN" is a thing people look for in a game's menu
 * and not in a network settings page.
 */
const LAN_KEY = 'network.enableLanDiscovery';

export const SENSITIVITY_KEY = 'hassault.sensitivity';
export const CONTROLS_KEY = 'hassault.controls';
export const VOLUME_KEY = 'hassault.volume';
/**
 * Field of view: one row for both clients (`settings::KEY_FOV` natively), 60–110.
 * The pane used to keep its own `hassault.fov`; `useFov` still reads that when
 * this row has never been saved, and saves it here the first time it does.
 */
export const FOV_KEY = 'hassault.video.fov';
export const LEGACY_FOV_KEY = 'hassault.fov';
/** The HUD scale, shared with the native client: the crosshair's unit uses it. */
export const HUD_SCALE_KEY = 'hassault.video.hudScale';
export const CROUCH_TOGGLE_KEY = 'hassault.crouchToggle';
/** Lobby voice: closed mic except while the Push to talk key is held. */
export const PUSH_TO_TALK_KEY = 'hassault.voice.pushToTalk';

/**
 * Whether Play, Train and Host open the native window instead of playing here.
 *
 * A setting rather than a per-press choice: which client you play in is a
 * standing preference, and putting it on every button would put a decision in
 * front of somebody who wanted to press Play. On by default: the native client
 * has its own HUD, weapon model, sound and scoreboard now, and it is the one
 * with raw mouse input and no frame cap.
 */
export const NATIVE_CLIENT_KEY = 'hassault.nativeClient';

/**
 * Draw the served hitbox around every body.
 *
 * Shared with the native client (`settings::KEY_SHOW_HITBOXES`), which is the
 * point of it being a node setting rather than a flag in either client: the
 * question it answers — "is what I am shooting at where it is drawn?" — has to
 * be askable in whichever one you are playing, and the answers have to be
 * comparable.
 *
 * Off by default. A permanent wireframe is a worse picture of the game than the
 * game is.
 */
export const SHOW_HITBOXES_KEY = 'hassault.debug.hitboxes';

/**
 * Draw the predicted flight path while a grenade is out.
 *
 * Off by default, and even when on it only draws in Training and in a room you
 * are hosting yourself — never ranked, never someone else's server. It is a
 * practice aid for learning lineups, and a lineup read off a drawn line in a
 * real match is not one the player knows. Shared with the native client
 * (`settings::KEY_GRENADE_ARC`).
 */
export const GRENADE_ARC_KEY = 'hassault.debug.grenadeArc';

/** Where the grenade arc may draw: Training (not joined) or our own hosted room. */
export function grenadeArcAllowed(joined: boolean, host: string, ranked: boolean): boolean {
  return !joined || (host === '' && !ranked);
}

// ---- settings ---------------------------------------------------------------

const SENS_MIN = 0.1;
const SENS_MAX = 4;
/**
 * The FOV range, in degrees (vertical, before the scope divides it). The native
 * client's `FOV_RANGE` is the same pair and `browser_parity.rs` pins them: a
 * wider ceiling on one client is a wider view of the map for that player.
 */
export const FOV_MIN = 60;
export const FOV_MAX = 110;

export function clampFov(value: unknown): number {
  const v = typeof value === 'number' && Number.isFinite(value) ? value : 75;
  return Math.max(FOV_MIN, Math.min(FOV_MAX, v));
}

/** The shared row when it was saved, else the pane's old row, else the default. */
export function resolveFov(current: unknown, saved: boolean, legacy: unknown): number {
  if (!saved && typeof legacy === 'number' && Number.isFinite(legacy)) return clampFov(legacy);
  return clampFov(current);
}

/** The field of view, migrating the pane's old `hassault.fov` the first time it is read. */
export function useFov(): number {
  const current = useSetting<number>(FOV_KEY);
  const legacy = useSetting<number>(LEGACY_FOV_KEY);
  const saved = isSettingOverridden(FOV_KEY);
  const fov = resolveFov(current, saved, legacy);
  useEffect(() => {
    if (!saved && typeof legacy === 'number' && Number.isFinite(legacy)) {
      void setSetting(FOV_KEY, clampFov(legacy)).catch(() => {});
    }
  }, [saved, legacy]);
  return fov;
}

/**
 * A multiplier on the map's exposure, shared with the native client — the same
 * key, so on the desktop one slider moves both. The same range as its
 * `BRIGHTNESS_RANGE`: bounded, because a brightness that could turn every dark
 * corner to daylight would be a setting that wins gunfights.
 */
export const BRIGHTNESS_KEY = 'hassault.video.brightness';
export const BRIGHTNESS_MIN = 0.6;
export const BRIGHTNESS_MAX = 1.6;

/** A stored brightness, clamped to the shared range. */
export function clampBrightness(value: number | undefined): number {
  const v = typeof value === 'number' && Number.isFinite(value) ? value : 1;
  return Math.max(BRIGHTNESS_MIN, Math.min(BRIGHTNESS_MAX, v));
}

function Slider({
  label,
  hint,
  value,
  min,
  max,
  step,
  suffix,
  fallback,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix: string;
  fallback: number;
  onChange: (v: number) => void;
}) {
  return (
    <div style={styles.row}>
      <div style={styles.rowMain}>
        <span>{label}</span>
        <span style={styles.dim}>{hint}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{ width: 170 }}
          aria-label={label}
        />
        <code style={{ width: 48, textAlign: 'right' }}>
          {step < 1 ? value.toFixed(2) : Math.round(value)}
          {suffix}
        </code>
        <button onClick={() => onChange(fallback)} disabled={value === fallback}>
          Reset
        </button>
      </div>
    </div>
  );
}

/**
 * Sensitivity, field of view, volume, and how crouch behaves.
 *
 * Every one is written straight through to the app's settings store, so it survives
 * a reload and shows the same value on the settings page. The control *map* is
 * deliberately not there — it is a JSON document rather than a scalar, and the
 * settings page renders scalars — which is why `ControlsPanel` is its only editor.
 */
type SurfaceChoice = 'simple' | 'standard' | 'photo';

/** The two surface rows as the one choice a player actually makes. */
function surfaceOf(spec: TierSpec): SurfaceChoice {
  if (spec.lambert) return 'simple';
  return spec.photos ? 'photo' : 'standard';
}

const SURFACE_ROWS: Record<SurfaceChoice, Pick<TierSpec, 'lambert' | 'photos'>> = {
  simple: { lambert: true, photos: false },
  standard: { lambert: false, photos: false },
  photo: { lambert: false, photos: true },
};

/** One row of choices, highlighting the current one. */
function ChoiceRow<T extends string | number | boolean>({
  label,
  hint,
  value,
  options,
  onPick,
}: {
  label: string;
  hint: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onPick: (value: T) => void;
}) {
  return (
    <div style={styles.row}>
      <div style={styles.rowMain}>
        <span>{label}</span>
        <span style={styles.dim}>{hint}</span>
      </div>
      <div style={{ display: 'flex', gap: '0.25rem', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        {options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={value === option.value}
            onClick={() => onPick(option.value)}
            style={{ ...styles.choice, ...(value === option.value ? styles.choiceActive : null) }}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const ON_OFF = [
  { value: true, label: 'On' },
  { value: false, label: 'Off' },
] as const;

/**
 * The graphics level, and every row under it as its own setting.
 *
 * A level is a **preset**, as in the native client's video page: picking one
 * sets every row, and a row changed afterwards is kept as an override, with the
 * level shown as `Custom`. Moving a row back to the level's own value drops the
 * override again. See `graphics.ts`.
 *
 * Nothing here can change what a player can see — the fog, smoke, flashes and
 * how players are lit are the same at every setting — and the rows say so, so
 * nobody goes looking for the one that does.
 */
export function GraphicsRows() {
  const [choice, setChoice] = useGraphicsChoice();
  const overrides = useGraphicsOverrides();
  const [open, setOpen] = useState(false);
  const tier: GraphicsTier = resolveTier(choice, deviceHints());
  const spec = effectiveSpec(tier, overrides);
  const custom = Object.keys(overrides).length > 0;

  const set = <K extends keyof TierSpec>(key: K, value: TierSpec[K]) =>
    setGraphicsOverrides(withOverride(overrides, tier, key, value));
  const setSurfaces = (surface: SurfaceChoice) => {
    let next: GraphicsOverrides = overrides;
    for (const [key, value] of Object.entries(SURFACE_ROWS[surface]) as [
      'lambert' | 'photos',
      boolean,
    ][]) {
      next = withOverride(next, tier, key, value);
    }
    setGraphicsOverrides(next);
  };
  const numbers = <K extends keyof typeof GRAPHICS_OPTIONS>(
    key: K,
    label: (v: number) => string,
  ) => GRAPHICS_OPTIONS[key].map((v) => ({ value: v as number, label: label(v) }));

  return (
    <>
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>
            Graphics
            {custom && <span style={styles.badge}>CUSTOM</span>}
          </span>
          <span style={styles.dim}>
            {choice === 'auto' ? `Auto picked ${tier}. ` : ''}A level sets every row below; change
            a row and it is kept on top. Nothing here changes what you can see — fog, smoke,
            flashes and how players are lit are the same at every setting.
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {GRAPHICS_CHOICES.map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={!custom && choice === c}
              onClick={() => setChoice(c)}
              style={{
                ...styles.choice,
                ...(!custom && choice === c ? styles.choiceActive : null),
              }}
            >
              {c === 'auto' ? 'Auto' : c[0].toUpperCase() + c.slice(1)}
            </button>
          ))}
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
            style={{ ...styles.choice, ...(open ? styles.choiceActive : null) }}
          >
            {open ? 'Hide rows' : 'Advanced'}
          </button>
        </div>
      </div>
      {open && (
        <>
          <ChoiceRow
            label="Resolution"
            hint="A cap on the screen's pixel ratio. The biggest single cost on a HiDPI laptop."
            value={spec.pixelRatio}
            options={numbers('pixelRatio', (v) => `${v}×`)}
            onPick={(v) => set('pixelRatio', v)}
          />
          <ChoiceRow
            label="Anti-aliasing"
            hint="Smooths edges. Applies the next time the game loads."
            value={spec.antialias}
            options={ON_OFF}
            onPick={(v) => set('antialias', v)}
          />
          <ChoiceRow
            label="Surfaces"
            hint="Simple is flat-shaded and cheapest; Photographed downloads real material scans."
            value={surfaceOf(spec)}
            options={[
              { value: 'simple', label: 'Simple' },
              { value: 'standard', label: 'Standard' },
              { value: 'photo', label: 'Photographed' },
            ]}
            onPick={setSurfaces}
          />
          <ChoiceRow
            label="Texture filtering"
            hint="Keeps floors sharp at a glancing angle."
            value={spec.anisotropy}
            options={numbers('anisotropy', (v) => (v === 1 ? 'Off' : `${v}×`))}
            onPick={(v) => set('anisotropy', v)}
          />
          <ChoiceRow
            label="Shadow map"
            hint="Resolution of the sun's shadow. Shadows are always on: who is standing in shade is information."
            value={spec.shadowSize}
            options={numbers('shadowSize', (v) => `${v}`)}
            onPick={(v) => set('shadowSize', v)}
          />
          <ChoiceRow
            label="Soft shadows"
            hint="Filtered shadow edges."
            value={spec.softShadows}
            options={ON_OFF}
            onPick={(v) => set('softShadows', v)}
          />
          <ChoiceRow
            label="Map lamps"
            hint="How many of the nearest lamps light the scene. Players are never darker than their light floor."
            value={spec.lights}
            options={numbers('lights', (v) => (v === 0 ? 'Off' : `${v}`))}
            onPick={(v) => set('lights', v)}
          />
          <ChoiceRow
            label="Sky"
            hint="The sky dome on open maps; off draws the flat horizon colour."
            value={spec.sky}
            options={ON_OFF}
            onPick={(v) => set('sky', v)}
          />
        </>
      )}
    </>
  );
}

/**
 * `standalone` is the browser build, which shows none of the last three rows:
 * there is no native client to choose, and hitboxes and the grenade arc are the
 * room's call there — a hitbox overlay one side sees and the other doesn't is an
 * unequal match — so they are host commands in the console
 * (`server.show_hitboxes`, `server.grenade_trajectory`). On the desktop they stay
 * node settings, shared with the native client.
 */
export function SettingsPanel({ standalone = false }: { standalone?: boolean } = {}) {
  const sensitivity = useSetting<number>(SENSITIVITY_KEY) ?? 1;
  const fov = useFov();
  const brightness = clampBrightness(useSetting<number>(BRIGHTNESS_KEY));
  const volume = useSetting<number>(VOLUME_KEY) ?? 0.7;
  const crouchToggle = useSetting<boolean>(CROUCH_TOGGLE_KEY) ?? false;
  const nativeClient = useSetting<boolean>(NATIVE_CLIENT_KEY) ?? true;
  const showHitboxes = useSetting<boolean>(SHOW_HITBOXES_KEY) ?? false;
  const grenadeArc = useSetting<boolean>(GRENADE_ARC_KEY) ?? false;

  return (
    <div style={styles.rows}>
      <GraphicsRows />
      <Slider
        label="Mouse sensitivity"
        hint="Turn per pixel of mouse movement, this game only."
        value={sensitivity}
        min={SENS_MIN}
        max={SENS_MAX}
        step={0.05}
        suffix="×"
        fallback={1}
        onChange={(v) => void setSetting(SENSITIVITY_KEY, v)}
      />
      <Slider
        label="Field of view"
        hint="Applies immediately, mid-match included."
        value={fov}
        min={FOV_MIN}
        max={FOV_MAX}
        step={1}
        suffix="°"
        fallback={75}
        onChange={(v) => void setSetting(FOV_KEY, v)}
      />
      <Slider
        label="Brightness"
        hint="The map's exposure. Bounded, and the same range as the native client."
        value={brightness}
        min={BRIGHTNESS_MIN}
        max={BRIGHTNESS_MAX}
        step={0.05}
        suffix="×"
        fallback={1}
        onChange={(v) => void setSetting(BRIGHTNESS_KEY, v)}
      />
      <Slider
        label="Volume"
        hint="Footsteps, shots and landings."
        value={volume}
        min={0}
        max={1}
        step={0.05}
        suffix=""
        fallback={0.7}
        onChange={(v) => void setSetting(VOLUME_KEY, v)}
      />
      <RadarRows />
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Crouch</span>
          <span style={styles.dim}>Hold releases the instant you need speed back.</span>
        </div>
        <div style={{ display: 'flex', gap: '0.25rem' }}>
          {[
            { on: false, label: 'Hold' },
            { on: true, label: 'Toggle' },
          ].map((option) => (
            <button
              key={option.label}
              onClick={() => void setSetting(CROUCH_TOGGLE_KEY, option.on)}
              style={{
                ...styles.choice,
                ...(crouchToggle === option.on ? styles.choiceActive : null),
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {!standalone && (
        <>
          <div style={styles.row}>
            <div style={styles.rowMain}>
              <span>Client</span>
              <span style={styles.dim}>
                The native client is where the game is played: its own window, raw mouse input, no
                frame cap. This pane is then setup and spectating. It has to be built first (`cargo
                build --release --manifest-path apps/native-fps/Cargo.toml`); playing in the pane is
                the fallback while it is not.
              </span>
            </div>
            <div style={{ display: 'flex', gap: '0.25rem' }}>
              {[
                { on: false, label: 'This pane' },
                { on: true, label: 'Native' },
              ].map((option) => (
                <button
                  key={option.label}
                  onClick={() => void setSetting(NATIVE_CLIENT_KEY, option.on)}
                  style={{
                    ...styles.choice,
                    ...(nativeClient === option.on ? styles.choiceActive : null),
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div style={styles.row}>
            <div style={styles.rowMain}>
              <span>Show hitboxes</span>
              <span style={styles.dim}>
                Draws the exact volume a shot is resolved against around every body, with the head
                band marked. It is the served spec, not a second copy — which is the point: a body
                drawn a few percent off its hitbox costs you shots you were sure of and looks like
                lag. Works in both clients.
              </span>
            </div>
            <div style={{ display: 'flex', gap: '0.25rem' }}>
              {[
                { on: false, label: 'Off' },
                { on: true, label: 'On' },
              ].map((option) => (
                <button
                  key={option.label}
                  onClick={() => void setSetting(SHOW_HITBOXES_KEY, option.on)}
                  style={{
                    ...styles.choice,
                    ...(showHitboxes === option.on ? styles.choiceActive : null),
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div style={styles.row}>
            <div style={styles.rowMain}>
              <span>Grenade trajectory</span>
              <span style={styles.dim}>
                Draws where a held grenade will fly and land. Practice only: it shows in Training
                and in a match you host yourself, never in ranked or on someone else's server.
              </span>
            </div>
            <div style={{ display: 'flex', gap: '0.25rem' }}>
              {[
                { on: false, label: 'Off' },
                { on: true, label: 'On' },
              ].map((option) => (
                <button
                  key={option.label}
                  onClick={() => void setSetting(GRENADE_ARC_KEY, option.on)}
                  style={{
                    ...styles.choice,
                    ...(grenadeArc === option.on ? styles.choiceActive : null),
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      <div style={{ marginTop: '1rem' }}>
        <VoiceCommsPanel />
      </div>
    </div>
  );
}

/** An Off/On pair, the shape every boolean row on this page uses. */
function OnOff({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div style={{ display: 'flex', gap: '0.25rem' }}>
      {[
        { on: false, label: 'Off' },
        { on: true, label: 'On' },
      ].map((option) => (
        <button
          key={option.label}
          onClick={() => onChange(option.on)}
          style={{ ...styles.choice, ...(value === option.on ? styles.choiceActive : null) }}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The radar, as CS lays it out: `cl_radar_scale`, `cl_hud_radar_scale`,
 * `cl_radar_icon_scale_min`, `cl_radar_always_centered`, `cl_radar_rotate` and
 * `cl_radar_square_with_scoreboard`. The same rows the native menu writes, read
 * through the same clamp (`readRadarSettings`).
 */
function RadarRows() {
  const raw = {
    [RADAR_KEYS.scale]: useSetting<number>(RADAR_KEYS.scale),
    [RADAR_KEYS.hudScale]: useSetting<number>(RADAR_KEYS.hudScale),
    [RADAR_KEYS.iconScale]: useSetting<number>(RADAR_KEYS.iconScale),
    [RADAR_KEYS.centered]: useSetting<boolean>(RADAR_KEYS.centered),
    [RADAR_KEYS.rotate]: useSetting<boolean>(RADAR_KEYS.rotate),
    [RADAR_KEYS.fullOnScores]: useSetting<boolean>(RADAR_KEYS.fullOnScores),
    [RADAR_KEYS.soundPings]: useSetting<boolean>(RADAR_KEYS.soundPings),
  } as Record<string, unknown>;
  const r = readRadarSettings((key) => raw[key]);
  const flags: { key: string; label: string; hint: string; value: boolean }[] = [
    {
      key: RADAR_KEYS.rotate,
      label: 'Rotate with view',
      hint: 'Off keeps north up and turns your arrow instead.',
      value: r.rotate,
    },
    {
      key: RADAR_KEYS.centered,
      label: 'Always centred',
      hint: 'Off stops the map at its own edge, so you see more map near a border.',
      value: r.centered,
    },
    {
      key: RADAR_KEYS.fullOnScores,
      label: 'Whole map with scoreboard',
      hint: 'While Tab is held, the radar shows the entire map, north up.',
      value: r.fullOnScores,
    },
    {
      key: RADAR_KEYS.soundPings,
      label: 'Sound pings',
      hint: 'Faint red blobs for enemy footsteps: direction and roughly how far they sounded. Unsuppressed gunfire is always an exact dot.',
      value: r.soundPings,
    },
  ];
  return (
    <>
      <h4 style={styles.heading}>Radar</h4>
      <Slider
        label="Zoom"
        hint="Lower shows more map. 0.4 is the common pro setting; hold M (radar zoom) for it on demand."
        value={r.scale}
        min={0.3}
        max={1.3}
        step={0.05}
        suffix=""
        fallback={RADAR_DEFAULTS.scale}
        onChange={(v) => void setSetting(RADAR_KEYS.scale, v)}
      />
      <Slider
        label="Size"
        hint="How big the radar is on screen."
        value={r.hudScale}
        min={0.8}
        max={1.3}
        step={0.05}
        suffix="×"
        fallback={RADAR_DEFAULTS.hudScale}
        onChange={(v) => void setSetting(RADAR_KEYS.hudScale, v)}
      />
      <Slider
        label="Icon size"
        hint="The dots for players, gunfire and pings."
        value={r.iconScale}
        min={0.4}
        max={1}
        step={0.05}
        suffix=""
        fallback={RADAR_DEFAULTS.iconScale}
        onChange={(v) => void setSetting(RADAR_KEYS.iconScale, v)}
      />
      {flags.map((f) => (
        <div key={f.key} style={styles.row}>
          <div style={styles.rowMain}>
            <span>{f.label}</span>
            <span style={styles.dim}>{f.hint}</span>
          </div>
          <OnOff value={f.value} onChange={(v) => void setSetting(f.key, v)} />
        </div>
      ))}
    </>
  );
}

// ---- the crosshair generator -------------------------------------------------

const DECODE_MESSAGES: Record<DecodeFailure, string> = {
  prefix: 'Not a crosshair code: one starts with HXC1-.',
  char: 'Contains a character no code uses.',
  short: 'Too short: part of the code is missing.',
  crc: 'Checksum mismatch: a character was mistyped or dropped.',
  version: 'Made by a newer version of the game.',
};

const STYLE_LABELS: Record<CrosshairStyle, string> = {
  cross: 'Cross',
  crossDot: 'Cross + pip',
  dot: 'Dot',
  circle: 'Ring',
};

/**
 * Three surfaces to judge contrast against, standing in for map stills: open
 * sky, sunlit sandstone and a shadowed interior. A reticle that only works on
 * one of them is the thing this page exists to catch.
 *
 * Literal colours on purpose, and the same in every theme: they are pictures of
 * map pixels, which do not change with the app's theme either.
 */
const BACKDROPS: { label: string; background: string }[] = [
  { label: 'SKY', background: 'linear-gradient(180deg, rgb(156,195,228) 0%, rgb(207,224,236) 70%, rgb(185,162,124) 100%)' },
  {
    label: 'SANDSTONE',
    background:
      'repeating-linear-gradient(0deg, rgba(0,0,0,0.06) 0 1px, transparent 1px 22px), linear-gradient(160deg, rgb(217,185,138) 0%, rgb(196,154,99) 55%, rgb(167,125,75) 100%)',
  },
  { label: 'SHADOW', background: 'linear-gradient(200deg, rgb(58,62,68) 0%, rgb(29,32,36) 60%, rgb(18,20,22) 100%)' },
];

/** The preview's reference height: the reticle as it draws at 1080p. */
const PREVIEW_HEIGHT = 1080;

/** Keyframes the generator needs; inline styles cannot declare them. */
const GENERATOR_CSS = `
@keyframes hx-xh-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
@keyframes hx-xh-flash { from { background: rgba(110,168,254,0.28); } to { background: rgba(0,0,0,0.25); } }
`;

/** Write only the rows `next` changes, so a slider drag is one PUT per frame, not ten. */
function writeSpec(current: CrosshairSpec, next: CrosshairSpec): void {
  const before = settingsFromSpec(current);
  for (const [key, value] of Object.entries(settingsFromSpec(next))) {
    if (before[key] !== value) void setSetting(key, value).catch(() => {});
  }
}

/**
 * The crosshair generator: every spec v1 field, a live preview against three
 * surfaces, and the `HXC1-` share code to copy out or paste in.
 *
 * Applying a code writes the same rows the controls below write, so a pasted
 * crosshair is the player's crosshair in the native client too — it reads the
 * same bag and draws the same `crosshairRects`.
 */
export function CrosshairPanel() {
  const spec = useCrosshairSpec();
  const hudScale = useSetting<number>(HUD_SCALE_KEY) ?? 1;
  const outlineWidth = useSetting<number>(CROSSHAIR_KEYS.outlineThickness) ?? DEFAULT_SPEC.outline;
  const [paste, setPaste] = useState('');
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [previewCone, setPreviewCone] = useState(0.012);
  const [showMarker, setShowMarker] = useState(false);
  const code = encodeCrosshair(spec);
  const unit = hudUnit(PREVIEW_HEIGHT, hudScale);
  const set = (patch: Partial<CrosshairSpec>) => writeSpec(spec, { ...spec, ...patch });

  const applyCode = (text: string) => {
    const result = decodeCrosshair(text);
    if (!result.ok) {
      setNote({ ok: false, text: DECODE_MESSAGES[result.reason] });
      return;
    }
    writeSpec(spec, result.spec);
    setPaste('');
    setNote({ ok: true, text: 'Applied.' });
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setNote({ ok: true, text: 'Copied to the clipboard.' });
    } catch {
      // A sandboxed pane can refuse the clipboard; the code is selectable.
      setNote({ ok: false, text: 'The clipboard refused. Select the code and copy it.' });
    }
  };

  return (
    <div style={styles.rows} data-testid="crosshair-generator">
      <style>{GENERATOR_CSS}</style>
      <div style={xh.top}>
        <div style={xh.previewGrid}>
          {BACKDROPS.map((b, i) => (
            <div
              key={b.label}
              style={{ ...xh.tile, background: b.background, animationDelay: `${i * 40}ms` }}
            >
              <div style={xh.gridOverlay} />
              <div style={{ position: 'absolute', left: '50%', top: '50%' }}>
                <CrosshairShapes
                  spec={spec}
                  coneGap={coneGapPx(previewCone, unit)}
                  unit={unit}
                  hit={showMarker}
                />
              </div>
              <span style={xh.tileLabel}>{b.label}</span>
            </div>
          ))}
        </div>
        <div style={xh.codeColumn}>
          <div style={xh.heading}>Share code</div>
          <input
            key={code}
            type="text"
            readOnly
            value={code}
            aria-label="Crosshair share code"
            onFocus={(e) => e.currentTarget.select()}
            style={xh.code}
          />
          <div style={{ display: 'flex', gap: '0.35rem' }}>
            <button type="button" onClick={() => void copy()}>
              Copy
            </button>
          </div>
          <div style={xh.heading}>Paste a code</div>
          <div style={{ display: 'flex', gap: '0.35rem' }}>
            <input
              type="text"
              value={paste}
              placeholder="HXC1-…"
              aria-label="Paste a crosshair code"
              spellCheck={false}
              onChange={(e) => setPaste(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') applyCode(paste);
              }}
              style={xh.pasteInput}
            />
            <button type="button" onClick={() => applyCode(paste)} disabled={!paste.trim()}>
              Apply
            </button>
          </div>
          {note && (
            <div style={{ ...xh.meta, color: note.ok ? 'var(--text-dim)' : 'var(--danger)' }}>
              {note.text}
            </div>
          )}
        </div>
      </div>

      <div style={xh.heading}>Presets</div>
      <div style={xh.presets}>
        {Object.entries(PRESETS).map(([name, presetCode], i) => {
          const decoded = decodeCrosshair(presetCode);
          if (!decoded.ok) return null;
          const active = presetCode === code;
          return (
            <button
              key={name}
              type="button"
              className="hx-xh-preset"
              aria-pressed={active}
              onClick={() => applyCode(presetCode)}
              style={{
                ...xh.preset,
                ...(active ? xh.presetActive : null),
                animationDelay: `${Math.min(i, 6) * 35}ms`,
              }}
            >
              <span style={xh.presetSwatch}>
                <span style={{ position: 'absolute', left: '50%', top: '50%' }}>
                  <CrosshairShapes spec={decoded.spec} coneGap={0} unit={2} />
                </span>
              </span>
              <span style={xh.presetName}>{name}</span>
            </button>
          );
        })}
      </div>

      <div style={xh.heading}>Shape</div>
      <ChoiceRow
        label="Style"
        hint="The ring sits at the spread radius: the honest picture of a cone."
        value={spec.style}
        options={STYLES.map((value) => ({ value, label: STYLE_LABELS[value] }))}
        onPick={(style) => set({ style })}
      />
      <Slider
        label="Size"
        hint="Arm length, scaled with the window."
        value={spec.size}
        min={1}
        max={12}
        step={0.5}
        suffix=""
        fallback={DEFAULT_SPEC.size}
        onChange={(size) => set({ size })}
      />
      <Slider
        label="Gap"
        hint="Below zero the arms cross the centre."
        value={spec.gap}
        min={-5}
        max={20}
        step={0.5}
        suffix=""
        fallback={DEFAULT_SPEC.gap}
        onChange={(gap) => set({ gap })}
      />
      <Slider
        label="Thickness"
        hint="Line width."
        value={spec.thickness}
        min={0.2}
        max={3}
        step={0.1}
        suffix=""
        fallback={DEFAULT_SPEC.thickness}
        onChange={(thickness) => set({ thickness })}
      />
      <ChoiceRow
        label="Centre dot"
        hint="Independent of the style."
        value={spec.dot}
        options={ON_OFF}
        onPick={(dot) => set({ dot })}
      />
      <ChoiceRow
        label="T-style"
        hint="Drops the top arm."
        value={spec.tStyle}
        options={ON_OFF}
        onPick={(tStyle) => set({ tStyle })}
      />
      <ChoiceRow
        label="Dynamic"
        hint="Opens with the weapon's cone. Off changes what you see, never where shots land."
        value={spec.dynamic}
        options={ON_OFF}
        onPick={(dynamic) => set({ dynamic })}
      />

      <div style={xh.heading}>Colour</div>
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Colour</span>
          <span style={styles.dim}>
            Any hue, but not dark: a custom colour below the luma floor is lifted towards white.
          </span>
        </div>
        <div style={{ display: 'flex', gap: '0.25rem', alignItems: 'center', flexWrap: 'wrap' }}>
          {COLOR_NAMES.map((name) => (
            <button
              key={name}
              type="button"
              className="hx-xh-swatch"
              aria-label={name}
              aria-pressed={spec.color === name}
              title={name}
              onClick={() => set({ color: name, rgb: [...NAMED_COLORS[name]] })}
              style={{
                ...xh.swatch,
                background: `rgb(${NAMED_COLORS[name].join(',')})`,
                ...(spec.color === name ? xh.swatchActive : null),
              }}
            />
          ))}
          <input
            type="color"
            aria-label="Custom colour"
            value={rgbHex(spec.rgb)}
            onChange={(e) => {
              const rgb = hexRgb(e.target.value);
              if (rgb) set({ color: CUSTOM, rgb });
            }}
            style={{
              ...xh.colorInput,
              ...(spec.color === CUSTOM ? xh.swatchActive : null),
            }}
          />
          <code style={xh.meta}>{spec.color === CUSTOM ? rgbHex(spec.rgb) : spec.color}</code>
        </div>
      </div>
      <Slider
        label="Opacity"
        hint="The outline fades with it."
        value={spec.alpha}
        min={0.15}
        max={1}
        step={0.01}
        suffix=""
        fallback={DEFAULT_SPEC.alpha}
        onChange={(alpha) => set({ alpha })}
      />
      <ChoiceRow
        label="Outline"
        hint="A dark border so one colour works on every wall."
        value={spec.outline > 0}
        options={ON_OFF}
        onPick={(on) => set({ outline: on ? Math.max(outlineWidth, 0.1) : 0 })}
      />
      <Slider
        label="Outline thickness"
        hint="Kept when the outline is off."
        value={Math.max(0.1, outlineWidth)}
        min={0.1}
        max={2}
        step={0.1}
        suffix=""
        fallback={DEFAULT_SPEC.outline}
        onChange={(v) => {
          void setSetting(CROSSHAIR_KEYS.outlineThickness, v).catch(() => {});
          if (spec.outline > 0) set({ outline: v });
        }}
      />

      <div style={xh.heading}>Preview</div>
      <Slider
        label="Preview spread"
        hint="A cone to open a dynamic crosshair by. Only this page."
        value={previewCone}
        min={0}
        max={0.06}
        step={0.002}
        suffix=""
        fallback={0.012}
        onChange={setPreviewCone}
      />
      <ChoiceRow
        label="Hitmarker"
        hint="Drawn around the reticle, never instead of it."
        value={showMarker}
        options={ON_OFF}
        onPick={setShowMarker}
      />
    </div>
  );
}

/** The generator's own styles: structure by grid and edge, not glow. */
const xh: Record<string, React.CSSProperties> = {
  top: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '0.8rem',
    borderTop: '2px solid var(--accent)',
    paddingTop: '0.7rem',
  },
  previewGrid: {
    flex: '2 1 300px',
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
    gap: 2,
    minWidth: 0,
  },
  tile: {
    position: 'relative',
    height: 150,
    overflow: 'hidden',
    clipPath: 'polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)',
    animation: 'hx-xh-in 220ms ease-out both',
  },
  gridOverlay: {
    position: 'absolute',
    inset: 0,
    backgroundImage:
      'linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)',
    backgroundSize: '16px 16px',
    pointerEvents: 'none',
  },
  tileLabel: {
    position: 'absolute',
    left: 6,
    bottom: 4,
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: '0.62rem',
    letterSpacing: '0.14em',
    color: 'rgba(255,255,255,0.75)',
    textShadow: '0 1px 2px rgba(0,0,0,0.6)',
  },
  codeColumn: {
    flex: '1 1 220px',
    display: 'flex',
    flexDirection: 'column',
    gap: '0.35rem',
    minWidth: 0,
  },
  heading: {
    margin: '0.8rem 0 0.25rem',
    fontSize: '0.7rem',
    fontWeight: 700,
    letterSpacing: '0.14em',
    textTransform: 'uppercase',
    color: 'var(--text)',
  },
  code: {
    width: '100%',
    padding: '0 0.5rem',
    fontFamily: 'var(--font-mono, monospace)',
    // Thirty characters that must be readable whole: no extra tracking.
    fontSize: '0.74rem',
    color: 'var(--text)',
    background: 'rgba(0,0,0,0.25)',
    border: '1px solid var(--border)',
    borderLeft: '2px solid var(--accent)',
    animation: 'hx-xh-flash 600ms ease-out',
  },
  pasteInput: {
    flex: 1,
    minWidth: 0,
    padding: '0 0.6rem',
    fontFamily: 'var(--font-mono, monospace)',
  },
  meta: {
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: '0.72rem',
    color: 'var(--text-dim)',
  },
  presets: { display: 'flex', flexWrap: 'wrap', gap: '0.35rem' },
  preset: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.45rem',
    padding: '0.25rem 0.6rem 0.25rem 0.25rem',
    background: 'rgba(0,0,0,0.2)',
    border: '1px solid var(--border)',
    borderTop: '2px solid transparent',
    color: 'var(--text-dim)',
    cursor: 'pointer',
    animation: 'hx-xh-in 200ms ease-out both',
  },
  presetActive: {
    borderTop: '2px solid var(--accent)',
    color: 'var(--text)',
  },
  presetSwatch: {
    position: 'relative',
    width: 30,
    height: 30,
    background: 'linear-gradient(160deg, rgb(107,111,117), rgb(52,55,59))',
    flexShrink: 0,
  },
  presetName: {
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: '0.72rem',
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
  },
  swatch: {
    width: 22,
    height: 22,
    padding: 0,
    border: '1px solid rgba(0,0,0,0.5)',
    outline: '1px solid transparent',
    outlineOffset: 1,
    cursor: 'pointer',
  },
  swatchActive: { outline: '2px solid var(--accent)' },
  colorInput: {
    width: 30,
    padding: '0 2px',
    background: 'transparent',
    border: '1px solid var(--border)',
    cursor: 'pointer',
  },
};

// ---- the server browser -----------------------------------------------------

export interface ServerBrowserProps {
  /** Maps this node can actually load, so an unjoinable row says why. */
  maps: MapSummary[];
  /** The loaded map: what "open an online room" opens on. */
  mapName?: string;
  /** Who is in the match we are already in, if any. */
  peers: MatchPeer[];
  playerId: string;
  room: string;
  hosting: boolean;
  onJoin: (room: string, map: string, host: string) => void;
  onInvite: (friendCode: string) => void;
}

export function ServerBrowserPanel(props: ServerBrowserProps) {
  const [filter, setFilter] = useState('');
  const [matches, setMatches] = useState<BrowseMatch[]>([]);
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const lan = useSetting<boolean>(LAN_KEY) ?? false;

  const refresh = useCallback(async () => {
    try {
      const data = await browseServers();
      setMatches(data.matches);
      setPartial(data.peers_answered < data.peers_asked);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Polled while this panel is open and nowhere else: the fan-out waits on other
    // people's nodes, so it is far too expensive to leave running behind a game.
    const timer = window.setInterval(() => void refresh(), BROWSE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const known = useMemo(() => new Set(props.maps.map((m) => m.name)), [props.maps]);
  // The game server plays bundled maps only — it has nobody's install.
  const onlineMap =
    props.maps.find((m) => m.name === props.mapName && m.source === 'bundled')?.name ?? '';
  const q = filter.trim().toLowerCase();
  const visibleMatches = matches.filter(
    (m) =>
      q === '' ||
      m.map.toLowerCase().includes(q) ||
      m.hostName.toLowerCase().includes(q) ||
      m.id.toLowerCase().includes(q),
  );
  return (
    <div>
      <div style={styles.toolbar}>
        <input
          placeholder="Search maps, hosts or players…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{ flex: 1 }}
        />
        <button onClick={() => void refresh()}>Refresh</button>
      </div>

      {/* LAN is a *fabric* transport, not a game feature — this row exists because
          "play on LAN" is something people look for in a game menu rather than in
          a network settings page. With it on, machines on the same network find
          each other with no friend request and their matches appear below. */}
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Local network</span>
          <span style={styles.dim}>{lan ? 'On' : 'Off'}</span>
        </div>
        <button onClick={() => void setSetting(LAN_KEY, !lan)}>
          {lan ? 'Turn off' : 'Turn on'}
        </button>
      </div>

      {/* Online rooms run on the game server, which is where web players are:
          a room opened here shows up in the browser game's list, and theirs
          show up below. Unrated once a guest joins — the server decides. */}
      <div style={styles.row}>
        <div style={styles.rowMain}>
          <span>Online room</span>
          <span style={styles.dim}>
            {onlineMap
              ? `Open one on ${onlineMap} that web players can join`
              : 'Load a bundled map to open one'}
          </span>
        </div>
        <button onClick={() => props.onJoin('', onlineMap, ONLINE_HOST)} disabled={!onlineMap}>
          Open
        </button>
      </div>

      {error && <div style={styles.error}>{error}</div>}
      {partial && (
        <div style={styles.dim}>
          Some friends' nodes didn't answer in time — this list may be incomplete.
        </div>
      )}

      <h4 style={styles.heading}>
        Matches {loading && matches.length === 0 ? '…' : `(${visibleMatches.length})`}
      </h4>
      {visibleMatches.length === 0 && !loading && (
        <div style={styles.dim}>
          Nothing running. Host one from the menu and friends on the fabric will see it in their own
          browser.
        </div>
      )}
      {visibleMatches.map((m) => {
        const here = m.host === '';
        const online = m.host === ONLINE_HOST;
        const playable = known.has(m.map);
        const current = here && m.id === props.room;
        return (
          <div key={`${m.host}:${m.id}`} style={styles.row}>
            <div style={styles.rowMain}>
              <span>
                <code>{m.map}</code> · {m.hostName}
                {online && <span style={styles.badge}>web</span>}
                {current && <span style={styles.badge}>you're in this one</span>}
              </span>
              <span style={styles.dim}>
                {m.players - m.bots} player{m.players - m.bots === 1 ? '' : 's'}
                {m.bots > 0 ? ` + ${m.bots} bot${m.bots === 1 ? '' : 's'}` : ''} of {m.maxPlayers}
                {' · '}
                <code>{m.id.slice(0, 8)}</code>
                {!playable && ' · map not on this machine'}
              </span>
            </div>
            <button
              onClick={() => props.onJoin(m.id, m.map, m.host)}
              disabled={current || !playable || m.players >= m.maxPlayers}
              title={
                playable
                  ? undefined
                  : 'That map does not ship with this version of the game, so it cannot be loaded here.'
              }
            >
              {m.players >= m.maxPlayers ? 'Full' : 'Join'}
            </button>
          </div>
        );
      })}

      {/* The roster used to be rendered a second time here, as a "Players"
          list with its own Invite buttons — the same people, the same actions,
          two implementations that had already drifted (this copy disabled Invite
          for anyone already in a match; the Friends copy did not). Servers
          answers "what is running"; Friends answers "who is about". They are
          different questions, which is what this panel's own doc comment says,
          and one of them belongs in one place. */}
      <h4 style={styles.heading}>In your match ({props.peers.length})</h4>
      {props.peers.length === 0 && <div style={styles.dim}>Nobody yet.</div>}
      {props.peers.map((p) => (
        <div key={p.id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>
              {p.name}
              {p.id === props.playerId ? ' (you)' : ''}
            </span>
            <span style={styles.dim}>
              {p.bot ? 'bot' : `${Math.round(p.rtt)} ms`} · {p.kills}/{p.deaths}
              {p.alive ? '' : ' · down'}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---- friends ----------------------------------------------------------------

export interface FriendsProps {
  invitees: Invitee[];
  invites: MatchInvite[];
  hosting: boolean;
  /**
   * Invite somebody. **Not** conditional on already hosting any more — see
   * `FriendsPanel`. The caller starts a match first when there isn't one.
   */
  onInvite: (friendCode: string) => void;
  onAccept: (invite: MatchInvite) => void;
  onDismiss: (room: string) => void;
}

/** Debounce on the directory search, same as `people/DiscoverSection`. */
const SEARCH_DEBOUNCE_MS = 250;

/**
 * Find somebody by `@username` and send them a friend request, without leaving
 * the game.
 *
 * The panel used to say "the roster lives in the Social panel" and stop there,
 * which made an empty friends list a dead end: the one screen where you have just
 * discovered you have nobody to play with was the one screen that could not do
 * anything about it.
 *
 * `searchDirectory` and `addFriend` are the social module's own client — the same
 * pair `people/DiscoverSection` uses — so this is a second *view*, not a second
 * implementation, and there is no new backend route behind it. The request is sent
 * as `@username` rather than the person id already in hand, deliberately: the
 * backend re-resolves and re-checks the key fingerprint itself, because the
 * browser is not a trusted source of person ids.
 */
function AddFriend() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DirectoryEntry[]>([]);
  const [minPrefix, setMinPrefix] = useState(3);
  const [note, setNote] = useState('');
  // Which query the in-flight response belongs to: a slow request for "ro" can
  // otherwise land after a fast one for "robert" and repopulate the list with
  // stale, broader results.
  const latest = useRef('');

  useEffect(() => {
    const q = query.trim().replace(/^@/, '');
    latest.current = q;
    if (q.length < minPrefix) {
      setResults([]);
      return;
    }
    const timer = window.setTimeout(() => {
      void searchDirectory(q)
        .then((res) => {
          if (latest.current !== q) return;
          setResults(res.results ?? []);
          if (typeof res.min_prefix === 'number') setMinPrefix(res.min_prefix);
        })
        .catch(() => {
          if (latest.current === q) setResults([]);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, minPrefix]);

  const add = async (entry: DirectoryEntry) => {
    const res = await addFriend(`@${entry.handle}`);
    setNote(res.error ? res.error : `Friend request sent to @${entry.handle}.`);
    setQuery('');
  };

  return (
    <div>
      <div style={styles.toolbar}>
        <input
          type="search"
          placeholder="Add someone by @username…"
          aria-label="Find someone by username"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1 }}
        />
      </div>
      {note && <div style={styles.dim}>{note}</div>}
      {results.map((entry) => (
        <div key={entry.person_id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>@{entry.handle}</span>
            {entry.display_name && entry.display_name !== entry.handle && (
              <span style={styles.dim}>{entry.display_name}</span>
            )}
          </div>
          <button onClick={() => void add(entry)}>Add</button>
        </div>
      ))}
    </div>
  );
}

/**
 * Who you can play with, and who has asked you to.
 *
 * Separate from the server browser on purpose: that answers "what is running",
 * this answers "who is about". They are different questions and people arrive at
 * the menu with one or the other.
 */
export function FriendsPanel(props: FriendsProps) {
  return (
    <div>
      <h4 style={styles.heading}>Invitations ({props.invites.length})</h4>
      {props.invites.length === 0 && <div style={styles.dim}>Nothing waiting.</div>}
      {props.invites.map((invite) => (
        <div key={invite.room} style={styles.row}>
          <div style={styles.rowMain}>
            <span>
              {/* A person, not a machine. `hostName` is now their `@username`,
                  resolved roster-first by the backend; the device they sent it
                  from is the secondary line, which is worth keeping because an
                  invite fans out to every machine they have online. */}
              <strong>{invite.hostName}</strong> invited you to <code>{invite.map}</code>
            </span>
            <span style={styles.dim}>{invite.hostDevice || invite.host.slice(0, 8)}</span>
          </div>
          <button onClick={() => props.onAccept(invite)}>Join</button>
          <button onClick={() => props.onDismiss(invite.room)}>Dismiss</button>
        </div>
      ))}

      <h4 style={styles.heading}>Friends ({props.invitees.length})</h4>
      <AddFriend />
      {props.invitees.length === 0 && <div style={styles.dim}>Nobody online.</div>}
      {props.invitees.map((f) => (
        <div key={f.person_id} style={styles.row}>
          <div style={styles.rowMain}>
            <span>{f.username ? `@${f.username}` : f.name}</span>
            <span style={styles.dim}>
              {/* Presence beyond a dot: what they are actually doing. The backend
                  already knew this (`/invitees` reads `fabric.hosted_rooms`) and
                  was throwing it away. */}
              {f.room
                ? `playing ${f.room_map || 'a match'}`
                : `${f.devices_online} device${f.devices_online === 1 ? '' : 's'} online`}
              {f.can_play ? '' : ' · no match support on their build'}
            </span>
          </div>
          <button
            onClick={() => props.onInvite(f.friend_code)}
            disabled={!f.can_play}
            // No longer gated on already hosting. "Invite" is a complete intent,
            // and making somebody guess that it needs a match to exist first —
            // then go and start one, then come back — is why this panel read as
            // broken. The caller hosts on the selected map when there is nothing
            // to invite them to.
            title={
              props.hosting
                ? 'Invites every machine they have online'
                : 'Starts a match on the selected map, then invites them'
            }
          >
            Invite
          </button>
        </div>
      ))}
    </div>
  );
}

// ---- controls ---------------------------------------------------------------

interface Recording {
  action: GameAction;
  slot: number;
}

/**
 * The rebind table.
 *
 * Captures through a **focused input** rather than a window listener (the same
 * trick `ShortcutsPanel` uses): while a menu is open the keyboard belongs to the
 * shell again, and a window listener here would fight the dispatcher for it.
 */
export function ControlsPanel({
  bindings,
  onChange,
}: {
  bindings: Bindings;
  onChange: (next: Bindings) => void;
}) {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [note, setNote] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (recording) inputRef.current?.focus();
  }, [recording]);

  const capture = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!recording) return;
    // Nothing here reaches the shell: this is a focused text input, so the
    // dispatcher's own typing guard already stands aside.
    e.preventDefault();
    e.stopPropagation();
    const code = e.nativeEvent.code;
    if (e.key === 'Escape') {
      setRecording(null);
      setNote('');
      return;
    }
    // A bare modifier is *usually* somebody reaching for a real key — but crouch
    // genuinely wants to live on Ctrl, so a modifier held for a moment is taken as
    // itself rather than discarded forever.
    if (/^(Shift|Alt|Meta)(Left|Right)$/.test(code)) return;
    const previous = boundTo(bindings, code);
    onChange(setBinding(bindings, recording.action, recording.slot, code));
    setNote(
      previous && previous !== recording.action
        ? `${keyLabel(code)} was ${actionLabel(previous)} — that one is now unbound.`
        : '',
    );
    setRecording(null);
  };

  // Every group, from the one list. This used to be a hand-written
  // `['Movement', 'Combat', 'View']`, which silently left out Utility — so the
  // grenade, throw, buy, use and drop keys were rebindable in code and on no
  // screen at all. `CONTROL_GROUPS` is checked against `ACTIONS` by a test.
  const groups = CONTROL_GROUPS;

  return (
    <div>
      <div style={styles.toolbar}>
        <span style={styles.dim}>
          These keys work only while you are playing — they never fire anywhere else in the app, and
          the app's own shortcuts are suppressed while you play. The native client reads the same
          table when it starts.
        </span>
        <button
          onClick={() => {
            onChange(defaultControls());
            setNote('');
          }}
          disabled={isDefaultControls(bindings)}
        >
          Reset all
        </button>
      </div>

      {recording && (
        <div style={styles.recording}>
          Press a key for <strong>{actionLabel(recording.action)}</strong> — Esc to cancel.
          {/* Focused so the keystroke lands here rather than doing whatever it is
              currently bound to elsewhere. `readOnly` keeps a caret out of it. */}
          <input
            ref={inputRef}
            readOnly
            value=""
            onKeyDown={capture}
            onBlur={() => setRecording(null)}
            style={styles.recorder}
          />
        </div>
      )}
      {note && <div style={styles.dim}>{note}</div>}

      {groups.map((group) => (
        <div key={group}>
          <h4 style={styles.heading}>{group}</h4>
          {ACTIONS.filter((a) => a.group === group).map((doc) => (
            <div key={doc.action} style={styles.row}>
              <div style={styles.rowMain}>
                <span>{doc.label}</span>
                {doc.note && <span style={styles.dim}>{doc.note}</span>}
              </div>
              <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center' }}>
                {Array.from({ length: SLOTS }, (_, slot) => {
                  const code = bindings[doc.action]?.[slot];
                  const active = recording?.action === doc.action && recording?.slot === slot;
                  return (
                    <button
                      key={slot}
                      onClick={() => setRecording({ action: doc.action, slot })}
                      style={{
                        ...styles.keycap,
                        ...(active ? styles.keycapActive : null),
                        ...(code ? null : styles.keycapEmpty),
                      }}
                      title={code ? `${code} — click to rebind` : 'Click to bind a key'}
                    >
                      {active ? '…' : code ? keyLabel(code) : '+'}
                    </button>
                  );
                })}
                <button
                  onClick={() => onChange(setBinding(bindings, doc.action, 0, null))}
                  disabled={(bindings[doc.action]?.length ?? 0) === 0}
                  title="Clear the first key"
                  style={styles.clear}
                >
                  ×
                </button>
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function actionLabel(action: GameAction): string {
  return ACTIONS.find((a) => a.action === action)?.label ?? action;
}

// ---- shared styles ----------------------------------------------------------

export const styles: Record<string, React.CSSProperties> = {
  rows: { display: 'flex', flexDirection: 'column' },
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.8rem',
    padding: '0.45rem 0',
    borderBottom: '1px solid rgba(255,255,255,0.055)',
  },
  rowMain: { display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 0 },
  dim: { color: 'var(--text-dim)', fontSize: '0.74rem', lineHeight: 1.45 },
  heading: {
    margin: '0.9rem 0 0.3rem',
    fontSize: '0.72rem',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'var(--text-dim)',
  },
  toolbar: { display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.4rem' },
  /** A standing condition the section is under — an accent edge, not a glowing box. */
  notice: {
    borderLeft: '2px solid var(--accent, #6ea8fe)',
    background: 'rgba(110,168,254,0.08)',
    padding: '0.4rem 0.6rem',
    fontSize: '0.74rem',
    lineHeight: 1.45,
    color: 'var(--text-dim)',
  },
  error: { color: '#f85149', padding: '0.3rem 0' },
  badge: {
    marginLeft: '0.4rem',
    fontSize: '0.66rem',
    padding: '0.05rem 0.3rem',
    borderRadius: 3,
    background: 'rgba(110,168,254,0.16)',
    color: 'var(--accent, #6ea8fe)',
  },
  recording: {
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
    padding: '0.4rem 0.5rem',
    borderRadius: 5,
    background: 'rgba(110,168,254,0.12)',
    border: '1px solid var(--accent, #6ea8fe)',
  },
  recorder: { width: 1, opacity: 0, border: 'none', background: 'transparent' },
  keycap: {
    minWidth: 46,
    fontFamily: 'var(--font-mono, monospace)',
    padding: '0.2rem 0.4rem',
    cursor: 'pointer',
  },
  keycapActive: {
    border: '1px solid var(--accent, #6ea8fe)',
    color: 'var(--accent, #6ea8fe)',
  },
  keycapEmpty: { opacity: 0.45 },
  clear: { padding: '0.1rem 0.35rem' },
  choice: {
    background: 'transparent',
    border: '1px solid var(--border, #2a2a2a)',
    color: 'var(--text-dim)',
    padding: '0.25rem 0.7rem',
    borderRadius: 5,
    cursor: 'pointer',
  },
  choiceActive: {
    background: 'rgba(110,168,254,0.14)',
    // The shorthand, not `borderColor`: spread over `choice`, which sets `border`,
    // and mixing a shorthand with its own longhand is what React warns about.
    border: '1px solid var(--accent, #6ea8fe)',
    color: 'var(--text)',
  },
};
