/**
 * The strip every suite section sits under: which section this is, which model
 * the suite is looking at, and where the shared model locus points.
 *
 * It is the same three facts in every tool on purpose. The suite's whole claim is
 * that these used to be separate windows that each knew one of them; a reader
 * moving from the lens to the explorer should never have to re-establish "which
 * model, which layer" by looking.
 */
import { useSyncExternalStore, type ReactNode } from 'react';

import { useAgentContext } from '../../agent-context';
import { IconComment } from '../../glyphs';
import { useModelLocus } from '../../model-locus';
import { askInterp } from './ask';
import { locusLabel } from './locus-label';
import { interpretabilityStore } from '../interpretability/store';
import { S } from './styles';
import { suiteDigest } from './suite-state';

/**
 * Publish the suite digest as this section's agent context. Every section does,
 * so whichever one is on screen answers "what am I looking at?" with the same
 * facts plus its own name — and the chat attaches it to every turn while the
 * suite is visible.
 */
export function useSuiteAgentContext(view: string): void {
  useAgentContext(() => ({ view, ...suiteDigest() }));
}

export function SuiteFrame({ title, children }: { title: string; children: ReactNode }) {
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  const locus = useModelLocus();
  const where = locusLabel(locus);
  useSuiteAgentContext(title);
  return (
    // The section's name is on the rail beside it; repeating it here read as
    // "MODEL  MODEL gemma…" on the Model section. It labels the region instead.
    <div style={S.frame} role="region" aria-label={title}>
      <div style={S.strip}>
        <span style={S.stripFact} title={arch?.sourceDetail}>
          <span style={S.stripLabel}>Model</span>
          {arch?.model || 'none'}
        </span>
        {where ? (
          <span style={S.stripFact}>
            <span style={S.stripLabel}>Locus</span>
            {where}
          </span>
        ) : null}
        {locus.traceId ? (
          <span style={S.stripFact} title={locus.traceId}>
            <span style={S.stripLabel}>Trace</span>
            {locus.traceId.slice(0, 10)}
          </span>
        ) : null}
        <button
          type="button"
          className="interp-ask"
          style={S.askButton}
          title="Ask the interpretability agent about this, in the Ask strip"
          onClick={() => askInterp(title)}
        >
          <IconComment />
          Ask about this
        </button>
      </div>
      <div style={S.body}>{children}</div>
    </div>
  );
}
