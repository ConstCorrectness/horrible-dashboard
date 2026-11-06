/**
 * The damage arrows (`damage-arrows.ts`) drawn around the crosshair.
 *
 * One SVG at the centre of the game view. Each arrow is mounted once, keyed by
 * its id, and fades on a CSS animation whose stops trace `arrowAlpha`'s square
 * curve — so the fade runs on the compositor and needs no re-render, while the
 * *direction* is recomputed from `yaw` on every HUD render and swings round as
 * the player turns, exactly as native's does.
 */
import { useSetting } from '../../settings';
import { hudUnit } from './crosshair';
import {
  ARROW_ALPHA,
  arrowTriangle,
  DAMAGE_ARROW_LIFE_MS,
  type DamageArrow,
} from './damage-arrows';
import { HUD_SCALE_KEY } from './menu-panels';

// (1 - t)^2 at quarter steps, scaled by the peak: the same curve `arrowAlpha`
// computes, sampled finely enough that linear segments between stops cannot be
// told from it at this size.
const STOPS = [0, 0.25, 0.5, 0.75, 1]
  .map((t) => `${t * 100}% { opacity: ${(ARROW_ALPHA * (1 - t) ** 2).toFixed(4)}; }`)
  .join(' ');
const KEYFRAMES = `@keyframes hx-damage-arrow { ${STOPS} }`;

export function DamageArrows({ arrows, yaw }: { arrows: readonly DamageArrow[]; yaw: number }) {
  const hudScale = useSetting<number>(HUD_SCALE_KEY) ?? 1;
  if (arrows.length === 0) return null;
  // The HUD unit native computes from its window, from ours: the game view is
  // the whole window now that nobody plays inside the dashboard pane.
  const unit = hudUnit(window.innerHeight, hudScale);
  return (
    <svg
      aria-hidden
      width={0}
      height={0}
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        overflow: 'visible',
        pointerEvents: 'none',
      }}
    >
      <style>{KEYFRAMES}</style>
      {arrows.map((a) => {
        const [tip, left, right] = arrowTriangle(a.bearing, yaw, unit);
        // No delay, and none computed from the arrow's age either: the style
        // is re-applied on every HUD render, and a delay that changed between
        // renders would move the running fade each time. An arrow is mounted
        // on the render its snapshot caused, so it starts at full strength.
        return (
          <polygon
            key={a.id}
            points={`${tip[0]},${tip[1]} ${left[0]},${left[1]} ${right[0]},${right[1]}`}
            fill="rgb(247, 82, 71)"
            style={{
              opacity: 0,
              animation: `hx-damage-arrow ${DAMAGE_ARROW_LIFE_MS}ms linear forwards`,
            }}
          />
        );
      })}
    </svg>
  );
}
