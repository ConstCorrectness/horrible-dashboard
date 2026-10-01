import { RollingNumber } from '../../../DataList';
import type { Metric } from '../api';
import { formatMetric } from '../format';

/** One figure and what it measures. A `null` value is "—": unknown, not zero. */
export function MetricCell({ metric }: { metric: Metric }) {
  return (
    <span title={metric.label || undefined}>
      {metric.value === null ? (
        '—'
      ) : (
        <RollingNumber value={metric.value} format={(n) => formatMetric(metric, n)} />
      )}
      {metric.label && <span className="dc-metric-label"> {metric.label}</span>}
    </span>
  );
}
