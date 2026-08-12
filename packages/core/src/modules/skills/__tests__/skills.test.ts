/**
 * The skills summary renderer, shared by `skills.status` and any chat surface.
 *
 * `summarize` is tested rather than the manifest because importing the manifest pulls
 * in the React pane and the registry, which reach WS-at-module-scope code with no
 * jsdom under vitest. The manifest's shape is covered by `pnpm typecheck`.
 *
 * What actually needs asserting: the four states a skill can be in are genuinely
 * different — working, disabled, shadowed, broken — and collapsing any of them into
 * "not working" would leave the user with no idea which lever to pull.
 */
import { describe, expect, it } from 'vitest';

import { summarize, type Skill, type SkillCost } from '../api';

function skill(overrides: Partial<Skill>): Skill {
  return {
    name: 'tidy',
    description: 'Tidy a file.',
    body: 'steps',
    allowedTools: [],
    scope: 'user',
    path: '/skills/tidy/SKILL.md',
    error: '',
    shadowed: false,
    enabled: true,
    ...overrides,
  };
}

const cost = (catalogTokens: number): SkillCost => ({
  skills: [],
  catalogTokens,
  exact: true,
  tokenizer: 'x',
});

describe('summarize', () => {
  it('tells the user how to make one when there are none', () => {
    expect(summarize([])).toContain('Skills: New skill');
  });

  it('leads with the per-turn cost, which nothing else in the app shows', () => {
    const text = summarize([skill({})], cost(180));
    expect(text).toContain('180 tokens every turn');
  });

  it('omits the cost line when skills cost nothing', () => {
    const text = summarize([skill({ enabled: false })], cost(0));
    expect(text).not.toContain('tokens every turn');
  });

  it('distinguishes disabled from broken from shadowed', () => {
    const text = summarize([
      skill({ name: 'a' }),
      skill({ name: 'b', enabled: false }),
      skill({ name: 'c', shadowed: true }),
      skill({ name: 'd', error: 'no `description`' }),
    ]);
    const lines = text.split('\n');
    expect(lines[1]).toContain('● a');
    expect(lines[2]).toContain('disabled');
    expect(lines[3]).toContain('shadowed');
    // The error text itself, not just "broken" — it names the fix.
    expect(lines[4]).toContain('no `description`');
  });

  it('reports an error even for a skill that is otherwise enabled', () => {
    const text = summarize([skill({ error: 'frontmatter is not valid YAML', enabled: true })]);
    expect(text).toContain('✕');
    expect(text).not.toContain('● tidy');
  });
});
