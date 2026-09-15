import { useCallback, useEffect, useRef, useState } from 'react';

import { envStatus, installLibraries, type NotebookLibraries } from './api';

/** Fast while something is happening; nothing at all once it has settled. */
const POLL_MS = 2000;

/**
 * The notebook venv's background library install, as the pane sees it.
 *
 * Polls only while the state can still change on its own (`idle` — the kernel open
 * is about to start an install — and `installing`), and stops at `ready`, `failed`
 * or `unmanaged`. `finished` is true when this pane watched an install complete, so
 * it can say "restart the kernel if an import still fails" exactly once, to the
 * person who saw the failure, rather than to everyone who ever opens a notebook.
 */
export function useLibraryInstall(active: boolean) {
  const [libraries, setLibraries] = useState<NotebookLibraries | null>(null);
  const [finished, setFinished] = useState(false);
  const [generation, setGeneration] = useState(0);
  const sawInstalling = useRef(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const res = await envStatus();
        if (cancelled) return;
        const state = res.libraries.state;
        if (state === 'installing') sawInstalling.current = true;
        if (state === 'ready' && sawInstalling.current) setFinished(true);
        setLibraries(res.libraries);
        if (state === 'idle' || state === 'installing') timer = setTimeout(tick, POLL_MS);
      } catch {
        // The backend being briefly unreachable says nothing about the install.
        if (!cancelled) timer = setTimeout(tick, POLL_MS * 5);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [active, generation]);

  const retry = useCallback(async () => {
    sawInstalling.current = true;
    setFinished(false);
    setLibraries((await installLibraries()).libraries);
    setGeneration((g) => g + 1);
  }, []);

  const dismiss = useCallback(() => setFinished(false), []);

  return { libraries, finished, retry, dismiss };
}
