/**
 * The pre-desktop home screen — avatar, ask bar, connector tiles — as a
 * backdrop.
 *
 * This is how `home` survives the desktop refactor rather than being deleted:
 * the surface people were used to landing on is still there, now with windows
 * floating over it. It is `interactive`, because its whole point is the ask bar.
 */
import { HomeView } from '../../HomeView';

export function SplashBackdrop() {
  return (
    <div className="os-backdrop-splash">
      <HomeView />
    </div>
  );
}
