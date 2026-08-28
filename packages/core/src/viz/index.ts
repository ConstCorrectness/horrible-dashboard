/**
 * The visualization primitives. See `viz.css` for what each one is doing and why.
 *
 * These live in `core` rather than `ui` for the reason `Avatar3D` does: `ui`
 * imports `core`, and every consumer of these is a core module, so the other
 * direction would be a cycle.
 */
export { Sparkline, type SparklineProps } from './Sparkline';
export { ValueStrip, STRIP_CELLS, type ValueStripProps } from './ValueStrip';
export { Meter, type MeterProps, type MeterSegment, type MeterTone } from './Meter';
export { ProgressBar, type ProgressBarProps } from './ProgressBar';
export { RollingCounter, type RollingCounterProps } from './RollingCounter';
export { HeatCanvas, type HeatCanvasProps } from './HeatCanvas';
export { chartColors, subscribeThemeColors, type ChartColors } from './uplot-theme';
export { sparkRuns, sharedDomain, type SparkPoint, type SparkGeometry } from './spark';
export { rampCell, rampProps, rampScale, RAMP_FLOOR, RAMP_RANGE, type RampCell, type RampSign } from './ramp';
export { poolMean, poolMaxAbs, type Pooled } from './pool';
