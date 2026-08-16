/**
 * Live app-fullscreen state for the controls that show it.
 *
 * Reads through the core seam (`isAppFullscreen`), which resolves the native OS
 * window under Tauri and `document.fullscreenElement` in the browser, and
 * resubscribes to `fullscreenchange`. A control that only re-read on its own
 * click would show the wrong icon the moment the user left fullscreen any other
 * way — Escape in the browser, the OS window menu on desktop — which is most of
 * the ways people actually leave it.
 */
import { useCallback, useEffect, useState } from 'react';
import { isAppFullscreen, subscribeFullscreen, toggleAppFullscreen } from '@horrible/core';

export function useAppFullscreen(): { fullscreen: boolean; toggle: () => void } {
  const [fullscreen, setFullscreen] = useState(false);

  const refresh = useCallback(() => {
    let alive = true;
    void isAppFullscreen().then((on) => {
      if (alive) setFullscreen(on);
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const cancel = refresh();
    const unsubscribe = subscribeFullscreen(refresh);
    return () => {
      cancel();
      unsubscribe();
    };
  }, [refresh]);

  const toggle = useCallback(() => {
    // Set from the resolved value rather than flipping local state: the request
    // can be refused (the Fullscreen API outside a user gesture), and painting
    // the button as if it succeeded is how a control starts lying.
    void toggleAppFullscreen().then(setFullscreen);
  }, []);

  return { fullscreen, toggle };
}
