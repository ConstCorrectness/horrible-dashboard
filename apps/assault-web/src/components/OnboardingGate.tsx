import { useAuth } from '../auth';
import { CallsignField } from './CallsignField';
import { Wordmark } from './Logo';
import { MapBackdrop } from './MapBackdrop';
import { SignInPanel } from './SignInDialog';

/**
 * The first thing a new visitor sees: sign in, or say plainly that you are
 * playing as a guest — and under what name.
 *
 * Signing in is the path the page leads with, because it is the one that keeps
 * anything (a username, a record, rated matches). Guest play stays one click
 * away at the bottom, never hidden, with the callsign as a real field so a guest
 * knows from the first screen that the name is theirs to change.
 *
 * Shown until the browser is signed in with a username or has chosen guest
 * play once (`guestChosen`). A shared match link waits behind it: the deploy
 * screen it asked for is still there when the gate is passed.
 */
export function OnboardingGate({
  callsign,
  onCallsignChange,
  onGuest,
  onSignedIn,
}: {
  callsign: string;
  onCallsignChange: (name: string) => void;
  onGuest: () => void;
  onSignedIn: () => void;
}) {
  const { account } = useAuth();
  // Signed in through OAuth but no username yet: finish that, not sign in again.
  const step = account && !account.handle ? 'handle' : 'in';

  return (
    <div className="page gate">
      <MapBackdrop mapId="hd_dust2" />
      <div className="wrap gate-grid">
        <div className="gate-pitch rise">
          <Wordmark size={30} />
          <h1 className="display gate-title">
            Pick a name.
            <span>Then drop in.</span>
          </h1>
          <ul className="operator-perks">
            <li>A username that's yours on every server</li>
            <li>Rated matches and a match history</li>
            <li>The same account as the desktop app</li>
          </ul>
        </div>

        <div className="dialog gate-card rise" style={{ ['--i' as string]: 2 }}>
          <SignInPanel key={step} initialStep={step} onDone={onSignedIn} />
          <div className="gate-guest">
            <div className="divider">or play without an account</div>
            <form
              className="gate-guest-row"
              onSubmit={(e) => {
                e.preventDefault();
                onGuest();
              }}
            >
              <CallsignField callsign={callsign} onChange={onCallsignChange} />
              <button type="submit" className="btn">
                Continue as guest
              </button>
            </form>
            <p className="hint">Guest matches are unrated, and the name isn't reserved for you.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
