/**
 * Workgroup counts for a 1-D job of `n` items. One dimension holds at most
 * `maxComputeWorkgroupsPerDimension` (65,535 by default) workgroups — 16.7M items at
 * 256 per workgroup, which a vocabulary-sized f32 job can approach — so larger jobs
 * spill into y. Kernels recover the flat index as
 * `gid.x + gid.y * num_workgroups.x * WORKGROUP_SIZE` and bounds-check against `n`.
 */
export function dispatch1d(
  n: number,
  workgroupSize: number,
  maxPerDimension: number,
): [x: number, y: number] {
  const groups = Math.ceil(n / workgroupSize);
  if (groups <= maxPerDimension) return [Math.max(groups, 1), 1];
  const y = Math.ceil(groups / maxPerDimension);
  if (y > maxPerDimension) throw new Error(`${n} items is more than one dispatch can cover`);
  return [Math.ceil(groups / y), y];
}
