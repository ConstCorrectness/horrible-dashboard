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

import { useModelLocus, type ModelLocus } from '../../model-locus';
import { interpretabilityStore } from '../interpretability/store';
import { S } from './styles';

export function locusLabel(locus: ModelLocus): string | null {
  const parts: string[] = [];
  if (typeof locus.layer === 'number') parts.push(locus.layer === -1 ? 'EMB' : `L${locus.layer}`);
  if (typeof locus.position === 'number') parts.push(`P${locus.position}`);
  if (locus.stage) parts.push(locus.stage);
  return parts.length ? parts.join(' · ') : null;
}

export function SuiteFrame({ title, children }: { title: string; children: ReactNode }) {
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  const locus = useModelLocus();
  const where = locusLabel(locus);
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
      </div>
      <div style={S.body}>{children}</div>
    </div>
  );
}
