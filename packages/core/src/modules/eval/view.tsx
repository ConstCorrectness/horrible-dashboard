import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '../../api';
import { useAgentContext } from '../../agent-context';

interface StateValidator {
  type: string;
  target: string;
  expected: string;
}

interface EvaluationTask {
  id: string;
  name: string;
  description: string;
  prompt: string;
  initial_files: Record<string, string>;
  expected_tools: string[];
  banned_tools: string[];
  state_validators: StateValidator[];
  optimal_turns: number;
}

interface EvaluationResult {
  task_id: string;
  success: boolean;
  turns: number;
  duration_s: number;
  tool_calls: string[];
  precision: number;
  recall: number;
  f1: number;
  errors: string[];
}

type TaskStatus = 'idle' | 'running' | 'passed' | 'failed';

interface TaskState {
  task: EvaluationTask;
  status: TaskStatus;
  selected: boolean;
  result: EvaluationResult | null;
}

export function EvaluationDashboard() {
  const [tasks, setTasks] = useState<TaskState[]>([]);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [isRunningAll, setIsRunningAll] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Expose evaluation metrics and state to the agent via context
  useAgentContext(() => {
    const total = tasks.length;
    const passed = tasks.filter((t) => t.status === 'passed').length;
    const failed = tasks.filter((t) => t.status === 'failed').length;
    return {
      evaluator: 'sandbox-testbed',
      tasksCount: total,
      passedCount: passed,
      failedCount: failed,
      passRate: total > 0 ? (passed / total) * 100 : 0,
      activeTaskId,
      running: isRunningAll || tasks.some((t) => t.status === 'running'),
    };
  });

  // Fetch registered evaluation tasks on mount
  useEffect(() => {
    let cancelled = false;
    apiGet<EvaluationTask[]>('/agent/eval/tasks')
      .then((data) => {
        if (cancelled) return;
        const initialStates: TaskState[] = data.map((t) => ({
          task: t,
          status: 'idle',
          selected: true,
          result: null,
        }));
        setTasks(initialStates);
        if (initialStates.length > 0) {
          setActiveTaskId(initialStates[0].task.id);
        }
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runSingleTask = async (taskId: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.task.id === taskId ? { ...t, status: 'running', result: null } : t)),
    );
    try {
      const res = await apiPost<EvaluationResult>(`/agent/eval/run/${taskId}`, {});
      setTasks((prev) =>
        prev.map((t) =>
          t.task.id === taskId
            ? { ...t, status: res.success ? 'passed' : 'failed', result: res }
            : t,
        ),
      );
      return res.success;
    } catch {
      setTasks((prev) =>
        prev.map((t) =>
          t.task.id === taskId
            ? {
                ...t,
                status: 'failed',
                result: {
                  task_id: taskId,
                  success: false,
                  turns: 0,
                  duration_s: 0,
                  tool_calls: [],
                  precision: 0,
                  recall: 0,
                  f1: 0,
                  errors: ['Request failed or timed out'],
                },
              }
            : t,
        ),
      );
      return false;
    }
  };

  const runSelectedSuite = async () => {
    const toRun = tasks.filter((t) => t.selected);
    if (toRun.length === 0) return;
    setIsRunningAll(true);

    for (const t of toRun) {
      setActiveTaskId(t.task.id);
      await runSingleTask(t.task.id);
    }
    setIsRunningAll(false);
  };

  const toggleSelect = (taskId: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.task.id === taskId ? { ...t, selected: !t.selected } : t)),
    );
  };

  const toggleSelectAll = () => {
    const allSelected = tasks.every((t) => t.selected);
    setTasks((prev) => prev.map((t) => ({ ...t, selected: !allSelected })));
  };

  // Calculate executive summary statistics
  const ranTasks = tasks.filter((t) => t.status === 'passed' || t.status === 'failed');
  const passCount = tasks.filter((t) => t.status === 'passed').length;
  const passRate = ranTasks.length > 0 ? Math.round((passCount / ranTasks.length) * 100) : 0;

  const validResults = ranTasks.map((t) => t.result).filter(Boolean) as EvaluationResult[];
  const avgEfficiency =
    validResults.length > 0
      ? Math.round(
          (validResults.reduce((acc, curr) => acc + curr.f1, 0) / validResults.length) * 100,
        )
      : 0;

  const avgTurns =
    validResults.length > 0
      ? (validResults.reduce((acc, curr) => acc + curr.turns, 0) / validResults.length).toFixed(1)
      : '0.0';

  const totalTime = validResults.reduce((acc, curr) => acc + curr.duration_s, 0).toFixed(2);

  const activeState = tasks.find((t) => t.task.id === activeTaskId);

  if (loading) {
    return (
      <div className="eval-container loading-state">
        <div className="spinner"></div>
        <p>Loading Evaluation Suite...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="eval-container error-state">
        <p className="error-title">Failed to load evaluations</p>
        <p className="error-message">{error}</p>
      </div>
    );
  }

  return (
    <div className="eval-container">
      <style dangerouslySetInnerHTML={{ __html: styles }} />

      {/* Title Header */}
      <header className="eval-header">
        <div className="header-meta">
          <span className="badge-glow">UNSLOTH ENGINE CONTROLLER</span>
          <h1>Agent Evaluation Suite</h1>
          <p className="subtitle">Execute sandbox assertions and compute tool calling trajectory efficiency</p>
        </div>
        <div className="header-actions">
          <button
            className={`btn-primary ${isRunningAll ? 'running' : ''}`}
            onClick={runSelectedSuite}
            disabled={isRunningAll}
          >
            {isRunningAll ? (
              <>
                <span className="spinner-mini"></span> Executing Suite...
              </>
            ) : (
              'Run Checked Benchmarks'
            )}
          </button>
        </div>
      </header>

      {/* Summary Gauges */}
      <section className="eval-summary">
        <div className="summary-card">
          <div className="card-label">Pass Rate</div>
          <div className="card-value text-green">{passRate}%</div>
          <div className="progress-bar-bg">
            <div className="progress-bar-fill fill-green" style={{ width: `${passRate}%` }}></div>
          </div>
          <div className="card-footer">{passCount} of {ranTasks.length} tasks passing</div>
        </div>

        <div className="summary-card">
          <div className="card-label">Average Tool F1-Score</div>
          <div className="card-value text-blue">{avgEfficiency}%</div>
          <div className="progress-bar-bg">
            <div className="progress-bar-fill fill-blue" style={{ width: `${avgEfficiency}%` }}></div>
          </div>
          <div className="card-footer">Matches target schemas</div>
        </div>

        <div className="summary-card">
          <div className="card-label">Mean Trajectory Length</div>
          <div className="card-value text-amber">{avgTurns}</div>
          <div className="card-subtext">Turns/steps per task</div>
          <div className="card-footer">Optimal ceiling: 3 turns</div>
        </div>

        <div className="summary-card">
          <div className="card-label">Total Suite Duration</div>
          <div className="card-value">{totalTime}s</div>
          <div className="card-subtext">Execution timeout: 120s</div>
          <div className="card-footer">Isolated sandbox runs</div>
        </div>
      </section>

      {/* Two Column Layout */}
      <div className="eval-main-split">
        {/* Left Column: Tasks list */}
        <aside className="tasks-sidebar">
          <div className="sidebar-header">
            <h3>Benchmarks</h3>
            <button className="btn-link" onClick={toggleSelectAll}>
              Toggle All
            </button>
          </div>
          <ul className="tasks-list">
            {tasks.map((t) => (
              <li
                key={t.task.id}
                className={`task-item ${t.task.id === activeTaskId ? 'active' : ''}`}
                onClick={() => setActiveTaskId(t.task.id)}
              >
                <input
                  type="checkbox"
                  checked={t.selected}
                  onChange={() => toggleSelect(t.task.id)}
                  onClick={(e) => e.stopPropagation()}
                />
                <div className="task-info">
                  <span className="task-name">{t.task.name}</span>
                  <span className="task-id-tag">{t.task.id}</span>
                </div>
                <div className="task-status">
                  {t.status === 'running' && <span className="status-dot running-dot"></span>}
                  {t.status === 'passed' && <span className="status-badge passed-badge">PASS</span>}
                  {t.status === 'failed' && <span className="status-badge failed-badge">FAIL</span>}
                  {t.status === 'idle' && <span className="status-dot idle-dot"></span>}
                </div>
              </li>
            ))}
          </ul>
        </aside>

        {/* Right Column: Detailed Execution Trace */}
        <main className="task-details-pane">
          {activeState ? (
            <div className="details-wrapper">
              <section className="details-header">
                <h2>{activeState.task.name}</h2>
                <p className="task-desc">{activeState.task.description}</p>
                <div className="details-header-actions">
                  <button
                    className="btn-sec"
                    onClick={() => runSingleTask(activeState.task.id)}
                    disabled={activeState.status === 'running'}
                  >
                    {activeState.status === 'running' ? 'Running...' : 'Run Bench'}
                  </button>
                </div>
              </section>

              {/* Specifications */}
              <section className="details-section">
                <h4>Prompt Specification</h4>
                <div className="prompt-box">"{activeState.task.prompt}"</div>
              </section>

              {/* Target schemas & rules */}
              <div className="rules-grid">
                <div className="rule-card">
                  <h5>Expected Tools</h5>
                  {activeState.task.expected_tools.length > 0 ? (
                    <ul>
                      {activeState.task.expected_tools.map((et) => (
                        <li key={et}><code>{et}</code></li>
                      ))}
                    </ul>
                  ) : (
                    <span className="empty-rule">None specified</span>
                  )}
                </div>
                <div className="rule-card">
                  <h5>Banned Tools</h5>
                  {activeState.task.banned_tools.length > 0 ? (
                    <ul>
                      {activeState.task.banned_tools.map((bt) => (
                        <li key={bt} className="text-red"><code>{bt}</code></li>
                      ))}
                    </ul>
                  ) : (
                    <span className="empty-rule">None specified</span>
                  )}
                </div>
              </div>

              {/* Execution outputs */}
              <section className="details-section">
                <h4>Sandbox Trajectory Trace</h4>
                {activeState.result ? (
                  <div className="trajectory-view">
                    <div className="trajectory-meta">
                      <span><strong>Turns:</strong> {activeState.result.turns} / {activeState.task.optimal_turns} optimal</span>
                      <span><strong>Duration:</strong> {activeState.result.duration_s.toFixed(2)}s</span>
                      <span><strong>F1 Accuracy:</strong> {Math.round(activeState.result.f1 * 100)}%</span>
                    </div>

                    <div className="trace-section">
                      <h5>Tool Invocation Sequence</h5>
                      {activeState.result.tool_calls.length > 0 ? (
                        <ol className="trace-list">
                          {activeState.result.tool_calls.map((call, idx) => (
                            <li key={idx}>
                              <span className="step-num">Step {idx + 1}:</span> Called <code>{call}</code>
                            </li>
                          ))}
                        </ol>
                      ) : (
                        <p className="no-calls">No tools were called (conversational output only).</p>
                      )}
                    </div>

                    {activeState.result.errors.length > 0 && (
                      <div className="errors-console">
                        <h5>Validation / Execution Failures</h5>
                        <pre>
                          {activeState.result.errors.join('\n')}
                        </pre>
                      </div>
                    )}

                    {activeState.result.errors.length === 0 && (
                      <div className="success-banner">
                        ✓ All sandbox assertions and validation scripts compiled and passed cleanly.
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="trajectory-placeholder">
                    {activeState.status === 'running' ? (
                      <div className="running-placeholder">
                        <span className="spinner-large"></span>
                        <p>Executing agent code in the sandbox...</p>
                      </div>
                    ) : (
                      <p>Run the task to view sandbox trajectory and assertion trace.</p>
                    )}
                  </div>
                )}
              </section>
            </div>
          ) : (
            <div className="empty-state">
              <p>Select a benchmark from the sidebar to inspect trajectory details.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

const styles = `
.eval-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 1.5rem;
  background: #0f1115;
  color: var(--text);
  overflow-y: auto;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.loading-state {
  align-items: center;
  justify-content: center;
  height: 100%;
}

.spinner {
  width: 2.5rem;
  height: 2.5rem;
  border: 4px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 1rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.eval-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border);
  padding-bottom: 1.2rem;
  margin-bottom: 1.5rem;
}

.header-meta h1 {
  margin: 0.2rem 0;
  font-size: 1.8rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.badge-glow {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--accent);
  letter-spacing: 0.1em;
  background: rgba(110, 168, 254, 0.1);
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  border: 1px solid rgba(110, 168, 254, 0.2);
}

.subtitle {
  color: var(--text-dim);
  margin: 0;
  font-size: 0.9rem;
}

.btn-primary {
  background: var(--accent);
  color: #0b0c10;
  border: none;
  padding: 0.6rem 1.2rem;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  transition: opacity 0.2s;
}

.btn-primary:hover {
  opacity: 0.9;
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spinner-mini {
  width: 1rem;
  height: 1rem;
  border: 2px solid #0b0c10;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.eval-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.summary-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.2rem;
  display: flex;
  flex-direction: column;
}

.card-label {
  font-size: 0.8rem;
  color: var(--text-dim);
  text-transform: uppercase;
  font-weight: 600;
  margin-bottom: 0.4rem;
}

.card-value {
  font-size: 2rem;
  font-weight: 700;
  margin-bottom: 0.5rem;
}

.text-green { color: #52c41a; }
.text-blue { color: var(--accent); }
.text-amber { color: #faad14; }

.card-subtext {
  font-size: 0.8rem;
  color: var(--text-dim);
  margin-bottom: 0.5rem;
}

.card-footer {
  margin-top: auto;
  font-size: 0.75rem;
  color: var(--text-dim);
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  padding-top: 0.5rem;
}

.progress-bar-bg {
  background: #2e333d;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 0.6rem;
}

.progress-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.fill-green { background: #52c41a; }
.fill-blue { background: var(--accent); }

.eval-main-split {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 1.5rem;
  min-height: 400px;
}

.tasks-sidebar {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.5rem;
  margin-bottom: 0.8rem;
}

.sidebar-header h3 {
  margin: 0;
  font-size: 0.95rem;
  text-transform: uppercase;
  color: var(--text-dim);
}

.btn-link {
  background: transparent;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0;
}

.tasks-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.task-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 0.8rem;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s;
}

.task-item:hover {
  background: var(--bg-hover);
}

.task-item.active {
  background: var(--bg-hover);
  border-color: var(--border);
}

.task-info {
  display: flex;
  flex-direction: column;
  flex: 1;
}

.task-name {
  font-size: 0.85rem;
  font-weight: 600;
}

.task-id-tag {
  font-size: 0.7rem;
  color: var(--text-dim);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.idle-dot { background: #565d6d; }
.running-dot {
  background: var(--accent);
  box-shadow: 0 0 8px var(--accent);
  animation: pulse 1s infinite alternate;
}

@keyframes pulse {
  from { opacity: 0.5; }
  to { opacity: 1; }
}

.status-badge {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 0.15rem 0.35rem;
  border-radius: 4px;
}

.passed-badge {
  background: rgba(82, 196, 26, 0.15);
  color: #52c41a;
  border: 1px solid rgba(82, 196, 26, 0.3);
}

.failed-badge {
  background: rgba(255, 77, 79, 0.15);
  color: #ff4d4f;
  border: 1px solid rgba(255, 77, 79, 0.3);
}

.task-details-pane {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.5rem;
}

.details-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  border-bottom: 1px solid var(--border);
  padding-bottom: 1rem;
  margin-bottom: 1rem;
}

.details-header h2 {
  margin: 0 0 0.4rem 0;
  font-size: 1.4rem;
}

.task-desc {
  color: var(--text-dim);
  margin: 0;
  font-size: 0.9rem;
}

.btn-sec {
  background: transparent;
  color: var(--text);
  border: 1px solid var(--border);
  padding: 0.5rem 1rem;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
}

.btn-sec:hover {
  background: var(--bg-hover);
}

.details-section {
  margin-bottom: 1.2rem;
}

.details-section h4 {
  margin: 0 0 0.5rem 0;
  font-size: 0.9rem;
  color: var(--text-dim);
  text-transform: uppercase;
}

.prompt-box {
  background: #0f1115;
  border: 1px solid var(--border);
  padding: 0.8rem 1rem;
  border-radius: 6px;
  font-style: italic;
  font-size: 0.95rem;
}

.rules-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
  margin-bottom: 1.2rem;
}

.rule-card {
  background: #0f1115;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.8rem;
}

.rule-card h5 {
  margin: 0 0 0.5rem 0;
  font-size: 0.8rem;
  color: var(--text-dim);
  text-transform: uppercase;
}

.rule-card ul {
  margin: 0;
  padding-left: 1.2rem;
  font-size: 0.85rem;
}

.empty-rule {
  font-size: 0.8rem;
  color: var(--text-dim);
  font-style: italic;
}

.trajectory-view {
  background: #0f1115;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
}

.trajectory-meta {
  display: flex;
  gap: 1.5rem;
  font-size: 0.85rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.6rem;
  margin-bottom: 0.8rem;
  color: var(--text-dim);
}

.trace-section h5 {
  margin: 0 0 0.5rem 0;
  font-size: 0.85rem;
  color: var(--text-dim);
}

.trace-list {
  padding-left: 1.2rem;
  margin: 0 0 1rem 0;
  font-size: 0.85rem;
}

.step-num {
  font-weight: bold;
  color: var(--accent);
}

.errors-console {
  background: rgba(255, 77, 79, 0.05);
  border: 1px solid rgba(255, 77, 79, 0.2);
  border-radius: 4px;
  padding: 0.8rem;
}

.errors-console h5 {
  margin: 0 0 0.4rem 0;
  color: #ff4d4f;
}

.errors-console pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: monospace;
  font-size: 0.8rem;
  color: #ff7875;
}

.success-banner {
  background: rgba(82, 196, 26, 0.1);
  border: 1px solid rgba(82, 196, 26, 0.2);
  color: #73d13d;
  padding: 0.8rem;
  border-radius: 4px;
  font-size: 0.85rem;
}

.trajectory-placeholder {
  height: 150px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--border);
  border-radius: 6px;
  color: var(--text-dim);
  font-size: 0.85rem;
}

.running-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.8rem;
}

.spinner-large {
  width: 1.8rem;
  height: 1.8rem;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.empty-state {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-dim);
}
`;
