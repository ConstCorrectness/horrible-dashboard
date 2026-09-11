import { describe, expect, it } from 'vitest';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { CrossfireKillBadges, CrossfireHeaderHUD } from '../CrossfireKillBadges';

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
