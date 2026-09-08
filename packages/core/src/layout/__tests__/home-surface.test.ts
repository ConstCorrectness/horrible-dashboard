/**
 * The floating-desktop transition.
 *
 * The home screen owns the desktop's single backdrop slot, so "put it away and
 * use the desktop" only became expressible once the home surface started
 * painting a wallpaper of its own underneath. These are the two rules that make
 * that hold, and both fail in ways no reviewer would catch by reading:
 *
 * - The underlay may never be the home surface or any other interactive
 *   backdrop. The first mounts `HomeView` inside `HomeView` forever; the second
 *   buries a surface that wants the pointer beneath one that takes it.
 * - Collapsing and choosing a wallpaper write into the *same* params bag, so
 *   either one written as a replacement silently discards the other.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_HOME_UNDERLAY,
  HOME_BACKDROP_ID,
  homeUnderlayId,
  isHomeCollapsed,
  resolveHomeUnderlay,
  setHomeCollapsed,
  setHomeUnderlay,
  toggleHomeCollapsed,
} from '../home-surface';
import { setBackdrop } from '../controller';
import { layoutStore } from '../store';

/** The registry's answer for the built-in set: everything decorative is usable,
 *  `splash` and `board` are interactive, and `ghost` was never registered. */
const usable = (id: string) => ['none', 'aurora', 'grid', 'image', 'pulse'].includes(id);

describe('resolveHomeUnderlay', () => {
  it('uses the default when the desktop has never chosen one', () => {
    expect(resolveHomeUnderlay(undefined, usable)).toBe(DEFAULT_HOME_UNDERLAY);
    expect(resolveHomeUnderlay({ collapsed: true }, usable)).toBe(DEFAULT_HOME_UNDERLAY);
  });

  it('honours the desktop’s choice', () => {
    expect(resolveHomeUnderlay({ under: 'grid' }, usable)).toBe('grid');
  });

  it('honours a deliberately flat surface rather than reading it as unset', () => {
    // `none` is a real provider that renders nothing. Falling back to the default
    // here would make "I want a plain background" unselectable.
    expect(resolveHomeUnderlay({ under: 'none' }, usable)).toBe('none');
  });

  it('never nests the home surface inside itself', () => {
    // Would mount HomeView inside HomeView, forever. The symptom is a hung tab,
    // not a wrong wallpaper, and one bad menu wiring is all it takes.
    expect(resolveHomeUnderlay({ under: HOME_BACKDROP_ID }, usable)).toBe(DEFAULT_HOME_UNDERLAY);
  });

  it('refuses another interactive backdrop', () => {
    // `board` under the home screen is two surfaces competing for the pointer in
    // one square of screen, the lower one silently unreachable.
    expect(resolveHomeUnderlay({ under: 'board' }, usable)).toBe(DEFAULT_HOME_UNDERLAY);
  });

  it('falls back for a backdrop whose plugin is gone', () => {
    expect(resolveHomeUnderlay({ under: 'ghost' }, usable)).toBe(DEFAULT_HOME_UNDERLAY);
  });

  it('paints nothing when even the default is unavailable', () => {
    expect(resolveHomeUnderlay({ under: 'ghost' }, () => false)).toBeNull();
  });
});

describe('the home surface’s params', () => {
  beforeEach(() => {
    setBackdrop({ id: HOME_BACKDROP_ID, params: {} });
  });

  it('collapses and restores', () => {
    expect(isHomeCollapsed()).toBe(false);
    toggleHomeCollapsed();
    expect(isHomeCollapsed()).toBe(true);
    toggleHomeCollapsed();
    expect(isHomeCollapsed()).toBe(false);
  });

  it('keeps the wallpaper when collapsing, and the collapse when choosing one', () => {
    setHomeUnderlay('grid');
    setHomeCollapsed(true);
    expect(homeUnderlayId()).toBe('grid');
    expect(isHomeCollapsed()).toBe(true);

    setHomeUnderlay('pulse');
    expect(isHomeCollapsed()).toBe(true);
    expect(homeUnderlayId()).toBe('pulse');
  });

  it('reports nothing collapsed on a desktop showing another backdrop', () => {
    setBackdrop({ id: HOME_BACKDROP_ID, params: { collapsed: true } });
    setBackdrop({ id: 'aurora' });
    // There is nothing collapsed about a home screen that is not on: an item
    // labelled "Show the home screen" here would switch backdrops as a side
    // effect of a verb that claims to restore one.
    expect(isHomeCollapsed()).toBe(false);
  });

  it('restores the home surface when the collapse is written from another backdrop', () => {
    setBackdrop({ id: 'aurora' });
    setHomeCollapsed(false);
    expect(layoutStore.getSnapshot().frame.backdrop.id).toBe(HOME_BACKDROP_ID);
  });
});
