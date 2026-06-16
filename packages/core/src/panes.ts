/**
 * Per-pane-instance **params**: arbitrary data the opener passes to a pane
 * instance (e.g. the editor's buffer `source`). The workspace host sets it on each
 * pane; a pane component reads its own params with `usePaneParams`. Distinct from
 * `PaneInstanceContext` (the instance id) — params are the instance's input.
 *
 * Lives in core (not ui) so feature modules can read params without a core→ui
 * cycle; ui only supplies the values.
 */
import { createContext, useContext } from 'react';

export type PaneParams = Record<string, unknown>;

export const PaneParamsContext = createContext<PaneParams>({});

/** The params the current pane instance was opened with (`{}` outside a pane). */
export function usePaneParams(): PaneParams {
  return useContext(PaneParamsContext);
}
