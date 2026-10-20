import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

// The real armory is core's dashboard panel — and `@horrible/core` is the whole
// dashboard barrel. What is under test here is which face the section shows, so
// the panel is a marker.
vi.mock('@horrible/core', () => ({
  ArmoryMarketplace: ({ studio }: { studio?: boolean }) => (
    <div data-testid="armory-panel" data-studio={String(studio)} />
  ),
}));

import { ArmorySection, armoryView } from './ArmorySection';

const account = { id: 'acct-1', display_name: 'Rob', handle: 'rob' };

describe('armoryView', () => {
  it('waits while a stored token is being checked', () => {
    expect(armoryView({ token: 't', account: null, checking: true })).toBe('checking');
  });

  it('is the guest prompt with no token', () => {
    expect(armoryView({ token: null, account: null, checking: false })).toBe('guest');
  });

  it('is the guest prompt when the token never resolved to an account', () => {
    // Offline at boot: `refreshAccount` keeps the token but has no account, and
    // every armory call would go out unauthenticated as far as the UI knows.
    expect(armoryView({ token: 't', account: null, checking: false })).toBe('guest');
  });

  it('opens for a signed-in account, with or without a handle yet', () => {
    expect(armoryView({ token: 't', account, checking: false })).toBe('account');
    expect(armoryView({ token: 't', account: { ...account, handle: null }, checking: false })).toBe(
      'account',
    );
  });
});

describe('ArmorySection', () => {
  const render = (auth: Parameters<typeof armoryView>[0]) =>
    renderToStaticMarkup(<ArmorySection auth={auth} onSignIn={() => {}} />);

  it('shows guests a sign-in prompt and no armory', () => {
    const html = render({ token: null, account: null, checking: false });
    expect(html).toContain('data-armory="guest"');
    expect(html).toContain('Sign in to open your armory');
    expect(html).not.toContain('armory-panel');
  });

  it('shows a signed-in player the armory, without the dashboard-only studio', () => {
    const html = render({ token: 't', account, checking: false });
    expect(html).toContain('data-armory="account"');
    expect(html).toContain('data-testid="armory-panel"');
    expect(html).toContain('data-studio="false"');
    expect(html).not.toContain('Sign in to open your armory');
  });

  it('shows neither while the account is being checked', () => {
    const html = render({ token: 't', account: null, checking: true });
    expect(html).not.toContain('armory-panel');
    expect(html).not.toContain('Sign in to open your armory');
  });
});
