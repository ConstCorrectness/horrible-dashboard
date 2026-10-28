/**
 * What you carry, and the aim range's controls: the two pickers the pause menu
 * needs as much as the main menu does.
 *
 * The browser client never shows the main menu (`apps/assault-web` drops a
 * player straight into a room), and while the only loadout picker lived there a
 * browser player held the rifle, the pistol and the knife and nothing else — the
 * sniper included, which is how it "disappeared" (`test_hassault_sniper.py`).
 * So the picker is one component, composed by both menus.
 */
import type { ReactNode } from 'react';

import type { WeaponSpec } from '../api';
import { SIDES, type Side } from '../cosmetics';
import { DEFAULT_LOADOUT, weaponsInSlot, type LoadoutChoice, type Loadouts } from '../loadout';
import { styles as panel } from '../menu-panels';
import type { DrillStatus } from '../training';
import type { DrillSummary } from '../training-drill';

const MONO = 'var(--font-mono, ui-monospace, monospace)';

const SLOTS = ['primary', 'secondary'] as const;

/** One weapon select: the slot's guns, the default when the pick is not among them. */
function WeaponSelect({
  offered,
  slot,
  wanted,
  label,
  onPick,
}: {
  offered: WeaponSpec[];
  slot: (typeof SLOTS)[number];
  wanted: string | undefined;
  label: string;
  onPick: (id: string) => void;
}) {
  // A pick the other side carries shows as this side's default, which is what
  // the server will hand out.
  const value = wanted && offered.some((w) => w.id === wanted) ? wanted : DEFAULT_LOADOUT[slot];
  return (
    <select
      value={value}
      onChange={(e) => onPick(e.target.value)}
      aria-label={label}
      style={{ width: 132, padding: '0 0.4rem' }}
    >
      {offered.map((w) => (
        <option key={w.id} value={w.id}>
          {w.name}
        </option>
      ))}
    </select>
  );
}

/** One side's primary and secondary, plus whatever else the caller puts beside them. */
export function LoadoutSideRow({
  side,
  weapons,
  loadouts,
  onLoadouts,
  children,
}: {
  side: Side;
  weapons: WeaponSpec[];
  loadouts: Loadouts | null;
  onLoadouts: (next: Loadouts) => void;
  children?: ReactNode;
}) {
  const chosen = loadouts?.[side];
  return (
    <div style={panel.row}>
      <div style={panel.rowMain}>
        <span>{side === 't' ? 'Loadout · T' : 'Loadout · CT'}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
        {SLOTS.map((slot) => (
          <WeaponSelect
            key={slot}
            offered={weaponsInSlot(weapons, slot, side)}
            slot={slot}
            wanted={chosen?.[slot]}
            label={`${side.toUpperCase()} ${slot} weapon`}
            onPick={(id) => onLoadouts({ ...loadouts, [side]: { ...chosen, [slot]: id } })}
          />
        ))}
        {children}
      </div>
    </div>
  );
}

/** The pause menu's Loadout tab. */
export function LoadoutPanel(props: {
  weapons: WeaponSpec[];
  loadouts: Loadouts | null;
  onLoadouts: (next: Loadouts) => void;
}) {
  if (!props.weapons.some((w) => w.slot)) {
    return <div style={panel.dim}>The server has not sent its weapons yet.</div>;
  }
  return (
    <div style={panel.rows}>
      <div style={panel.notice}>
        Applies at your next spawn, in every mode without a buy menu. Arenas hand everyone the same
        guns.
      </div>
      {SIDES.map((side) => (
        <LoadoutSideRow
          key={side}
          side={side}
          weapons={props.weapons}
          loadouts={props.loadouts}
          onLoadouts={props.onLoadouts}
        />
      ))}
    </div>
  );
}

export interface RangePanelProps {
  /** The drills this map offers (`drills.py`): moving targets, scored runs. */
  drills: DrillSummary[];
  /** The drill Start begins; `''` is free practice. */
  drill: string;
  onDrill: (id: string) => void;
  /** The running or last-run drill, if any. */
  status: DrillStatus | null;
  weapons: WeaponSpec[];
  /** The range's own guns. It has no sides, so every gun is on offer. */
  gun: LoadoutChoice;
  onGun: (next: LoadoutChoice) => void;
  /** Start `drill` from its countdown (or free practice, for `''`). */
  onStart: () => void;
  /** Stop any drill and go back to the free range. */
  onFree: () => void;
}

/**
 * The pause menu's Range tab: pick a drill and a gun, then Start. Restart is
 * Start again on the drill that is running — a fresh countdown and a clean
 * score — so it is the same button, named for what it will do.
 */
export function RangePanel(props: RangePanelProps) {
  const running = props.status !== null && props.status.phase !== 'done';
  const selected = props.drills.find((d) => d.id === props.drill);
  const restart = running && props.status?.id === props.drill;
  return (
    <div style={panel.rows}>
      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Drill</span>
          <span style={panel.dim}>
            {selected
              ? selected.description
              : 'Still dummies on the spawn points. No clock, no score.'}
          </span>
        </div>
        <select
          value={props.drill}
          onChange={(e) => props.onDrill(e.target.value)}
          aria-label="Drill"
          style={{ width: 180, padding: '0 0.4rem' }}
        >
          <option value="">Free practice</option>
          {props.drills.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>

      <div style={panel.row}>
        <div style={panel.rowMain}>
          <span>Gun</span>
          <span style={panel.dim}>Swaps at once. A run in progress keeps its clock.</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {SLOTS.map((slot) => (
            <WeaponSelect
              key={slot}
              offered={weaponsInSlot(props.weapons, slot)}
              slot={slot}
              wanted={props.gun[slot]}
              label={`Range ${slot} weapon`}
              onPick={(id) => props.onGun({ ...props.gun, [slot]: id })}
            />
          ))}
        </div>
      </div>

      <div style={{ ...panel.row, borderBottom: 'none', paddingTop: '0.7rem' }}>
        <div style={panel.rowMain}>
          {props.status && (
            <span style={{ fontFamily: MONO, fontSize: '0.74rem', color: 'var(--text-dim)' }}>
              {props.status.name} · {props.status.phase}
              {running ? ` · ${Math.ceil(props.status.remaining)}s` : ''}
            </span>
          )}
        </div>
        {running && <button onClick={props.onFree}>Stop</button>}
        {/* Classless, so it is a standard 30px control (`controls.css`); the
            accent is only colour. */}
        <button
          onClick={props.onStart}
          style={{
            background: 'var(--accent)',
            color: 'var(--bg)',
            fontWeight: 700,
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
          }}
        >
          {restart ? 'Restart' : 'Start'}
        </button>
      </div>
    </div>
  );
}
