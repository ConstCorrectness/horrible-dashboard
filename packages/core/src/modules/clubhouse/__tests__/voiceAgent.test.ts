import { describe, expect, it } from 'vitest';

import { chatSpeakerLabel } from '../voiceAgent';

describe('chatSpeakerLabel', () => {
  it('names a chat sender with their handle', () => {
    expect(chatSpeakerLabel({ userName: 'Ada Lovelace', username: 'ada' })).toBe(
      'Ada Lovelace (@ada)',
    );
  });

  it('does not double an @ the payload already carries', () => {
    expect(chatSpeakerLabel({ userName: 'Ada', username: '@ada' })).toBe('Ada (@ada)');
  });

  it('falls back to the handle, then the name', () => {
    expect(chatSpeakerLabel({ username: 'ada' })).toBe('@ada');
    expect(chatSpeakerLabel({ userName: 'Ada' })).toBe('Ada');
  });

  it("treats the pane's placeholders as no sender at all", () => {
    expect(chatSpeakerLabel({ userName: 'Anonymous' })).toBeUndefined();
    expect(chatSpeakerLabel({ userName: 'Unknown', username: null })).toBeUndefined();
  });
});
