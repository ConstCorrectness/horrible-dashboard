/**
 * The badge shown while a pane holds the keyboard/mouse.
 *
 * Its only job is to answer "how do I get out of this?" — and to answer it
 * *honestly*. Holding Escape only works where the Keyboard Lock API does
 * (Chromium, and only in document fullscreen); everywhere else the browser
 * releases pointer lock on the first Escape whatever we do. Promising a hold
 * gesture that silently isn't available is worse than offering the tap, so the
 * text is derived from `canHoldEscape()` rather than from the pane's preference.
 *
 * The same rule, harder, for system keys: when Alt+Tab is actually being taken
 * the badge must *say so*. A window that swallows Alt+Tab silently is
 * indistinguishable from a frozen machine, and the release gesture is then the
 * only way out — so it is stated, not implied.
 */
import { canHoldEscape, canHoldSystemKeys, useCaptureState } from '@horrible/core';

export function CaptureHud() {
  const capture = useCaptureState();
  if (!capture) return null;
  const holdWorks = capture.escape === 'passthrough' && canHoldEscape();
  const systemKeys = canHoldSystemKeys();
  const release = holdWorks ? (
    <>
      <kbd>Hold Esc</kbd> to release the mouse
    </>
  ) : (
    <>
      <kbd>Esc</kbd> to release the mouse
    </>
  );
  return (
    <div className="capture-hud" role="status">
      <span className="capture-hud-dot" aria-hidden="true" />
      {systemKeys ? (
        <>
          {release} · <kbd>Alt+Tab</kbd> is captured
        </>
      ) : (
        release
      )}
    </div>
  );
}
