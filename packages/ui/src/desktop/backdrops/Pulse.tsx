/**
 * Live node state as the backdrop: every request the node makes or serves
 * arrives as a ripple, coloured by its I/O source and dimmed when it was
 * blocked. The same `telemetryStore` the observability panel reads — the
 * backdrop is a second renderer of it, never a second collector.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';
import { telemetryStore, type IoEvent, type IoSource } from '@horrible/core';

import { useCanvasBackdrop } from './canvas';

const TOKENS = ['bg', 'accent', 'text-dim', 'success', 'warning', 'danger'] as const;

/** Which token colours a ripple, by where the traffic came from. */
const SOURCE_TOKEN: Record<IoSource, string> = {
  client: 'accent',
  inbound: 'success',
  outbound: 'warning',
  ws: 'accent',
  browser: 'text-dim',
};

interface Ripple {
  x: number;
  y: number;
  /** 0 → 1 over `LIFE` seconds; the radius and the fade both read it. */
  age: number;
  token: string;
  blocked: boolean;
}

const LIFE = 2.4;
const MAX_RIPPLES = 120;

export function PulseBackdrop() {
  const events = useSyncExternalStore(telemetryStore.subscribe, telemetryStore.getSnapshot);
  const ripples = useRef<Ripple[]>([]);
  // Events are upserted by (source, id) — the backend re-emits one to fill in a
  // streamed body — so "new" has to mean "a key never seen", not "the array got
  // longer". Keying by index would ripple the whole buffer on every amendment.
  const seen = useRef(new Set<string>());

  useEffect(() => {
    for (const event of events) {
      const key = `${event.source}-${event.id}`;
      if (seen.current.has(key)) continue;
      seen.current.add(key);
      ripples.current.push(rippleFor(event));
    }
    // The store caps its own buffer, and this set outlives it, so trim on the
    // same schedule or it grows without bound over a long session.
    if (seen.current.size > 4000) {
      seen.current = new Set(events.map((e) => `${e.source}-${e.id}`));
    }
    if (ripples.current.length > MAX_RIPPLES) {
      ripples.current = ripples.current.slice(-MAX_RIPPLES);
    }
  }, [events]);

  const ref = useCanvasBackdrop(TOKENS, ({ ctx, width, height, dt, tokens }) => {
    ctx.fillStyle = tokens.bg || '#000';
    ctx.fillRect(0, 0, width, height);

    const live: Ripple[] = [];
    const reach = Math.min(width, height) * 0.28;
    for (const r of ripples.current) {
      r.age += dt / LIFE;
      if (r.age >= 1) continue;
      live.push(r);
      const radius = 4 + r.age * reach;
      ctx.beginPath();
      ctx.arc(r.x * width, r.y * height, radius, 0, Math.PI * 2);
      ctx.strokeStyle = tokens[r.token] || tokens.accent || '#4ea1ff';
      ctx.lineWidth = r.blocked ? 1 : 1.5;
      // Blocked traffic is drawn dashed and faint: it is a thing that did not
      // happen, and painting it as loud as a served request would misreport the
      // node's activity at a glance.
      ctx.setLineDash(r.blocked ? [3, 4] : []);
      ctx.globalAlpha = (1 - r.age) * (r.blocked ? 0.25 : 0.55);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ripples.current = live;
  });

  return <canvas ref={ref} className="os-backdrop-canvas" aria-hidden="true" />;
}

/**
 * Place a ripple from the event's own identity rather than at random, so the
 * same endpoint always pulses in the same spot and the backdrop reads as a map
 * of what this node talks to instead of noise.
 */
function rippleFor(event: IoEvent): Ripple {
  const h = hash(`${event.method} ${event.target}`);
  return {
    x: 0.08 + ((h & 0xffff) / 0xffff) * 0.84,
    y: 0.08 + (((h >>> 16) & 0xffff) / 0xffff) * 0.84,
    age: 0,
    token: SOURCE_TOKEN[event.source] ?? 'accent',
    blocked: event.verdict === 'blocked' || !!event.error,
  };
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
