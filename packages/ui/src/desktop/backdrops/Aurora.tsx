/**
 * The default backdrop: a slow procedural gradient mesh built entirely from
 * theme tokens, so it restyles with the theme and ships no imagery.
 *
 * Pure CSS rather than a canvas — three blurred radial gradients drifting on a
 * long keyframe cost nothing measurable and, unlike a rAF loop, keep working on
 * a desktop the user has left in the background for an hour.
 */
export function AuroraBackdrop({ params }: { params?: Record<string, unknown> }) {
  // `calm` drops the animation entirely (also what `prefers-reduced-motion`
  // does in CSS) — some people want a still background, and it is one class.
  const calm = params?.motion === 'calm';
  return (
    <div className={`os-backdrop-aurora${calm ? ' is-calm' : ''}`} aria-hidden="true">
      <span className="os-aurora-blob os-aurora-blob--a" />
      <span className="os-aurora-blob os-aurora-blob--b" />
      <span className="os-aurora-blob os-aurora-blob--c" />
    </div>
  );
}
