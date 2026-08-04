import { SignInCard } from '../../../SignInCard';
import { useAccount } from '../../../useAccount';
import { ensureConnected } from '../game-ws';

/**
 * The Games client's front door: sign in, or you don't get in.
 *
 * This exists because the pane used to render the whole Lobby signed-out, and every
 * button on it was a dead end — **every** start flow in `matchmaking.ts` calls
 * `ensureConnected`, `playVsOwnAgent` included, and the hosted game server runs
 * `GAMES_ALLOW_DEV_AUTH=0`, so the dev token the node falls back to is refused. What
 * the user saw was `invalid token` on a screen that had just invited them to play.
 * The first-run hero even promised in so many words that play worked without an
 * account. It never did against that server.
 *
 * So the gate is unconditional, including against a local server that *would* accept
 * anonymous play: one rule is explainable, and "sometimes you need an account" is the
 * state that produced the bug.
 *
 * The sign-in itself is core's `SignInCard` — the same one HorribleAssault's boot
 * overlay uses. Do not grow a second copy of the OAuth dance here; that file's header
 * is the record of what happens when there are three.
 */
export function GamesSignIn() {
  const { server } = useAccount();

  return (
    <div className="games-gate">
      <div className="games-gate-body">
        <span className="games-eyebrow">Games</span>
        <h1 className="games-hero-title">
          Sign in to <em>play</em>.
        </h1>
        <p className="games-hero-sub">
          Matches, ratings, replays and tables all live on the game server, so an account
          isn&rsquo;t optional here — it&rsquo;s the seat you play from. Your callsign is derived
          automatically; you can rename it later.
        </p>

        {/* Naming the server is the difference between a fixable problem and a mystery:
            the node signs in and plays against the SAME url (resolve_server_url), so if
            this isn't the server you expect, that's the bug — not the sign-in. */}
        {server && (
          <p className="games-gate-server">
            server: <code>{server}</code>
          </p>
        )}

        {/* Reconnecting on success rather than waiting for the next click: the play
            socket authenticates on connect, so a fresh identity needs a fresh socket. */}
        <SignInCard
          className="games-gate-card"
          onSignedIn={() => {
            void ensureConnected(false).catch(() => {
              /* the channel's own error toast reports this; a failed warm-up
                 connect must not look like a failed sign-in */
            });
          }}
        />
      </div>
    </div>
  );
}

/** Shown while the node is still being asked who we are. Deliberately quiet — a
 * sign-in form that flashes up and vanishes reads as a bug. */
export function GamesAccountLoading() {
  return (
    <div className="games-gate">
      <div className="games-gate-body">
        <span className="games-eyebrow">Games</span>
        <p className="games-hero-sub">Checking your sign-in…</p>
      </div>
    </div>
  );
}

/**
 * The backend couldn't be reached. Distinct from a confident signed-out, and it has
 * to be: `refreshAccount` reports `unavailable` with `signedIn: false` on a cold
 * start, so treating it as signed-out would put a sign-in form in front of a user
 * whose node is simply down — and no amount of signing in would fix it.
 */
export function GamesNodeUnreachable({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="games-gate">
      <div className="games-gate-body">
        <span className="games-eyebrow">Games</span>
        <h1 className="games-hero-title">
          Can&rsquo;t reach this <em>node</em>.
        </h1>
        <p className="games-hero-sub">
          The backend didn&rsquo;t answer, so we can&rsquo;t tell whether you&rsquo;re signed in.
          This is the node itself, not the game server — check that the backend is running.
        </p>
        <div className="games-hero-actions">
          <button type="button" className="games-play-btn" onClick={onRetry}>
            Try again
          </button>
        </div>
      </div>
    </div>
  );
}
