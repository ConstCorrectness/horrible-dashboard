import { useEffect, useRef, useState } from 'react';

import { subscribeChannel } from '../../../ws';

const dim = { color: 'var(--text-dim)' } as const;

interface ManimState {
  log: string[];
  videoUrl: string | null;
  scene: string | null;
}

/**
 * Manim render viewer: streams `manim_status` progress lines and plays the
 * finished mp4 announced by `manim_done` (served by the project media route).
 * Singleton.
 */
export function ManimPane() {
  const [state, setState] = useState<ManimState>({ log: [], videoUrl: null, scene: null });
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    return subscribeChannel('training', (msg) => {
      if (msg.event === 'manim_status') {
        const { line } = msg.data as { line: string };
        setState((s) => ({ ...s, log: [...s.log.slice(-299), line] }));
      } else if (msg.event === 'manim_done') {
        const { url, scene } = msg.data as { url: string; scene?: string };
        setState((s) => ({ ...s, videoUrl: url, scene: scene ?? null }));
      }
    });
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [state.log]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000',
          minHeight: 120,
        }}
      >
        {state.videoUrl ? (
          <video
            key={state.videoUrl}
            src={state.videoUrl}
            controls
            autoPlay
            loop
            style={{ maxWidth: '100%', maxHeight: '100%' }}
          />
        ) : (
          <span style={{ fontSize: '0.8rem', padding: '1rem', textAlign: 'center', ...dim }}>
            No render yet — ask the agent to “animate the forward pass with manim”, or POST a scene
            to /api/training/projects/&lt;id&gt;/manim.
          </span>
        )}
      </div>
      {state.log.length > 0 && (
        <div
          ref={logRef}
          style={{
            height: 110,
            overflow: 'auto',
            borderTop: '1px solid var(--border)',
            padding: '0.25rem 0.5rem',
            fontFamily: 'var(--font-mono, monospace)',
            fontSize: '0.68rem',
            whiteSpace: 'pre-wrap',
            ...dim,
          }}
        >
          {state.log.join('\n')}
        </div>
      )}
    </div>
  );
}
