/**
 * Reading a case result's tool budget. Its own file so the rule is testable without
 * importing the evals pane.
 */
import type { CaseResult } from './api';

/** Expected tools the budget kept off a failing case's catalog. Empty for a pass:
 *  a case that passed without the tool was not held back by its absence. */
export function budgetCut(r: Pick<CaseResult, 'passed' | 'expected' | 'tools_dropped'>): string[] {
  if (r.passed || r.tools_dropped.length === 0) return [];
  const dropped = new Set(r.tools_dropped);
  return [...new Set(r.expected.map((c) => c.name).filter((name) => dropped.has(name)))];
}
