/**
 * Runs other people have shared: one section, two audiences.
 *
 * These used to be two tabs named **Friends** and **Commons**, which read as a
 * second friends list and a second social network sitting in a trace viewer. Both
 * are the same thing seen from this pane — *trajectories that came from somewhere
 * else* — and differ only in who shared them and how much they carry:
 *
 * - **Friends** — whole runs a friend's node offers, browsed live on their machine
 *   and pulled here as a copy (`PeersSection`).
 * - **Public** — digests on the agent commons index: a run's *shape* from anyone,
 *   never its payloads (`CommonsSection`).
 *
 * The switch is in the section header rather than in a tab strip of its own, so
 * the pane's strip names what you are looking at (trajectories) instead of who you
 * are looking at them with. The choice is remembered per viewer.
 */
import { useState } from 'react';

import { Button } from '../../../Primitives';
import { CommonsSection } from './CommonsSection';
import { PeersSection } from './PeersSection';

type Scope = 'friends' | 'public';

const KEY = 'trajectories.shared.scope';

function loadScope(): Scope {
  try {
    return window.localStorage.getItem(KEY) === 'public' ? 'public' : 'friends';
  } catch {
    return 'friends';
  }
}

export function SharedSection({ onPulled }: { onPulled: () => void }) {
  const [scope, setScopeState] = useState<Scope>(loadScope);
  const setScope = (next: Scope) => {
    setScopeState(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // Not remembered; the section still switches.
    }
  };

  const switcher = (
    <>
      <Button
        size="sm"
        intent={scope === 'friends' ? 'primary' : 'ghost'}
        title="Whole runs your friends share, pulled as a copy"
        onClick={() => setScope('friends')}
      >
        Friends
      </Button>
      <Button
        size="sm"
        intent={scope === 'public' ? 'primary' : 'ghost'}
        title="Run digests on the commons index — shape only, from anyone"
        onClick={() => setScope('public')}
      >
        Public
      </Button>
    </>
  );

  return scope === 'friends' ? (
    <PeersSection onPulled={onPulled} switcher={switcher} />
  ) : (
    <CommonsSection switcher={switcher} />
  );
}
