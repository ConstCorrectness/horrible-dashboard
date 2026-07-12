import { useCallback, useEffect, useRef } from 'react';

import type { SocialBubble, SocialOccupant } from '../game-ws';

/**
 * The Plaza floor: a Habbo-style top-down room where each real user is an avatar.
 * Click the floor to walk your avatar there; speech bubbles pop over speakers and
 * fade out. Positions are the server's 0..100 room coordinates; the canvas lerps
 * each avatar toward its target for smooth motion. All rendering is one rAF loop —
 * no per-frame React state — so it stays smooth as occupants/bubbles update.
 */

const BUBBLE_MS = 7000; // how long a speech bubble lingers before it fades out
const PAD = 28; // floor inset from the canvas edge, in px

interface Rendered {
  x: number;
  y: number;
}

export function PlazaCanvas({
  occupants,
  bubbles,
  accountId,
  roomName,
  onMove,
}: {
  occupants: SocialOccupant[];
  bubbles: SocialBubble[];
  accountId: string | null;
  roomName: string;
  onMove: (x: number, y: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Live refs so the rAF loop always reads the latest props without restarting.
  const occRef = useRef<SocialOccupant[]>(occupants);
  const bubRef = useRef<SocialBubble[]>(bubbles);
  const meRef = useRef<string | null>(accountId);
  const roomRef = useRef<string>(roomName);
  const renderedRef = useRef<Map<string, Rendered>>(new Map());
  occRef.current = occupants;
  bubRef.current = bubbles;
  meRef.current = accountId;
  roomRef.current = roomName;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;
    let running = true;

    const cssColor = (name: string, fallback: string) =>
      getComputedStyle(canvas).getPropertyValue(name).trim() || fallback;

    const sizeCanvas = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth || 480;
      const h = canvas.clientHeight || 320;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    sizeCanvas();
    const ro = new ResizeObserver(sizeCanvas);
    ro.observe(canvas);

    const worldToPx = (wx: number, wy: number) => {
      const w = canvas.clientWidth || 480;
      const h = canvas.clientHeight || 320;
      return {
        px: PAD + (wx / 100) * (w - 2 * PAD),
        py: PAD + (wy / 100) * (h - 2 * PAD),
      };
    };

    const roundRect = (x: number, y: number, w: number, h: number, r: number) => {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    };

    const draw = () => {
      if (!running) return;
      const w = canvas.clientWidth || 480;
      const h = canvas.clientHeight || 320;
      const accent = cssColor('--accent', '#6c8cff');
      const text = cssColor('--text', '#e8e8ea');
      const dim = cssColor('--text-dim', '#9aa0a6');
      const panel = cssColor('--panel', '#1c1d21');

      ctx.clearRect(0, 0, w, h);

      // Floor: a soft rounded slab with a subtle checker so motion reads.
      roundRect(PAD - 12, PAD - 12, w - 2 * (PAD - 12), h - 2 * (PAD - 12), 16);
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, withAlpha(accent, 0.1));
      grad.addColorStop(1, withAlpha(accent, 0.03));
      ctx.fillStyle = panel;
      ctx.fill();
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.save();
      ctx.clip();
      ctx.globalAlpha = 0.5;
      for (let gx = 0; gx <= 100; gx += 12.5) {
        for (let gy = 0; gy <= 100; gy += 12.5) {
          if ((Math.round(gx / 12.5) + Math.round(gy / 12.5)) % 2 === 0) continue;
          const a = worldToPx(gx, gy);
          const b = worldToPx(gx + 12.5, gy + 12.5);
          ctx.fillStyle = withAlpha(accent, 0.05);
          ctx.fillRect(a.px, a.py, b.px - a.px, b.py - a.py);
        }
      }
      ctx.restore();

      // Room label, watermark-style in the corner.
      ctx.globalAlpha = 0.5;
      ctx.fillStyle = dim;
      ctx.font = '600 12px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(roomRef.current, PAD, PAD - 6);
      ctx.globalAlpha = 1;

      // Reconcile rendered positions: drop the gone, lerp the rest toward target.
      const rendered = renderedRef.current;
      const present = new Set(occRef.current.map((o) => o.account_id));
      for (const id of [...rendered.keys()]) if (!present.has(id)) rendered.delete(id);
      for (const o of occRef.current) {
        const cur = rendered.get(o.account_id) ?? { x: o.x, y: o.y };
        cur.x += (o.x - cur.x) * 0.18;
        cur.y += (o.y - cur.y) * 0.18;
        rendered.set(o.account_id, cur);
      }

      // Avatars (sorted by y so lower ones overlap correctly).
      const sorted = [...occRef.current].sort((a, b) => a.y - b.y);
      for (const o of sorted) {
        const r = rendered.get(o.account_id)!;
        const { px, py } = worldToPx(r.x, r.y);
        const isMe = o.account_id === meRef.current;

        // Shadow.
        ctx.fillStyle = 'rgba(0,0,0,0.25)';
        ctx.beginPath();
        ctx.ellipse(px, py + 13, 12, 5, 0, 0, Math.PI * 2);
        ctx.fill();

        // "You" ring.
        if (isMe) {
          ctx.strokeStyle = accent;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(px, py, 17, 0, Math.PI * 2);
          ctx.stroke();
        }

        // Avatar emoji.
        ctx.font = '26px system-ui, "Segoe UI Emoji", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(o.avatar, px, py);

        // Name plate.
        ctx.font = '600 11px system-ui, sans-serif';
        const label = o.name;
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = isMe ? withAlpha(accent, 0.9) : 'rgba(0,0,0,0.55)';
        roundRect(px - tw / 2 - 5, py + 18, tw + 10, 15, 7);
        ctx.fill();
        ctx.fillStyle = isMe ? '#fff' : text;
        ctx.fillText(label, px, py + 25);
      }

      // Speech bubbles (fade over their lifetime).
      const now = Date.now();
      for (const bub of bubRef.current) {
        const age = now - bub.ts * 1000;
        if (age < 0 || age > BUBBLE_MS) continue;
        const r = rendered.get(bub.account_id);
        const base = r ? worldToPx(r.x, r.y) : worldToPx(bub.x, bub.y);
        const alpha = age > BUBBLE_MS - 900 ? (BUBBLE_MS - age) / 900 : 1;
        ctx.globalAlpha = Math.max(0, alpha);
        ctx.font = bub.emote
          ? '20px system-ui, "Segoe UI Emoji", sans-serif'
          : '12px system-ui, sans-serif';
        const txt = bub.emote ? bub.text : bub.text;
        const tw = Math.min(180, ctx.measureText(txt).width);
        const bw = tw + 16;
        const bh = 24;
        const bx = base.px - bw / 2;
        const by = base.py - 46;
        ctx.fillStyle = '#fff';
        roundRect(bx, by, bw, bh, 10);
        ctx.fill();
        // little tail
        ctx.beginPath();
        ctx.moveTo(base.px - 5, by + bh);
        ctx.lineTo(base.px + 5, by + bh);
        ctx.lineTo(base.px, by + bh + 7);
        ctx.closePath();
        ctx.fill();
        ctx.fillStyle = '#1c1d21';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(txt, base.px, by + bh / 2, 172);
        ctx.globalAlpha = 1;
      }

      raf = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      const w = canvas.clientWidth || 480;
      const h = canvas.clientHeight || 320;
      const x = ((e.clientX - rect.left - PAD) / (w - 2 * PAD)) * 100;
      const y = ((e.clientY - rect.top - PAD) / (h - 2 * PAD)) * 100;
      onMove(Math.max(0, Math.min(100, x)), Math.max(0, Math.min(100, y)));
    },
    [onMove],
  );

  return (
    <canvas
      ref={canvasRef}
      className="games-plaza-canvas"
      onClick={handleClick}
      title="Click the floor to walk here"
    />
  );
}

/** Turn a CSS color (hex or otherwise) into an rgba() at the given alpha. */
function withAlpha(color: string, alpha: number): string {
  const hex = color.replace('#', '');
  if (/^[0-9a-f]{6}$/i.test(hex)) {
    const n = parseInt(hex, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  if (/^[0-9a-f]{3}$/i.test(hex)) {
    const r = parseInt(hex[0] + hex[0], 16);
    const g = parseInt(hex[1] + hex[1], 16);
    const b = parseInt(hex[2] + hex[2], 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color; // named/other color: use as-is (alpha best-effort)
}
