/**
 * The agent's `ask_user` questions, inline in its turn. Each question is a set of
 * options with one-line descriptions plus an "Other" box; the agent waits until
 * the card is submitted or skipped. Once answered it folds to what was chosen.
 */
import { useState } from 'react';

import { IconCheck } from '../../glyphs';
import type { Answers, QuestionStep } from './turn-steps';

const OTHER = '\u0000other';

export function QuestionCard({
  step,
  onAnswer,
}: {
  step: QuestionStep;
  onAnswer: (questionId: string, answers: Answers | null) => void;
}) {
  // Per question: the picked labels (OTHER stands for the free-text box).
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [other, setOther] = useState<Record<string, string>>({});
  const settled = Boolean(step.answers) || Boolean(step.dismissed);

  if (settled) {
    return (
      <section className="agent-card agent-question" data-settled="true">
        <header className="agent-card-head">
          <span className="agent-card-title">Your answers</span>
        </header>
        {step.dismissed ? (
          <p className="agent-card-muted">Skipped — the agent went with its own judgement.</p>
        ) : (
          <dl className="agent-answers">
            {step.questions.map((q) => {
              const a = step.answers?.[q.question];
              return (
                <div key={q.question} className="agent-answer">
                  <dt>{q.header || q.question}</dt>
                  <dd>{Array.isArray(a) ? a.join(', ') : (a ?? '—')}</dd>
                </div>
              );
            })}
          </dl>
        )}
      </section>
    );
  }

  const toggle = (question: string, label: string, multi: boolean) =>
    setPicked((p) => {
      const cur = p[question] ?? [];
      if (!multi) return { ...p, [question]: cur[0] === label ? [] : [label] };
      return {
        ...p,
        [question]: cur.includes(label) ? cur.filter((l) => l !== label) : [...cur, label],
      };
    });

  const answerFor = (question: string, multi: boolean): string | string[] | null => {
    const labels = (picked[question] ?? []).map((l) =>
      l === OTHER ? (other[question] ?? '').trim() : l,
    );
    const clean = labels.filter(Boolean);
    if (clean.length === 0) return null;
    return multi ? clean : clean[0];
  };

  const answers: Answers = {};
  let complete = true;
  for (const q of step.questions) {
    const a = answerFor(q.question, q.multiSelect);
    if (a === null) complete = false;
    else answers[q.question] = a;
  }

  return (
    <section className="agent-card agent-question" aria-label="The agent is asking you">
      <header className="agent-card-head">
        <span className="agent-card-title">
          {step.questions.length === 1
            ? 'A choice for you'
            : `${step.questions.length} choices for you`}
        </span>
      </header>
      {step.questions.map((q) => {
        const sel = picked[q.question] ?? [];
        return (
          <fieldset key={q.question} className="agent-q">
            <legend className="agent-q-legend">
              {q.header && <span className="agent-q-chip">{q.header}</span>}
              <span className="agent-q-text">{q.question}</span>
            </legend>
            <div className="agent-q-options" role={q.multiSelect ? 'group' : 'radiogroup'}>
              {q.options.map((o) => {
                const on = sel.includes(o.label);
                return (
                  <button
                    key={o.label}
                    type="button"
                    className="agent-q-option"
                    role={q.multiSelect ? 'checkbox' : 'radio'}
                    aria-checked={on}
                    onClick={() => toggle(q.question, o.label, q.multiSelect)}
                  >
                    <span className="agent-q-mark" data-multi={q.multiSelect || undefined}>
                      {on && <IconCheck />}
                    </span>
                    <span className="agent-q-option-text">
                      <span className="agent-q-label">{o.label}</span>
                      {o.description && <span className="agent-q-desc">{o.description}</span>}
                    </span>
                  </button>
                );
              })}
              <label className="agent-q-other">
                <span className="agent-q-other-label">Other</span>
                <input
                  type="text"
                  value={other[q.question] ?? ''}
                  placeholder="Your own answer"
                  onChange={(e) => {
                    const text = e.target.value;
                    setOther((p) => ({ ...p, [q.question]: text }));
                    setPicked((p) => {
                      const cur = (p[q.question] ?? []).filter((l) => l !== OTHER);
                      if (!text.trim()) return { ...p, [q.question]: cur };
                      return { ...p, [q.question]: q.multiSelect ? [...cur, OTHER] : [OTHER] };
                    });
                  }}
                />
              </label>
            </div>
          </fieldset>
        );
      })}
      <div className="agent-card-actions">
        <button
          type="button"
          disabled={!complete}
          onClick={() => onAnswer(step.questionId, answers)}
        >
          Submit
        </button>
        <button type="button" onClick={() => onAnswer(step.questionId, null)}>
          Skip
        </button>
      </div>
    </section>
  );
}
