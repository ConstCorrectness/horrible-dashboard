import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import {
  BADGE_TTL_MS,
  badgeAge,
  CrossfireKillBadges,
  CrossfireHeaderHUD,
} from '../CrossfireKillBadges';
import { deckAnimation } from '../panels/KillCardDeck';
import { ROUND_KILL_TTL_MS } from '../session';

describe('KillCardDeck animation', () => {
  it('slides a fresh card in but not one being re-shown after a remount', () => {
    expect(deckAnimation(1000, 1000, 1050).card).toContain('killCardSlideUp');
    expect(deckAnimation(1000, 1000, 3000).card).toBe('none');
  });

  it('resumes the fade where the deck had reached', () => {
    // 1s before the session drops it: 600ms of a 400ms fade still to wait.
    expect(deckAnimation(0, 0, ROUND_KILL_TTL_MS - 1000).deck).toContain('600ms forwards');
    // 100ms into the fade.
    expect(deckAnimation(0, 0, ROUND_KILL_TTL_MS - 300).deck).toContain('-100ms forwards');
  });
});

describe('CrossfireKillBadges', () => {
  it('renders hitmarker when hit is true', () => {
    const html = renderToString(<CrossfireKillBadges badge={null} hit={true} />);
    expect(html).toContain('<svg');
  });

  it('renders headshot badge with banner text', () => {
    const badge = {
      id: 1,
      type: 'headshot' as const,
      timestamp: Date.now(),
      streak: 1,
    };
    const html = renderToString(<CrossfireKillBadges badge={badge} hit={false} />);
    expect(html).toContain('HEADSHOT!');
  });

  it('does not replay an expired badge when the HUD remounts', () => {
    // The HUD unmounts with pointer lock. Coming back from the Escape menu used
    // to mount it with the last kill's badge and pop it in again as if new.
    const stale = {
      id: 2,
      type: 'knife' as const,
      timestamp: Date.now() - BADGE_TTL_MS - 1,
      streak: 1,
    };
    const html = renderToString(<CrossfireKillBadges badge={stale} hit={false} />);
    expect(html).not.toContain('KNIFE KILL');
  });

  it('ages a badge off its timestamp, not its mount', () => {
    const badge = { id: 3, type: 'kill' as const, timestamp: 10_000, streak: 1 };
    expect(badgeAge(badge, 10_500)).toBe(500);
    expect(badgeAge(badge, 10_000 + BADGE_TTL_MS)).toBeNull();
    expect(badgeAge(null, 10_000)).toBeNull();
  });

  it('renders tactical match header with teams and bomb sites', () => {
    const html = renderToString(
      <CrossfireHeaderHUD
        scoreGR={5}
        scoreBL={3}
        roundTimerSeconds={95}
        bombPlantedSite="A"
        myTeam={1}
      />,
    );
    expect(html).toContain('GLOBAL RISK');
    expect(html).toContain('BLACK LIST');
    expect(html).toContain('5');
    expect(html).toContain('3');
    expect(html).toContain('[A]');
    expect(html).toContain('[B]');
    expect(html).toContain('1:35');
  });
});
