import { ArmoryMarketplace } from '@horrible/core';
import type { AuthState } from '../auth';

/** Which face the armory shows: it is account-only on the game server. */
export type ArmoryView = 'checking' | 'guest' | 'account';

/**
 * Inventory, wallet, cases and trades are all kept against an account
 * (`hassault_account.py`, `hassault_wallet.py`, `hassault_trading.py` answer a
 * request with no bearer token 401), so a guest is shown why rather than an
 * armory that fails every call. A token being checked is neither: showing the
 * prompt for that second would flash it at every returning player.
 */
export function armoryView(auth: Pick<AuthState, 'token' | 'account' | 'checking'>): ArmoryView {
  if (auth.checking) return 'checking';
  return auth.token && auth.account ? 'account' : 'guest';
}

export function ArmorySection({
  auth,
  onSignIn,
}: {
  auth: Pick<AuthState, 'token' | 'account' | 'checking'>;
  onSignIn: () => void;
}) {
  const view = armoryView(auth);
  return (
    <>
      <div className="section-head">
        <div>
          <h2 className="display section-title">Armory</h2>
          <p className="section-sub">
            Your skins, the case store and trading. Kept on your account, so the desktop app shows
            the same loadout.
          </p>
        </div>
      </div>

      {view === 'checking' ? (
        <div className="servers">
          <div className="empty">
            <span className="mono">Checking your account…</span>
          </div>
        </div>
      ) : view === 'guest' ? (
        <div className="servers armory-guest" data-armory="guest">
          <div className="empty">
            <div>
              <div className="display">Sign in to open your armory</div>
              <p>
                Skins, the wallet, cases and trades belong to an account. Guest matches earn none of
                them.
              </p>
            </div>
            <button type="button" className="btn" onClick={onSignIn}>
              Sign in
            </button>
          </div>
        </div>
      ) : (
        // Keyed on the account: signing out and in as someone else must not
        // show the previous player's inventory while the new one loads.
        <div className="armory-host" data-armory="account" key={auth.account?.id}>
          <ArmoryMarketplace studio={false} />
        </div>
      )}
    </>
  );
}
