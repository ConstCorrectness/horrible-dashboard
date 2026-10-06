import { useEffect } from 'react';

import { setSignInViaHub } from './account';
import { SignInCard } from './SignInCard';
import './hublogin.css';

/**
 * The hosted dashboard's front door — what a signed-out browser sees instead of
 * the shell (see `probeHub` in hosted.ts and the web entry).
 *
 * It is the app's one sign-in card, pointed at the hub: the accounts are the same
 * game-server accounts, the flows the same three, but a successful sign-in here
 * gives *this browser* a session on the hub and starts the person's own dashboard
 * instance, rather than signing a machine in. On success the page reloads, and the
 * boot goes the signed-in way.
 */
export function HubLogin({ host = window.location.host }: { host?: string }) {
  // During render, not in an effect: the card's own mount effect asks which
  // providers exist, and a child's effects run before its parent's — switching here
  // in an effect sent that first request to the node, which 401s behind a hub.
  // The effect sets it again because StrictMode runs a mount → cleanup → mount
  // cycle, and the cleanup in between would otherwise leave it off.
  setSignInViaHub(true);
  useEffect(() => {
    setSignInViaHub(true);
    return () => setSignInViaHub(false);
  }, []);

  return (
    <main className="hublogin">
      <section className="hublogin-brand" aria-label="About this dashboard">
        <p className="hublogin-kicker">Hosted workspace</p>
        <h1 className="hublogin-title">Horrible Dashboard</h1>
        <p className="hublogin-lead">
          Sign in with your games account. Your dashboard runs in its own isolated instance &mdash;
          your settings, files, keys and terminal are yours alone.
        </p>
        <dl className="hublogin-meta">
          <div>
            <dt>Host</dt>
            <dd>{host}</dd>
          </div>
          <div>
            <dt>Account</dt>
            <dd>games / email &middot; github &middot; google</dd>
          </div>
          <div>
            <dt>Session</dt>
            <dd>httponly cookie &middot; 30d</dd>
          </div>
        </dl>
      </section>
      <section className="hublogin-panel" aria-label="Sign in">
        <h2 className="hublogin-panel-title">Sign in</h2>
        <SignInCard onSignedIn={() => window.location.reload()} />
      </section>
    </main>
  );
}
