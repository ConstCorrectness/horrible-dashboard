/**
 * The Logit Lens lab: the window's lens runs, looked at properly.
 *
 * Three sections (`r` / `g` / `c`, declared on the panel so the host's tab strip
 * and the keyboard move one value):
 *
 * - **Reply** — the last reply's lens as a full layer × token heatmap, and the
 *   stack under any token in detail (`lens-lab/ReplyView`).
 * - **Guess** — "When does it know?": guess the layer a token settles at
 *   (`lens-lab/GuessView`).
 * - **Compare** — base against fine-tune on the same text (`lens-lab/CompareView`).
 */
import { usePaneSection } from '../../../layout/use-sections';
import { CompareView } from '../lens-lab/CompareView';
import { GuessView } from '../lens-lab/GuessView';
import { ReplyView } from '../lens-lab/ReplyView';
import '../lens-lab/lens-lab.css';

export function LensLabPanel() {
  const { section, setSection } = usePaneSection();
  return (
    <div className="ll-root">
      {section === 'guess' ? (
        <GuessView onOpenReply={() => setSection('reply')} />
      ) : section === 'compare' ? (
        <CompareView />
      ) : (
        <ReplyView />
      )}
    </div>
  );
}
