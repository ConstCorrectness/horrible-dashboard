/**
 * First-run setup, before the desktop.
 *
 * Three steps: who you are, what it looks like, and what it can reach. The third
 * is the **existing** `SetupCard` rather than a second copy of it — model,
 * account and connectors already had one flow with a carefully argued shape (the
 * account and the connectors are deliberately two steps, see SetupCard), and a
 * first-run rewrite of it would be a second thing to keep in step with reality.
 *
 * Every step is skippable. A setup wizard you cannot leave is a setup wizard
 * people close the app to escape, and each of these has a home in settings.
 */
import { useEffect, useState } from 'react';
import {
  applyTheme,
  getAgentStatus,
  registry,
  setBackdrop,
  setSetting,
  THEMES,
  THEME_SETTING_KEY,
  useThemeId,
  type AgentStatus,
} from '@horrible/core';

import { NAME_KEY } from '../home/constants';
import { SetupCard } from '../home/SetupCard';
import { OOBE_COMPLETE_KEY } from './constants';

type Step = 'welcome' | 'look' | 'setup';
const ORDER: Step[] = ['welcome', 'look', 'setup'];

export function Oobe({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>('welcome');
  const index = ORDER.indexOf(step);

  const finish = () => {
    void setSetting(OOBE_COMPLETE_KEY, true);
    onDone();
  };
  const next = () => (index >= ORDER.length - 1 ? finish() : setStep(ORDER[index + 1]));

  return (
    <div className="os-oobe">
      <div className="os-oobe-card">
        <ol className="os-oobe-progress" aria-label="Setup progress">
          {ORDER.map((s, i) => (
            <li key={s} className={i === index ? 'is-current' : i < index ? 'is-done' : ''} />
          ))}
        </ol>

        {step === 'welcome' && <Welcome />}
        {step === 'look' && <Look />}
        {step === 'setup' && <SetupStep />}

        <footer className="os-oobe-actions">
          {/* "Skip setup" all the way out, not just past this step: someone who
              knows they want none of this should not have to click through
              three screens to say so. */}
          <button type="button" className="os-oobe-skip" onClick={finish}>
            Skip setup
          </button>
          {index > 0 && (
            <button type="button" onClick={() => setStep(ORDER[index - 1])}>
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

function Welcome() {
  const [name, setName] = useState(() => localStorage.getItem(NAME_KEY) ?? '');
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
          onChange={(e) => {
            setName(e.target.value);
            // localStorage, matching where the greeting has always been read
            // from — it is a per-browser nicety, not a node-wide setting.
            localStorage.setItem(NAME_KEY, e.target.value);
          }}
        />
      </label>
    </section>
  );
}

function Look() {
  const themeId = useThemeId();
  const backdrops = registry.backdrops;
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
      <div className="os-oobe-choices">
        {backdrops.map((b) => (
          <button
            key={b.id}
            type="button"
            className="os-oobe-choice"
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
