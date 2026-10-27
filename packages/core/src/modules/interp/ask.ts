/**
 * "Ask about this": hand a question to the interpretability agent's Ask strip.
 *
 * Kept apart from `AskPanel.tsx` on purpose. Every section's header carries the
 * button, and importing the panel here would put `ChatWidget` — and the 3D avatar
 * it imports — into every section's chunk. This file imports nothing heavy; the
 * panel loads only when the strip opens.
 */
import { revealRegionView } from '../../layout/controller';
import { getModelLocus } from '../../model-locus';
import { sendInChat } from '../agent/openSession';
import { locusLabel } from './locus-label';

export const INTERP_AGENT = 'interp';
export const ASK_VIEW = 'interp.ask';

/**
 * Ask the interpretability agent about `subject`, in the Ask strip.
 *
 * Sent, not drafted: the button says "Ask", and the subject is something the user
 * pointed at rather than wording they would want to edit. The locus is named in
 * the question so the answer is about the block they selected, not the model in
 * general — the digest carries it too, but stated in the question it reads back in
 * the transcript as what was actually asked.
 */
export function askInterp(subject: string): void {
  const where = locusLabel(getModelLocus());
  const prompt =
    `Explain what I'm looking at in the ${subject}` +
    (where ? `, at ${where}` : '') +
    '. Ground it in interp.state.';
  revealRegionView(ASK_VIEW);
  sendInChat(prompt, { agentId: INTERP_AGENT, open: false });
}
