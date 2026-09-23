/**
 * The seam other surfaces use to open a trace in the stepper.
 *
 * Same shape and same two-directional race as `trace-prompt.ts`: revealing the
 * section mounts it fresh (so a target pushed *before* the reveal is read on mount
 * with `takeStepperTarget`), while a target pushed at an already-open section has a
 * listener and no mount. Either half alone drops the other case.
 *
 * A target is one-shot and `take` clears it — leaving it set would make every later
 * visit to the section jump back to someone's old cursor.
 */
import { revealSection } from '../../../layout/controller';

export interface StepperTarget {
  traceId: string;
  /** A step (program index) to land on; absent means "the start". */
  step?: number;
  /** Or a layer to land on, resolved against the program once it loads. */
  layer?: number;
  passIndex?: number;
}

let pending: StepperTarget | null = null;
const listeners = new Set<(next: StepperTarget) => void>();

export function sendStepperTarget(next: StepperTarget): void {
  pending = next;
  listeners.forEach((listener) => listener(next));
}

export function takeStepperTarget(): StepperTarget | null {
  const next = pending;
  pending = null;
  return next;
}

export function subscribeStepperTarget(listener: (next: StepperTarget) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Open `traceId` in the stepper — the verb behind every "Step through" button. */
export function openInStepper(target: StepperTarget): void {
  sendStepperTarget(target);
  revealSection('stepper', 'llamacpp.server');
}
