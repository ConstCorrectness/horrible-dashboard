/**
 * The agent's checklist (`update_todos`), pinned at the top of its turn — and, in
 * `compact` form, above the composer while the turn runs, so the step it is on
 * stays in view however far the transcript has scrolled.
 */
import { IconCheck } from '../../glyphs';
import type { TodoItem } from './turn-steps';

function Mark({ status }: { status: TodoItem['status'] }) {
  return (
    <span className="agent-todo-mark" data-status={status} aria-hidden="true">
      {status === 'completed' && <IconCheck />}
    </span>
  );
}

export function TurnTodos({ items, compact = false }: { items: TodoItem[]; compact?: boolean }) {
  if (items.length === 0) return null;
  const done = items.filter((t) => t.status === 'completed').length;
  const current = items.find((t) => t.status === 'in_progress');

  if (compact) {
    return (
      <div className="agent-todos-compact" role="status">
        <span className="agent-todos-count">
          {done}/{items.length}
        </span>
        <span className="agent-todos-current">{current?.content ?? 'Tasks'}</span>
      </div>
    );
  }

  return (
    <section className="agent-card agent-todos" aria-label="Tasks">
      <header className="agent-card-head">
        <span className="agent-card-title">Tasks</span>
        <span className="agent-todos-count">
          {done}/{items.length}
        </span>
      </header>
      <ol className="agent-todo-list">
        {items.map((t, i) => (
          <li key={i} className="agent-todo" data-status={t.status}>
            <Mark status={t.status} />
            <span className="agent-todo-text">{t.content}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
