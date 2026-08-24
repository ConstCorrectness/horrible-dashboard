/**
 * First-run setup, before the desktop.
 *
 * Four steps: who you are, how the desktop starts, what it looks like, and what
 * it can reach. The last
 * is the **existing** `SetupCard` rather than a second copy of it — model,
 * account and connectors already had one flow with a carefully argued shape (the
 * account and the connectors are deliberately two steps, see SetupCard), and a
 * first-run rewrite of it would be a second thing to keep in step with reality.
 *
 * Every step is skippable. A setup wizard you cannot leave is a setup wizard
 * people close the app to escape, and each of these has a home in settings.
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import {
  applyTheme,
  getAgentStatus,
  layoutStore,
  registry,
  setBackdrop,
  setSetting,
  toastsStore,
  THEMES,
  THEME_SETTING_KEY,
  useThemeId,
  type AgentStatus,
} from '@horrible/core';

import { getUserName, setUserName } from '../home/constants';
import { SetupCard } from '../home/SetupCard';
import { OOBE_COMPLETE_KEY } from './constants';

type Step = 'welcome' | 'start' | 'look' | 'setup';
/** `start` sits before `look` on purpose: what the desktop *is* is decided
 *  before what it looks like. */
const ORDER: Step[] = ['welcome', 'start', 'look', 'setup'];

export function Oobe({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>('welcome');
  const [name, setName] = useState(getUserName);
  const index = ORDER.indexOf(step);

  // The name is committed on the way out of the wizard rather than per keystroke:
  // it is a persisted setting now, and a PUT per character would be absurd.
  const commit = () => setUserName(name);

  // Awaited, and only *then* is the wizard dismissed. Firing the write and
  // navigating away is how "I already did this" became a wizard that reappeared
  // on the next launch: nothing surfaced a failed or unsent PUT, and the flag it
  // was supposed to set is the only thing standing between the user and a second
  // run of setup. A backend that refuses still lets the user through — the app
  // works offline — but it says so instead of silently forgetting.
  const finish = () => {
    void Promise.all([commit(), setSetting(OOBE_COMPLETE_KEY, true)])
      .catch((err) => {
        toastsStore.add(
          'warning',
          "Couldn't save your setup",
          `The backend didn't accept it, so first-run setup may appear again. ${String(err)}`,
          0,
        );
      })
      .finally(onDone);
  };
  const next = () => {
    if (index >= ORDER.length - 1) return finish();
    void commit();
    setStep(ORDER[index + 1]);
  };

  return (
    <div className="os-oobe">
      <div className="os-oobe-card">
        <ol className="os-oobe-progress" aria-label="Setup progress">
          {ORDER.map((s, i) => (
            <li key={s} className={i === index ? 'is-current' : i < index ? 'is-done' : ''} />
          ))}
        </ol>

        {step === 'welcome' && <Welcome name={name} onName={setName} />}
        {step === 'start' && <StartStep />}
        {step === 'look' && <Look />}
        {step === 'setup' && <SetupStep />}

        <footer className="os-oobe-actions">
          {/* "Skip setup" all the way out, not just past this step: someone who
              knows they want none of this should not have to click through
              every screen to say so. */}
          <button type="button" className="os-oobe-skip" onClick={finish}>
            Skip setup
          </button>
          {index > 0 && (
            <button
              type="button"
              onClick={() => {
                void commit();
                setStep(ORDER[index - 1]);
              }}
            >
              Back
            </button>
          )}
          <button type="button" className="os-oobe-next" onClick={next}>
            {index >= ORDER.length - 1 ? 'Finish' : 'Continue'}
          </button>
        </footer>
      </div>
    </div>
  );
}

function Welcome({ name, onName }: { name: string; onName: (name: string) => void }) {
  return (
    <section className="os-oobe-step">
      <h1>Welcome</h1>
      <p>
        One app for everything: panes you arrange yourself, an agent that can drive them, and a node
        that runs on your own machine.
      </p>
      <label className="os-oobe-field">
        <span>What should I call you?</span>
        <input
          value={name}
          autoFocus
          placeholder="Optional"
          onChange={(e) => onName(e.target.value)}
        />
      </label>
    </section>
  );
}

/** The id of the backdrop this desktop is currently showing. */
function useBackdropId(): string {
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  return frame.backdrop.id;
}

/**
 * What the desktop opens on.
 *
 * Its own step, deliberately separated from "Pick a look". This is a structural
 * choice — whether the app has a front door at all — and it used to sit fifth of
 * seven in a list of wallpapers, weighted identically to a decorative gradient.
 * A user picking a pretty backdrop had no way to know they were also switching
 * off the avatar, the ask bar and the connector tiles.
 *
 * The options are the two `interactive` backdrops (the ones that render the
 * node's own state and can be worked in) plus "nothing", which then hands over
 * to the cosmetic step. Decoration is chosen later, once this is settled.
 */
function StartStep() {
  const current = useBackdropId();
  const backdrops = registry.backdrops;
  const interactive = backdrops.filter((b) => b.interactive);
  // "Just a wallpaper" is any non-interactive backdrop. Landing on the previous
  // default keeps the old behaviour available in one click.
  const plain = current === 'none' || !interactive.some((b) => b.id === current);

  return (
    <section className="os-oobe-step">
      <h1>How should the desktop start?</h1>
      <p>
        Windows open on top either way. You can change this whenever you like by right-clicking the
        desktop.
      </p>

      <div className="os-oobe-choices">
        {interactive.map((b) => (
          <button
            key={b.id}
            type="button"
            className={`os-oobe-choice${b.id === current ? ' is-active' : ''}`}
            aria-pressed={b.id === current}
            onClick={() => setBackdrop({ id: b.id })}
          >
            <strong>{b.title}</strong>
            <span>{b.description}</span>
          </button>
        ))}
        <button
          type="button"
          className={`os-oobe-choice${plain ? ' is-active' : ''}`}
          aria-pressed={plain}
          onClick={() => setBackdrop({ id: 'aurora' })}
        >
          <strong>Just a wallpaper</strong>
          <span>An empty desktop. Everything opens from the Start menu.</span>
        </button>
      </div>
    </section>
  );
}

function Look() {
  const themeId = useThemeId();
  const current = useBackdropId();
  // The structural backdrops were promoted to their own step, so this one is
  // only decoration — which is what "Pick a look" always implied it was.
  const decorative = registry.backdrops.filter((b) => !b.interactive);
  return (
    <section className="os-oobe-step">
      <h1>Pick a look</h1>
      <p>
        Both of these are changeable later — themes in settings, the backdrop by right-clicking the
        desktop.
      </p>

      <h2>Theme</h2>
      <div className="os-oobe-choices">
        {THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`os-oobe-choice${t.id === themeId ? ' is-active' : ''}`}
            aria-pressed={t.id === themeId}
            // Applied immediately as well as saved: picking a theme in a setup
            // flow and seeing nothing change until you finish is indistinguishable
            // from the button not working.
            onClick={() => {
              applyTheme(t.id);
              void setSetting(THEME_SETTING_KEY, t.id);
            }}
          >
            <strong>{t.title}</strong>
            <span>{t.description}</span>
          </button>
        ))}
      </div>

      <h2>Desktop backdrop</h2>
      {/* These carried no selected state at all: `is-active` was applied to the
          theme buttons and simply forgotten here, so clicking one confirmed
          nothing and the wizard covers the desktop you would otherwise see
          change. Choosing something and being unable to tell whether it took is
          the worst possible outcome on the screen that teaches the app. */}
      <div className="os-oobe-choices">
        {decorative.map((b) => (
          <button
            key={b.id}
            type="button"
            className={`os-oobe-choice${b.id === current ? ' is-active' : ''}`}
            aria-pressed={b.id === current}
            onClick={() => setBackdrop({ id: b.id })}
          >
            <strong>{b.title}</strong>
            <span>{b.description}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function SetupStep() {
  const [status, setStatus] = useState<AgentStatus | null | 'loading'>('loading');
  const refresh = () =>
    getAgentStatus()
      .then((s) => setStatus(s.configured && s.reachable ? null : s))
      // A backend that is not answering must not block first-run setup: the rest
      // of the app works offline, and this step has a home in settings anyway.
      .catch(() => setStatus(null));
  useEffect(() => {
    void refresh();
  }, []);

  return (
    <section className="os-oobe-step">
      <h1>Get set up</h1>
      {status === 'loading' ? (
        <p>Checking what you already have…</p>
      ) : status === null ? (
        <p>Everything is configured. You can change any of it later in settings.</p>
      ) : (
        <SetupCard status={status} onChanged={refresh} />
      )}
    </section>
  );
}
