/**
 * An assistant turn as an ordered **activity timeline**: what the agent thought,
 * which tools it ran and how they went, what it said between them, what it asked.
 *
 * The orchestrator streams the pieces separately — `reasoning` deltas, `token`
 * deltas, `tool_start`/`tool_end` around every call, `question`, `todos`, `plan` —
 * and these reducers fold them into one list in arrival order, which is the order
 * the chat draws them in. Pure functions over plain data, kept apart from the
 * widget so they can be tested without a DOM (and saved as-is: the backend stores
 * `steps` opaquely, so this file owns the shape on both sides of a reload).
 *
 * Two rules carry most of the behaviour:
 *
 * - **Prose before a tool call becomes a `note`.** Text the model writes alongside
 *   its calls ("I'll check the scene first") streams into `text` like an answer,
 *   and only turns out not to be one when a `tool_start` follows. At that point it
 *   moves into the timeline, between the groups it separates, and `text` resets —
 *   so `text` is always the answer-in-progress, and the `answer` event stays the
 *   authority on the final one.
 * - **Rows are matched to the latest call with that id.** Providers number calls
 *   per response (`"0"`, `"1"`, …), so ids repeat across rounds; rounds are
 *   sequential, so the newest row with the id is the one a `tool_end` is about.
 */

export type ToolStatus = 'running' | 'approval' | 'ok' | 'error' | 'denied' | 'interrupted';

export interface ThoughtStep {
  kind: 'thought';
  text: string;
  startedAt: number;
  /** Unset while the model is still reasoning. */
  endedAt?: number;
}

export interface ToolStep {
  kind: 'tool';
  callId: string;
  name: string;
  args: unknown;
  status: ToolStatus;
  startedAt: number;
  durationMs?: number;
  /** The first ~1.5 KB of the result, as the backend serialised it. */
  preview?: string;
  /** A fixed label, for rows rebuilt from a pre-timeline transcript's `actions`. */
  label?: string;
}

export interface NoteStep {
  kind: 'note';
  text: string;
}

export interface QuestionOption {
  label: string;
  description: string;
}

export interface Question {
  question: string;
  header: string;
  multiSelect: boolean;
  options: QuestionOption[];
}

/** Keyed by question text: a picked label, several, or the user's own words. */
export type Answers = Record<string, string | string[]>;

export interface QuestionStep {
  kind: 'question';
  questionId: string;
  questions: Question[];
  answers?: Answers;
  dismissed?: boolean;
}

export type Step = ThoughtStep | ToolStep | NoteStep | QuestionStep;

export type TodoStatus = 'pending' | 'in_progress' | 'completed';

export interface TodoItem {
  content: string;
  status: TodoStatus;
}

export interface TurnPlan {
  text: string;
  status: 'proposed' | 'approved' | 'superseded';
}

/** The slice of a chat turn the timeline reads and writes. */
export interface ActivityTurn {
  text: string;
  steps?: Step[];
  todos?: TodoItem[];
  plan?: TurnPlan;
  startedAt?: number;
  endedAt?: number;
}

/** Close a still-open thought: the model stopped reasoning and did something. */
function closeThought(steps: Step[], now: number): Step[] {
  const last = steps.at(-1);
  if (last?.kind !== 'thought' || last.endedAt !== undefined) return steps;
  return [...steps.slice(0, -1), { ...last, endedAt: now }];
}

/** Move the answer-so-far into the timeline as a note (see the module comment). */
function flushText<T extends ActivityTurn>(t: T, steps: Step[]): { steps: Step[]; text: string } {
  const prose = t.text.trim();
  return prose
    ? { steps: [...steps, { kind: 'note', text: prose }], text: '' }
    : { steps, text: t.text };
}

export function applyReasoning<T extends ActivityTurn>(t: T, delta: string, now: number): T {
  if (!delta) return t;
  const steps = t.steps ?? [];
  const last = steps.at(-1);
  if (last?.kind === 'thought' && last.endedAt === undefined) {
    return { ...t, steps: [...steps.slice(0, -1), { ...last, text: last.text + delta }] };
  }
  return { ...t, steps: [...steps, { kind: 'thought', text: delta, startedAt: now }] };
}

export function applyToken<T extends ActivityTurn>(t: T, delta: string, now: number): T {
  if (!delta) return t;
  return { ...t, steps: closeThought(t.steps ?? [], now), text: t.text + delta };
}

export function applyToolStart<T extends ActivityTurn>(
  t: T,
  call: { callId: string; name: string; args: unknown },
  now: number,
): T {
  const { steps, text } = flushText(t, closeThought(t.steps ?? [], now));
  const row: ToolStep = {
    kind: 'tool',
    callId: call.callId,
    name: call.name,
    args: call.args,
    status: 'running',
    startedAt: now,
  };
  return { ...t, text, steps: [...steps, row] };
}

/** Index of the newest tool row for `callId` that is still waiting to finish. */
function openRow(steps: Step[], callId: string): number {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (
      s.kind === 'tool' &&
      s.callId === callId &&
      (s.status === 'running' || s.status === 'approval')
    ) {
      return i;
    }
  }
  return -1;
}

function patchRow<T extends ActivityTurn>(t: T, callId: string, patch: Partial<ToolStep>): T {
  const steps = t.steps ?? [];
  const i = openRow(steps, callId);
  if (i < 0) return t;
  const next = steps.slice();
  next[i] = { ...(steps[i] as ToolStep), ...patch };
  return { ...t, steps: next };
}

/** Tools whose result the chat draws as its own card — the checklist, the
 *  question card, the plan card. A row for them would say the same thing twice,
 *  so it is only shown when the call failed. */
export const CARD_TOOLS: ReadonlySet<string> = new Set(['update_todos', 'ask_user', 'submit_plan']);

const isCardRow = (s: Step): boolean => s.kind === 'tool' && CARD_TOOLS.has(s.name);

export function applyToolEnd<T extends ActivityTurn>(
  t: T,
  end: { callId: string; ok: boolean; denied?: boolean; durationMs?: number; preview?: string },
): T {
  const steps = t.steps ?? [];
  const i = openRow(steps, end.callId);
  if (i >= 0 && end.ok && isCardRow(steps[i])) {
    return { ...t, steps: steps.filter((_, j) => j !== i) };
  }
  return patchRow(t, end.callId, {
    status: end.denied ? 'denied' : end.ok ? 'ok' : 'error',
    durationMs: end.durationMs,
    preview: end.preview,
  });
}

/** The permission gate is waiting on the user for this call. */
export function applyApproval<T extends ActivityTurn>(t: T, callId: string): T {
  return patchRow(t, callId, { status: 'approval' });
}

export function applyQuestion<T extends ActivityTurn>(
  t: T,
  q: { questionId: string; questions: Question[] },
  now: number,
): T {
  const { steps, text } = flushText(t, closeThought(t.steps ?? [], now));
  return {
    ...t,
    text,
    steps: [...steps, { kind: 'question', questionId: q.questionId, questions: q.questions }],
  };
}

export function answerQuestion<T extends ActivityTurn>(
  t: T,
  questionId: string,
  answers: Answers | null,
): T {
  const steps = (t.steps ?? []).map((s) =>
    s.kind === 'question' && s.questionId === questionId
      ? answers
        ? { ...s, answers }
        : { ...s, dismissed: true }
      : s,
  );
  return { ...t, steps };
}

export function applyTodos<T extends ActivityTurn>(t: T, items: TodoItem[]): T {
  return { ...t, todos: items };
}

export function applyPlan<T extends ActivityTurn>(t: T, text: string, now: number): T {
  return { ...t, steps: closeThought(t.steps ?? [], now), plan: { text, status: 'proposed' } };
}

/**
 * The turn is over — answered, stopped, failed, or saved mid-flight. Nothing may
 * still look like it is running once nothing is: an open thought closes, a call
 * that never reported back is `interrupted`, an unanswered question is dismissed.
 */
export function finalizeTurn<T extends ActivityTurn>(t: T, now: number): T {
  const steps = closeThought(t.steps ?? [], now).map((s): Step => {
    if (s.kind === 'tool' && (s.status === 'running' || s.status === 'approval')) {
      return { ...s, status: 'interrupted' };
    }
    if (s.kind === 'question' && !s.answers && !s.dismissed) return { ...s, dismissed: true };
    return s;
  });
  return { ...t, steps, endedAt: t.endedAt ?? now };
}

/** What the turn is doing right now, for the live line under it. */
export type TurnPhase =
  | { kind: 'thinking' }
  | { kind: 'reasoning' }
  | { kind: 'writing' }
  | { kind: 'tool'; name: string }
  | { kind: 'approval'; name: string }
  | { kind: 'question' };

export function turnPhase(t: ActivityTurn): TurnPhase {
  const steps = t.steps ?? [];
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.kind === 'question' && !s.answers && !s.dismissed) return { kind: 'question' };
    if (s.kind === 'tool' && s.status === 'approval') return { kind: 'approval', name: s.name };
    if (s.kind === 'tool' && s.status === 'running') return { kind: 'tool', name: s.name };
  }
  const last = steps.at(-1);
  if (last?.kind === 'thought' && last.endedAt === undefined) return { kind: 'reasoning' };
  if (t.text) return { kind: 'writing' };
  return { kind: 'thinking' };
}

/** A run of consecutive thoughts and tool calls — one collapsible group. */
export interface ActivityBlock {
  kind: 'activity';
  steps: (ThoughtStep | ToolStep)[];
}

export type TimelineBlock = ActivityBlock | NoteStep | QuestionStep;

/** Fold the steps into what the chat draws: activity groups, with notes and
 *  questions standing on their own between them. */
export function timelineBlocks(steps: Step[]): TimelineBlock[] {
  const blocks: TimelineBlock[] = [];
  for (const s of steps) {
    // Still in flight: its card (or the answer it is waiting on) is the display.
    if (isCardRow(s) && s.kind === 'tool' && s.status === 'running') continue;
    if (s.kind === 'note' || s.kind === 'question') {
      blocks.push(s);
      continue;
    }
    const last = blocks.at(-1);
    if (last?.kind === 'activity') last.steps.push(s);
    else blocks.push({ kind: 'activity', steps: [s] });
  }
  return blocks;
}

/**
 * Steps for a turn saved before the timeline existed: its single reasoning blob
 * and its "Opened X" action notes. Read-only — the pre-timeline fields are never
 * written for a new turn.
 */
export function legacySteps(t: { reasoning?: string; actions?: string[] }): Step[] {
  const steps: Step[] = [];
  if (t.reasoning) steps.push({ kind: 'thought', text: t.reasoning, startedAt: 0, endedAt: 0 });
  for (const [i, action] of (t.actions ?? []).entries()) {
    steps.push({
      kind: 'tool',
      callId: `legacy-${i}`,
      name: '',
      label: action,
      args: null,
      status: 'ok',
      startedAt: 0,
    });
  }
  return steps;
}
