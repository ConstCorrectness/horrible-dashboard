/**
 * The pre-flight check: what a pixel stream would expose that the semantic
 * mirror would not.
 *
 * **A video frame does not know what a pane is.** That is the whole reason this
 * file exists. The semantic mirror can withhold a pane because it forwards
 * structure and can simply omit it; a screen capture forwards *light*, so every
 * pane the host can see is in the stream whether it was declared or not.
 *
 * So the guarantee changes shape between the two modes, and pretending otherwise
 * would be the most dangerous thing this module could do. In semantic mode
 * `redactFrame` is an enforcement boundary. Here there is no boundary to enforce
 * — only a warning that has to be honest, specific, and delivered *before* the
 * capture starts rather than after.
 *
 * Pure, and takes the frame and the registry lookup as arguments, so the rule can
 * be tested exhaustively without a DOM.
 */
import { visiblePanes } from '../../layout/model';
import type { FrameState } from '../../layout/types';

import type { ViewLookup } from './mirror';

/** One pane that would be caught by a capture without having agreed to it. */
export interface UndeclaredPane {
  instanceId: string;
  /** The module's manifest title. The host already knows what this is. */
  title: string;
  viewId: string;
}

export interface Preflight {
  /** Visible panes that never declared themselves shareable, in layout order. */
  undeclared: UndeclaredPane[];
  /** How many visible panes were checked. */
  checked: number;
}

/**
 * Whether a declared pane is safe to appear in a capture.
 *
 * **Any** declaration counts, not only `mode: 'pixels'`. A pane declaring
 * `collab` or `mirror` has already agreed to be seen by a guest; being seen in
 * the video too tells them nothing the semantic mirror was not already telling
 * them. `pixels` is the mode for a pane that can *only* be shared this way, not
 * a stricter permission — reading it as one would flag every shared pane in the
 * workspace and train the host to click through the warning, which is worse than
 * no warning at all.
 */
export function isPixelSafe(share: { mode: string } | undefined): boolean {
  return share !== undefined;
}

/**
 * What a capture of this workspace would expose beyond what has been declared.
 *
 * Checks the panes **actually on screen** (`visiblePanes`), not every open pane:
 * a background tab and a minimized window are not in the light, so listing them
 * would be a false positive — and a warning with false positives is a warning
 * people learn to dismiss.
 */
export function preflight(frame: FrameState, lookup: ViewLookup): Preflight {
  const undeclared: UndeclaredPane[] = [];
  const located = visiblePanes(frame);
  for (const { pane } of located) {
    const info = lookup(pane.viewId);
    if (isPixelSafe(info?.share)) continue;
    undeclared.push({
      instanceId: pane.instanceId,
      // The host is the audience for this list, so the real view id is useful
      // and there is nothing to redact — it never leaves their machine.
      title: info?.title ?? pane.viewId,
      viewId: pane.viewId,
    });
  }
  return { undeclared, checked: located.length };
}

/** Whether a capture may start without the host confirming anything. */
export function isClear(result: Preflight): boolean {
  return result.undeclared.length === 0;
}
