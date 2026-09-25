/**
 * Plain-language reads of a training run's curves, for the Learn strip.
 *
 * Deliberately **rules, not a model**: every diagnosis names the rule that fired
 * (`rule`), so the strip teaches how to read a loss curve rather than asserting a
 * verdict you have to take on trust. The thresholds are rules of thumb and say so
 * — the point is to put the right question in front of someone new to this, not
 * to replace looking at the chart.
 *
 * Pure and headless so it is unit-tested without a run.
 */

export interface Series {
  xs: number[];
  ys: number[];
}

export type Severity = 'good' | 'info' | 'warn' | 'bad';

export interface Diagnosis {
  id: string;
  severity: Severity;
  title: string;
  /** What it usually means, and what to try. */
  explanation: string;
  /** The rule that fired, stated with the numbers it saw. */
  rule: string;
}

/** Points below which a curve says nothing yet — early loss is always noisy. */
export const MIN_POINTS = 12;

const TRAIN_LOSS = /^(train[/_.])?loss$/i;
const EVAL_LOSS = /^eval[/_.]?loss$|^val(idation)?[/_.]?loss$/i;
const GRAD_NORM = /grad[_/.]?norm/i;

function find(series: Map<string, Series>, re: RegExp): [string, Series] | null {
  for (const entry of series) if (re.test(entry[0])) return entry;
  return null;
}

function mean(xs: number[]): number {
  return xs.reduce((a, b) => a + b, 0) / Math.max(xs.length, 1);
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

const fmt = (n: number) => (Math.abs(n) >= 100 || n === 0 ? n.toFixed(0) : n.toPrecision(3));

export function diagnose(series: Map<string, Series>): Diagnosis[] {
  const out: Diagnosis[] = [];
  const train = find(series, TRAIN_LOSS);
  const evalLoss = find(series, EVAL_LOSS);
  const grad = find(series, GRAD_NORM);

  for (const found of [train, evalLoss, grad]) {
    if (!found) continue;
    const [name, s] = found;
    if (s.ys.some((y) => !Number.isFinite(y))) {
      out.push({
        id: `nan:${name}`,
        severity: 'bad',
        title: `${name} went NaN or infinite`,
        explanation:
          'The run has diverged: once a value is NaN every later step is too. Usual causes are a learning rate far too high, fp16 overflow (try bf16), or a bad batch (an empty sequence, a label out of range). Restart from the last good checkpoint rather than waiting.',
        rule: `a non-finite value appeared in ${name}`,
      });
      return out;
    }
  }

  if (train && train[1].ys.length >= MIN_POINTS) {
    const [name, { ys }] = train;
    const q = Math.max(3, Math.floor(ys.length / 4));
    const first = mean(ys.slice(0, q));
    const last = mean(ys.slice(-q));
    const latest = ys[ys.length - 1];
    const recent = ys.slice(-21, -1);
    const typical = median(recent);

    if (recent.length >= 10 && latest > typical * 2 && latest - typical > 0.1) {
      out.push({
        id: 'spike',
        severity: 'warn',
        title: 'Loss just spiked',
        explanation:
          'One step came out far worse than the ones before it. A single spike that recovers is usually one hard or malformed batch; repeated spikes usually mean the learning rate is too high for this batch size — lower it, or add warmup.',
        rule: `latest ${name} ${fmt(latest)} > 2 × median of the previous ${recent.length} (${fmt(typical)})`,
      });
    }

    if (last >= first * 0.98) {
      out.push({
        id: 'flat',
        severity: 'warn',
        title: 'Loss is not going down',
        explanation:
          'The model is not learning from these batches. Check the learning rate first (too low barely moves; too high bounces in place), then that the labels are what you think they are — a masked or constant label gives a flat curve that looks exactly like this.',
        rule: `mean of the last ${q} points (${fmt(last)}) ≥ 98% of the first ${q} (${fmt(first)})`,
      });
    } else {
      out.push({
        id: 'falling',
        severity: 'good',
        title: 'Loss is falling',
        explanation:
          'The model is fitting the training data. That alone is not the goal — whether it generalises is what the eval loss (or an eval suite) answers.',
        rule: `mean of the last ${q} points (${fmt(last)}) is ${fmt((1 - last / first) * 100)}% below the first ${q} (${fmt(first)})`,
      });
    }
  } else {
    out.push({
      id: 'waiting',
      severity: 'info',
      title: 'Not enough of a curve yet',
      explanation:
        'Early loss is noisy and says little. Once a run logs a loss for a dozen steps, this strip will say how to read it.',
      rule: `fewer than ${MIN_POINTS} points of a training loss (looked for "loss" or "train/loss")`,
    });
  }

  if (evalLoss && train && evalLoss[1].ys.length >= 3 && train[1].ys.length >= MIN_POINTS) {
    const e = evalLoss[1].ys.slice(-3);
    const t = train[1].ys;
    const trainFalling = mean(t.slice(-5)) < mean(t.slice(-15, -5));
    if (e[2] > e[1] && e[1] > e[0] && trainFalling) {
      out.push({
        id: 'overfit',
        severity: 'bad',
        title: 'Overfitting: eval loss is rising',
        explanation:
          'Training loss keeps falling while loss on held-out data climbs — the model is memorising the training set. Stop at (or load) the checkpoint where eval loss was lowest, and consider fewer epochs, more data, or more regularisation (dropout, LoRA rank).',
        rule: `${evalLoss[0]} rose three evaluations in a row (${e.map(fmt).join(' → ')}) while ${train[0]} fell`,
      });
    }
  }

  if (grad && grad[1].ys.length >= MIN_POINTS) {
    const ys = grad[1].ys;
    const latest = ys[ys.length - 1];
    const typical = median(ys.slice(0, -1));
    if (latest > typical * 10) {
      out.push({
        id: 'grad',
        severity: 'warn',
        title: 'Gradient norm exploded',
        explanation:
          'The size of the update jumped by an order of magnitude. Gradient clipping (max_grad_norm) caps the damage; if it keeps happening, lower the learning rate.',
        rule: `latest ${grad[0]} ${fmt(latest)} > 10 × the run's median (${fmt(typical)})`,
      });
    }
  }

  return out;
}
