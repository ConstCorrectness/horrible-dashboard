import { useEffect, useRef } from 'react';

import { acquireAvatar, avatarKey, releaseAvatar, type AvatarMoodMap } from './avatar-scene';

export type { AvatarMoodMap } from './avatar-scene';

/**
 * The avatar's emotional moods → animation clips. To add a mood, drop a .glb in
 * apps/web/public and add a line here; pass `mood` to switch (it cross-fades).
 */
export const DEFAULT_AVATAR_MOODS: AvatarMoodMap = {
  happy: '/dancing.glb',
  flair: '/flair.glb',
  error: '/falling-over.glb',
};

export const DEFAULT_AVATAR_MOOD = 'happy';

/**
 * The dashboard friend: a rigged glTF avatar that plays one of several mood
 * animations (retargeted onto its skeleton) and cross-fades when the mood
 * changes.
 *
 * This component only places a canvas. The scene behind it lives in
 * `avatar-scene.ts` and outlives the component: unmounting parks it, and the next
 * mount adopts it, so reopening a pane that shows the avatar costs nothing. three
 * is dynamically imported there, so views without an avatar never pay for it.
 */
export function Avatar3D({
  size = 180,
  modelUrl = '/my-avatar.glb',
  moods = DEFAULT_AVATAR_MOODS,
  mood = DEFAULT_AVATAR_MOOD,
}: {
  size?: number;
  modelUrl?: string;
  moods?: AvatarMoodMap;
  mood?: string;
}) {
  const mount = useRef<HTMLDivElement>(null);
  // Bridge mood changes into the running scene without tearing it down.
  const desiredMood = useRef(mood);
  const applyMood = useRef<(mood: string) => void>(() => {});

  useEffect(() => {
    desiredMood.current = mood;
    applyMood.current(mood);
  }, [mood]);

  // Compared by content, so a `moods` literal passed inline does not rebuild the
  // scene on every render.
  const moodsRef = useRef(moods);
  moodsRef.current = moods;
  const key = avatarKey({ size, modelUrl, moods, fallbackMood: DEFAULT_AVATAR_MOOD });

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    const opts = { size, modelUrl, moods: moodsRef.current, fallbackMood: DEFAULT_AVATAR_MOOD };
    let mounted = true;
    const avatar = acquireAvatar(opts);
    avatar.then(
      (instance) => {
        if (!mounted) return;
        el.appendChild(instance.canvas);
        applyMood.current = instance.setMood;
        instance.setMood(desiredMood.current);
        instance.start();
      },
      (error) => console.error('Error starting the avatar:', error),
    );
    return () => {
      mounted = false;
      applyMood.current = () => {};
      releaseAvatar(opts, avatar);
    };
    // `key` stands for size, modelUrl and the content of `moods`.
  }, [key]);

  return <div ref={mount} className="avatar3d" style={{ width: size, height: size }} aria-hidden />;
}
