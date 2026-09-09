/**
 * The workbench's open-file tabs.
 *
 * A tab is a `<button>` carrying a class, which is load-bearing rather than
 * cosmetic: `controls.css` fixes every *classless* button at the 30px control
 * height, and a tab is a row, not an action button — an unclassed one would be
 * stretched. Same reason the close affordance is `.ide-tab-close`.
 *
 * The dirty mark follows VS Code: a filled dot **replacing** the close glyph
 * until you hover, so an unsaved file reads at a glance without adding a second
 * piece of furniture to a strip that is already tight.
 */
import { IconClose, IconDot } from '../../glyphs';
import { sourceTitle } from '../editor';

interface Props {
  tabs: string[];
  active: string | null;
  dirty: Set<string>;
  onSelect: (uri: string) => void;
  onClose: (uri: string) => void;
}

export function IdeTabStrip({ tabs, active, dirty, onSelect, onClose }: Props) {
  if (tabs.length === 0) return null;
  return (
    <div className="ide-tabs" role="tablist" aria-label="Open files">
      {tabs.map((uri) => {
        const isActive = uri === active;
        const isDirty = dirty.has(uri);
        return (
          <button
            key={uri}
            className={`ide-tab${isActive ? ' ide-tab--active' : ''}`}
            role="tab"
            aria-selected={isActive}
            title={pathOf(uri)}
            onClick={() => onSelect(uri)}
            onAuxClick={(e) => {
              // Middle click closes, as it does in every editor and browser.
              if (e.button === 1) {
                e.preventDefault();
                onClose(uri);
              }
            }}
          >
            <span className="ide-tab-label">{sourceTitle(uri)}</span>
            <span
              className={`ide-tab-close${isDirty ? ' ide-tab-close--dirty' : ''}`}
              role="button"
              tabIndex={-1}
              aria-label={`Close ${sourceTitle(uri)}${isDirty ? ' (unsaved)' : ''}`}
              onClick={(e) => {
                e.stopPropagation();
                onClose(uri);
              }}
            >
              {isDirty ? <IconDot /> : <IconClose />}
              {/* Hover swaps the dot for a close affordance, VS Code's behaviour:
                  the dot must be readable at rest, and a tab needs a way to be
                  closed while it is unsaved. */}
              {isDirty ? (
                <span className="ide-tab-close-hover">
                  <IconClose />
                </span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** The path behind a source URI, for the tooltip — `note:` ids stay as they are. */
function pathOf(uri: string): string {
  return uri.startsWith('workspace-file:') ? uri.slice('workspace-file:'.length) : uri;
}
