import { describe, expect, it } from 'vitest';

import { diagnose, MIN_POINTS, type Series } from '../diagnose';
import { conceptsIn, knobsIn } from '../terms';

const curve = (ys: number[]): Series => ({ xs: ys.map((_, i) => i), ys });
const ids = (m: Map<string, Series>) => diagnose(m).map((d) => d.id);
const falling = (n: number, from = 3, to = 1) =>
  Array.from({ length: n }, (_, i) => from - ((from - to) * i) / (n - 1));

describe('diagnose', () => {
  it('waits for a curve before saying anything about it', () => {
    expect(ids(new Map([['loss', curve([2, 1.9])]]))).toEqual(['waiting']);
  });

  it('reads a falling loss as learning', () => {
    expect(ids(new Map([['loss', curve(falling(40))]]))).toEqual(['falling']);
  });

  it('flags a flat loss', () => {
    expect(ids(new Map([['train/loss', curve(Array(MIN_POINTS * 2).fill(2.3))]]))).toContain(
      'flat',
    );
  });

  it('flags a spike against the recent median', () => {
    const ys = [...falling(30, 2, 1.5), 4];
    expect(ids(new Map([['loss', curve(ys)]]))).toContain('spike');
  });

  it('flags overfitting only when train falls while eval rises', () => {
    const m = new Map([
      ['loss', curve(falling(40))],
      ['eval_loss', curve([1.2, 1.1, 1.15, 1.2, 1.3])],
    ]);
    expect(ids(m)).toContain('overfit');
    m.set('eval_loss', curve([1.3, 1.2, 1.1]));
    expect(ids(m)).not.toContain('overfit');
  });

  it('stops at a NaN, since nothing after it means anything', () => {
    expect(ids(new Map([['loss', curve([...falling(20), NaN])]]))).toEqual(['nan:loss']);
  });

  it('states the rule that fired, with its numbers', () => {
    const [d] = diagnose(new Map([['loss', curve(falling(40))]]));
    expect(d.rule).toMatch(/below the first/);
  });
});

describe('terms', () => {
  const glossary = [
    { name: 'learning_rate', label: 'Learning rate', help: 'How far each step moves.' },
    { name: 'max_length', label: 'Max length', help: 'Truncation.', aliases: ['max_seq_length'] },
    { name: 'lr', label: 'LR', help: 'x' },
  ];

  it('matches knobs by identifier, in cell order, including aliases', () => {
    const src = 'cfg = SFTConfig(max_seq_length=512, learning_rate=2e-4)';
    expect(knobsIn(src, glossary).map((t) => t.name)).toEqual(['max_length', 'learning_rate']);
  });

  it('never matches inside a longer identifier', () => {
    expect(knobsIn('clr_scheduler = 1', glossary)).toEqual([]);
  });

  it('finds the concepts a cell uses', () => {
    const titles = conceptsIn('trainer = SFTTrainer(model, peft_config=LoraConfig(r=8))').map(
      (c) => c.title,
    );
    expect(titles).toContain('Supervised fine-tuning (SFT)');
    expect(titles).toContain('LoRA adapters');
    // `Trainer` inside `SFTTrainer` is not the generic training-loop concept.
    expect(titles).not.toContain('The training loop');
  });
});
