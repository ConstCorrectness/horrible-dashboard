import { useCallback, useEffect, useRef, useState } from 'react';

import { gameAccent, gameHeroImage, gameIcon, gameTagline } from '../game-identity';
import { type GameCatalogEntry } from '../games-api';
import { playVsOwnAgent } from '../matchmaking';
import { setActiveGame } from '../selected-game';

/**
 * The Play section's empty state: a full-bleed hero that cycles through the game
 * catalog with one **Quick Play** button on it.
 *
 * It replaces a text card that explained the sidebar ("← Please select a game from
 * the Games Library on the left to play"). Prose telling you where to click is a
 * design smell — the hero *is* the game list, one game at a time, and the button
 * starts the one you're looking at. Nothing to read, one thing to press.
 *
 * **Quick Play means self-play against your own agent** (`playVsOwnAgent`): it is the
 * only start flow that needs neither a sign-in nor the central game server, so the
 * button can never be a dead end for a first-time player. Ranked, bots and open
 * tables all stay where they are — one click deeper, on the selected game's card.
 * Every start still goes through `matchmaking.ts`; this adds a caller, not a path.
 */

/** How long each game holds the hero before the crossfade to the next one. */
const DWELL_MS = 6000;

export function GamesHero({
  games,
  setSelectedGame,
}: {
  games: GameCatalogEntry[];
  setSelectedGame: (id: string | null) => void;
}) {
  const [index, setIndex] = useState(0);
  // Auto-advance pauses while the pointer or keyboard focus is on the hero: the dots
  // are targets, and sliding the hero out from under someone aiming at one is hostile.
  const [held, setHeld] = useState(false);
  const reduce = usePrefersReducedMotion();

  // The catalog arrives async and can shrink (a game deregisters), so the index is
  // clamped on read rather than trusted — it is only ever a cursor into a live list.
  const count = games.length;
  const current = count > 0 ? games[index % count] : null;

  useEffect(() => {
    // Reduced motion gets a static hero rather than a slower one: an unattended,
    // indefinitely looping crossfade is exactly what the preference is asking to
    // stop. The dots still work, so every game is still reachable by hand.
    if (reduce || held || count < 2) return;
    const t = setInterval(() => setIndex((i) => (i + 1) % count), DWELL_MS);
    return () => clearInterval(t);
  }, [reduce, held, count]);

  const quickPlay = useCallback(() => {
    if (!current) return;
    // Mirror the sidebar's selection handshake so the pane doesn't end up showing the
    // hero for one game while the harness and board sit on another.
    setSelectedGame(current.id);
    setActiveGame(current.id);
    void playVsOwnAgent(current.id);
  }, [current, setSelectedGame]);

  if (!current) {
    // Catalog still loading (or the node is down and even the fallback entry hasn't
    // landed). No skeleton — the server browser below is the rest of the section.
    return null;
  }

  const accent = gameAccent(current.id);
  const art = gameHeroImage(current.id);

  return (
    <section
      className="games-hero"
      style={{ '--hero-accent': accent } as React.CSSProperties}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
      aria-label="Featured game"
    >
      {/* The art layer is keyed by game id so React swaps the node and the CSS
          animation replays — a crossfade needs a new element, not a restyled one. */}
      <div
        key={current.id}
        className={`games-hero-art${reduce ? '' : ' fading'}`}
        style={art ? { backgroundImage: `url(${art})` } : undefined}
        aria-hidden
      >
        {/* No artwork: the glyph is the art, oversized and bled off the right edge. */}
        {!art && <span className="games-hero-glyph">{gameIcon(current.id)}</span>}
      </div>
      <div className="games-hero-scrim" aria-hidden />

      <div className="games-hero-body">
        <p className="games-hero-eyebrow">Agent Arcade</p>
        {/* aria-live so the cycling headline is announced when it changes rather than
            silently swapping under a screen reader mid-read. */}
        <h2 className="games-hero-title" aria-live="polite">
          {current.name}
        </h2>
        <p className="games-hero-tagline">{gameTagline(current.id)}</p>

        <div className="games-hero-actions">
          <button type="button" className="games-hero-play" onClick={quickPlay}>
            ▶ Quick Play
          </button>
          <button
            type="button"
            className="games-hero-details"
            onClick={() => {
              setSelectedGame(current.id);
              setActiveGame(current.id);
            }}
          >
            Details & modes
          </button>

          {/* In the actions row rather than absolutely positioned in a corner: the
              dot count follows the catalog, so any floating row eventually wraps into
              the headline (it already clipped the eyebrow at a narrow dock width).
              In the flow it takes real space and can never overlap anything. */}
          {count > 1 && (
            <div className="games-hero-dots" role="tablist" aria-label="Featured games">
              {games.map((g, i) => (
                <button
                  key={g.id}
                  type="button"
                  role="tab"
                  aria-selected={i === index % count}
                  aria-label={g.name}
                  className={`games-hero-dot${i === index % count ? ' active' : ''}`}
                  onClick={() => setIndex(i)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/** Live `prefers-reduced-motion`, so toggling the OS setting takes effect without a
 * reload (the module's motion guard is only honest if it tracks the current value). */
function usePrefersReducedMotion(): boolean {
  const query = useRef<MediaQueryList | null>(null);
  query.current ??= matchMedia('(prefers-reduced-motion: reduce)');
  const [reduce, setReduce] = useState(query.current.matches);

  useEffect(() => {
    const mq = query.current;
    if (!mq) return;
    const onChange = () => setReduce(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);

  return reduce;
}
