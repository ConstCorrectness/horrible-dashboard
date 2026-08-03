import { useState, type ReactNode } from 'react';
import {
  setSetting,
  SignInCard,
  useAccount,
  useConnectors,
  type AgentStatus,
} from '@horrible/core';

import { IntegrationRow } from './IntegrationRow';
import { OnboardingCard } from './OnboardingCard';
import { SETUP_DISMISSED_KEY } from './constants';

/**
 * The intro page's one setup flow: get a model, get an account, connect your tools.
 *
 * These three things were previously in three different places and only one of
 * them was on the home page — the model card. Signing in existed solely inside the
 * games lobby and HorribleAssault's front door, which meant the way to discover
 * that this app has an account at all was to open a game. The connector tiles sat
 * above the ask bar with no explanation of what they were for.
 *
 * **The account and the connectors are two steps on purpose.** They are not the
 * same kind of credential and collapsing them would quietly change what a click
 * grants. Signing in is identity: the game server keeps a JWT, discards the
 * provider's token, and can read nothing of yours. A connector is access: your
 * node holds a real provider token, encrypted, and hands it to agent tools. One
 * button doing both would grant `repo` scope to answer "who are you", and the two
 * halves can still fail independently — which is much harder to explain after the
 * fact than it is to show as two steps up front.
 */
export function SetupCard({
  status,
  onChanged,
}: {
  /** Agent status, or null when the agent is already configured and reachable. */
  status: AgentStatus | null;
  onChanged: () => void;
}) {
  const { signedIn, account, phase: accountPhase } = useAccount();
  const { connectors, phase: connectorsPhase } = useConnectors();
  const [dismissing, setDismissing] = useState(false);

  const modelDone = status === null;
  // `unavailable` (backend down) is not "signed out" — don't nag about a step
  // whose state we couldn't read. Home already says the backend is unreachable.
  const accountDone = signedIn || accountPhase === 'unavailable';
  const connected = connectors.filter((c) => c.connected);
  const toolsDone = connected.length > 0 || connectorsPhase === 'unavailable';

  if (modelDone && accountDone && toolsDone) return null;

  const dismiss = () => {
    setDismissing(true);
    void setSetting(SETUP_DISMISSED_KEY, true);
  };
  if (dismissing) return null;

  return (
    <section className="setup-card" aria-label="Get set up">
      <header className="setup-card-head">
        <h2>Get set up</h2>
        <button type="button" className="setup-dismiss" onClick={dismiss}>
          dismiss
        </button>
      </header>

      <Step
        n={1}
        title="Your local model"
        done={modelDone}
        doneNote={status === null ? 'Agent is configured and reachable.' : undefined}
      >
        {status && <OnboardingCard status={status} onChanged={onChanged} />}
      </Step>

      <Step
        n={2}
        title="Your account"
        done={accountDone}
        doneNote={
          signedIn
            ? `Signed in as ${account?.handle || account?.display_name || 'you'}.`
            : undefined
        }
      >
        <p className="setup-step-blurb">
          One account for the games ladder, HorribleAssault, and your identity on the peer fabric.
          Sign-in proves who you are and nothing more — the provider&rsquo;s token is discarded, not
          stored.
        </p>
        <SignInCard />
      </Step>

      <Step
        n={3}
        title="Connect your tools"
        done={toolsDone}
        doneNote={
          connected.length > 0
            ? `Connected: ${connected.map((c) => c.label).join(', ')}.`
            : undefined
        }
      >
        <p className="setup-step-blurb">
          What your agent can reach. Each one is optional, and its credential stays encrypted on
          this machine — the browser never receives it.
        </p>
        <IntegrationRow />
      </Step>
    </section>
  );
}

/** One numbered step. A finished step collapses to its check and a one-line
 * summary rather than disappearing, so the flow doesn't reflow under the user
 * and they can see what they already have. */
function Step({
  n,
  title,
  done,
  doneNote,
  children,
}: {
  n: number;
  title: string;
  done: boolean;
  doneNote?: string;
  children: ReactNode;
}) {
  return (
    <div className={`setup-step${done ? ' done' : ''}`}>
      <div className="setup-step-head">
        <span className="setup-step-dot">{done ? '✓' : n}</span>
        <span className="setup-step-title">{title}</span>
        {done && doneNote && <span className="setup-step-note">{doneNote}</span>}
      </div>
      {!done && <div className="setup-step-body">{children}</div>}
    </div>
  );
}
