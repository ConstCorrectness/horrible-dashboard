/**
 * The mode commands and the labels the timeline prints. `/plan <prompt>` has to
 * both switch and leave the prompt intact, and `/agent` must stay the roster
 * command it already was.
 */
import { describe, expect, it } from 'vitest';

import { asAgentMode, nextMode, parseModeCommand } from '../modes';
import { argsSummary, formatDuration, toolGroup, toolLabel } from '../tool-labels';

describe('parseModeCommand', () => {
  it('switches with or without a prompt after it', () => {
    expect(parseModeCommand('/plan')).toEqual({ mode: 'plan', rest: '' });
    expect(parseModeCommand('/plan add a dark-mode toggle')).toEqual({
      mode: 'plan',
      rest: 'add a dark-mode toggle',
    });
    expect(parseModeCommand('/auto  go')).toEqual({ mode: 'autonomous', rest: 'go' });
    expect(parseModeCommand('/act')).toEqual({ mode: 'default', rest: '' });
  });

  it('takes /mode with any label', () => {
    expect(parseModeCommand('/mode agent')).toEqual({ mode: 'default', rest: '' });
    expect(parseModeCommand('/mode Plan then this')).toEqual({ mode: 'plan', rest: 'then this' });
    expect(parseModeCommand('/mode sideways')).toBeNull();
  });

  it('leaves every other command alone', () => {
    expect(parseModeCommand('/agent coder')).toBeNull();
    expect(parseModeCommand('/help')).toBeNull();
    expect(parseModeCommand('plan this')).toBeNull();
  });

  it('cycles Agent → Plan → Auto and reads the Settings-only mode as Agent', () => {
    expect(nextMode('default')).toBe('plan');
    expect(nextMode('plan')).toBe('autonomous');
    expect(nextMode('autonomous')).toBe('default');
    expect(asAgentMode('acceptEdits')).toBe('default');
    expect(asAgentMode(undefined)).toBeNull();
  });
});

describe('tool labels', () => {
  it('names core tools by hand and derives the rest', () => {
    expect(toolLabel('get_pane_context')).toBe('Read pane');
    expect(toolLabel('files.read_file')).toBe('Read file');
    expect(toolLabel('editor.proposeEdit')).toBe('Propose edit');
    expect(toolGroup('files.read_file')).toBe('files');
    expect(toolGroup('show')).toBeNull();
  });

  it('summarises arguments on one line', () => {
    expect(argsSummary({ path: '/a/b.ts', line: 3, flags: ['x'], empty: '' })).toBe(
      'path: /a/b.ts, line: 3, flags: [1]',
    );
    expect(argsSummary({ text: 'x'.repeat(200) }).length).toBeLessThanOrEqual(120);
    expect(argsSummary(null)).toBe('');
  });

  it('prints durations the way the rows read', () => {
    expect(formatDuration(340)).toBe('340ms');
    expect(formatDuration(2400)).toBe('2.4s');
    expect(formatDuration(72_000)).toBe('1m 12s');
  });
});
