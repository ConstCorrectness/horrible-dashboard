import type { ModelLocus } from '../../model-locus';

/** "L17 · P42 · attention", or null when nothing is selected. */
export function locusLabel(locus: ModelLocus): string | null {
  const parts: string[] = [];
  if (typeof locus.layer === 'number') parts.push(locus.layer === -1 ? 'EMB' : `L${locus.layer}`);
  if (typeof locus.position === 'number') parts.push(`P${locus.position}`);
  if (locus.stage) parts.push(locus.stage);
  return parts.length ? parts.join(' · ') : null;
}
