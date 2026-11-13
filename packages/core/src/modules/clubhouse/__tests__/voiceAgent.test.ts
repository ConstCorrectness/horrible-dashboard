import { describe, expect, it } from 'vitest';

import { DEFAULT_VOICE_CONFIG, chatSpeakerLabel, normalizeVoiceConfig } from '../voiceAgent';

describe('normalizeVoiceConfig', () => {
  it('turns nothing, or nonsense, into the defaults', () => {
    expect(normalizeVoiceConfig(undefined)).toEqual(DEFAULT_VOICE_CONFIG);
    expect(normalizeVoiceConfig(null)).toEqual(DEFAULT_VOICE_CONFIG);
    expect(normalizeVoiceConfig('not an object')).toEqual(DEFAULT_VOICE_CONFIG);
    expect(normalizeVoiceConfig([1, 2])).toEqual(DEFAULT_VOICE_CONFIG);
  });

  it('keeps every field a current config carries', () => {
    const saved = {
      ...DEFAULT_VOICE_CONFIG,
      enabled: true,
      posture: 'always' as const,
      model: 'gemma',
      endpointingDelayMs: 900,
      allowBargeIn: false,
      ttsVolume: '+25%',
    };
    expect(normalizeVoiceConfig(saved)).toEqual(saved);
  });

  it('drops the keys of features that no longer exist', () => {
    const out = normalizeVoiceConfig({
      ...DEFAULT_VOICE_CONFIG,
      wakeWords: ['agent'],
      turnEagerness: 'fast',
      thinkingFiller: true,
      silenceTimeoutS: 30,
      interjectAfterS: 4,
      retrieval: 'auto',
      library: 'default',
    });
    for (const dead of [
      'wakeWords',
      'turnEagerness',
      'thinkingFiller',
      'silenceTimeoutS',
      'interjectAfterS',
      'retrieval',
      'library',
    ]) {
      expect(out).not.toHaveProperty(dead);
    }
  });

  it('turns the interject posture into the nearest one that remains', () => {
    expect(normalizeVoiceConfig({ posture: 'interject' }).posture).toBe('conversational');
    expect(normalizeVoiceConfig({ posture: 'who knows' }).posture).toBe('addressed');
  });

  it('reads an old "turn eagerness" preset as the pause it stood for', () => {
    expect(normalizeVoiceConfig({ turnEagerness: 'fast' }).endpointingDelayMs).toBe(400);
    expect(normalizeVoiceConfig({ turnEagerness: 'patient' }).endpointingDelayMs).toBe(1200);
    // An explicit number wins over the preset it was derived from.
    expect(
      normalizeVoiceConfig({ turnEagerness: 'fast', endpointingDelayMs: 1000 }).endpointingDelayMs,
    ).toBe(1000);
  });

  it('clamps numbers and falls back on junk instead of throwing', () => {
    const out = normalizeVoiceConfig({
      temperature: '',
      maxTokens: 'lots',
      memoryTurns: 9999,
      cooldownS: -5,
      endpointingDelayMs: 50,
    });
    expect(out.temperature).toBe(DEFAULT_VOICE_CONFIG.temperature);
    expect(out.maxTokens).toBe(DEFAULT_VOICE_CONFIG.maxTokens);
    expect(out.memoryTurns).toBe(40);
    expect(out.cooldownS).toBe(0);
    expect(out.endpointingDelayMs).toBe(400);
  });

  it('refuses a wrongly typed boolean or string rather than coercing it', () => {
    const out = normalizeVoiceConfig({ enabled: 'yes', speak: 0, persona: 5, model: '  gemma  ' });
    expect(out.enabled).toBe(false);
    expect(out.speak).toBe(true);
    expect(out.persona).toBe(DEFAULT_VOICE_CONFIG.persona);
    expect(out.model).toBe('gemma');
  });
});

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
