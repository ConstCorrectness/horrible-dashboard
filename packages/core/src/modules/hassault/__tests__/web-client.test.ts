import { describe, expect, it } from 'vitest';

import { webClientUrl } from '../web-client';

describe('webClientUrl', () => {
  it('turns the socket address into the page at the same origin', () => {
    expect(webClientUrl('wss://horrible-games.fly.dev')).toBe('https://horrible-games.fly.dev/');
    expect(webClientUrl('ws://127.0.0.1:9200')).toBe('http://127.0.0.1:9200/');
  });

  it('drops the socket route, which is not the page', () => {
    expect(webClientUrl('wss://horrible-games.fly.dev/ws?token=x')).toBe(
      'https://horrible-games.fly.dev/',
    );
  });

  it('accepts an address that is already a page', () => {
    expect(webClientUrl(' https://games.example.test ')).toBe('https://games.example.test/');
  });

  it('answers null rather than a link to nowhere', () => {
    expect(webClientUrl('')).toBeNull();
    expect(webClientUrl('not a url')).toBeNull();
    expect(webClientUrl('file:///C:/game')).toBeNull();
  });
});
