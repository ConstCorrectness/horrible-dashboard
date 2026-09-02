/**
 * Settings-page section: the **desktop paradigm** — tiled frame or free windows.
 *
 * This is where the tiled/floating choice lives now. It used to be reachable
 * from the Start menu as two separate "New … desktop" rows, which made a
 * launcher carry a preference: that menu exists to open things, and the paradigm
 * is not a thing you open.
 *
 * It renders **only the switch for the desktop you are on**. The neighbouring
 * question — which kind a *new* desktop should be — is the declared
 * `desktop.defaultMode` setting and renders itself directly above this, so
 * offering it here too would be two controls over one key sitting in one group.
 *
 * This one is *not* a setting and must never become one: mode is a property of
 * each workspace, stored in its own layout, so a single global key could only
 * ever disagree with whichever desktop was in front of you. The switch converts
 * in place, and says that the conversion is lossy — `explodeToWindows` can only
 * express split ratios as the rects they happened to occupy, and `tileWindows`
 * cannot recover the ratios you dragged.
 *
 * See docs/architecture/desktop-shell.mdx.
 */
import { layoutStore, setDesktopMode, useWorkspaces, type DesktopMode } from '@horrible/core';
import { useSyncExternalStore } from 'react';

const MODES: { id: DesktopMode; label: string; glyph: string; blurb: string }[] = [
  {
    id: 'tiling',
    label: 'Tiled',
    glyph: '▦',
    blurb: 'Panes split the screen between them and never overlap.',
  },
  {
    id: 'floating',
    label: 'Floating',
    glyph: '❐',
    blurb: 'Panes are windows you move, stack and overlap freely.',
  },
];

function ModeChoice({
  value,
  onPick,
  idPrefix,
}: {
  value: DesktopMode;
  onPick: (mode: DesktopMode) => void;
  idPrefix: string;
}) {
  return (
    <div className="desktop-mode-choice" role="radiogroup" aria-label="Desktop paradigm">
      {MODES.map((mode) => (
        <button
          key={mode.id}
          id={`${idPrefix}-${mode.id}`}
          type="button"
          role="radio"
          aria-checked={value === mode.id}
          className={`desktop-mode-btn${value === mode.id ? ' is-active' : ''}`}
          onClick={() => onPick(mode.id)}
        >
          <span className="desktop-mode-glyph" aria-hidden="true">
            {mode.glyph}
          </span>
          <span className="desktop-mode-label">{mode.label}</span>
          <span className="desktop-mode-blurb">{mode.blurb}</span>
        </button>
      ))}
    </div>
  );
}

export function DesktopModeSection() {
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const { workspaces, activeId } = useWorkspaces();
  const active = workspaces.find((w) => w.id === activeId);

  return (
    <div className="desktop-mode">
      <div className="desktop-mode-block">
        {/* Named, because "this desktop" is only unambiguous while you can see
            which one you are on — and the settings page is a tab like any other,
            reachable from any of them. */}
        <h3 className="desktop-mode-head">
          This desktop{active ? <span className="desktop-mode-name">{active.name}</span> : null}
        </h3>
        <ModeChoice
          idPrefix="desktop-mode-current"
          value={frame.mode}
          onPick={(mode) => {
            if (mode !== frame.mode) void setDesktopMode(mode);
          }}
        />
        <p className="desktop-mode-warn">
          Converting rearranges everything that is open. Which panes are open is preserved; their
          sizes and split ratios are not.
        </p>
      </div>
    </div>
  );
}
